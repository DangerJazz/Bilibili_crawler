#!/usr/bin/env python3
"""Bilibili official-account dynamic monitor for news workflows.

The program intentionally uses only Python's standard library.  It fetches a
small number of public dynamic pages, normalises useful fields, and writes JSON
snapshots that can be consumed by a daily briefing workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
SCHEMA_VERSION = 1


class MonitorError(RuntimeError):
    """A readable configuration, network, or API error."""


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines without adding a third-party dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise MonitorError(f"JSON 格式错误：{path}（第 {exc.lineno} 行）") from exc


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    users = config.get("users")
    if not isinstance(users, list) or not users:
        raise MonitorError("config.json 的 users 必须是非空列表。")

    normalised_users: list[dict[str, str]] = []
    seen_uids: set[str] = set()
    for index, user in enumerate(users, start=1):
        if not isinstance(user, dict):
            raise MonitorError(f"users 第 {index} 项必须是对象。")
        uid = str(user.get("uid", "")).strip()
        name = str(user.get("name", "")).strip() or uid
        if not uid.isdigit():
            raise MonitorError(f"users 第 {index} 项的 uid 必须是纯数字。")
        if uid not in seen_uids:
            normalised_users.append({"uid": uid, "name": name})
            seen_uids.add(uid)

    options = config.get("options", {})
    if not isinstance(options, dict):
        raise MonitorError("config.json 的 options 必须是对象。")

    def bounded_number(key: str, default: float, minimum: float, maximum: float) -> float:
        try:
            value = float(options.get(key, default))
        except (TypeError, ValueError) as exc:
            raise MonitorError(f"options.{key} 必须是数字。") from exc
        if not minimum <= value <= maximum:
            raise MonitorError(f"options.{key} 必须在 {minimum}～{maximum} 之间。")
        return value

    return {
        "users": normalised_users,
        "options": {
            "lookback_hours": bounded_number("lookback_hours", 48, 1, 24 * 30),
            "max_pages": int(bounded_number("max_pages", 3, 1, 20)),
            "request_interval_seconds": bounded_number(
                "request_interval_seconds", 1.5, 0.5, 30
            ),
            "timeout_seconds": bounded_number("timeout_seconds", 20, 5, 120),
            "retries": int(bounded_number("retries", 3, 1, 6)),
        },
    }


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_major(module_dynamic: dict[str, Any]) -> tuple[str, str, list[str]]:
    major = _as_dict(module_dynamic.get("major"))
    if not major:
        return "", "", []

    kind = str(major.get("type", ""))
    content = _as_dict(
        major.get("opus")
        or major.get("archive")
        or major.get("article")
        or major.get("draw")
        or major.get("common")
        or major.get("live_rcmd")
    )

    summary = _as_dict(content.get("summary"))
    title = _first_text(content.get("title"), summary.get("title"))
    text = _first_text(
        summary.get("text"),
        content.get("desc"),
        content.get("description"),
    )

    if kind == "MAJOR_TYPE_LIVE_RCMD" and content.get("content"):
        try:
            live = json.loads(str(content["content"]))
            live_play = _as_dict(_as_dict(live.get("live_play_info")))
            title = _first_text(title, live_play.get("title"))
            text = _first_text(text, live_play.get("parent_area_name"))
        except json.JSONDecodeError:
            pass

    covers: list[str] = []
    for key in ("cover", "image"):
        value = content.get(key)
        if isinstance(value, str) and value:
            covers.append(value)
    for collection_key in ("pics", "items", "covers"):
        collection = content.get(collection_key)
        if not isinstance(collection, list):
            continue
        for entry in collection:
            if isinstance(entry, str) and entry:
                covers.append(entry)
            elif isinstance(entry, dict):
                url = _first_text(
                    entry.get("url"), entry.get("src"), entry.get("img_src")
                )
                if url:
                    covers.append(url)
    return title, text, list(dict.fromkeys(covers))


def parse_dynamic(item: dict[str, Any]) -> dict[str, Any] | None:
    dynamic_id = str(item.get("id_str") or item.get("id") or "").strip()
    if not dynamic_id:
        return None

    modules = _as_dict(item.get("modules"))
    author = _as_dict(modules.get("module_author"))
    module_dynamic = _as_dict(modules.get("module_dynamic"))
    desc = _as_dict(module_dynamic.get("desc"))
    major_title, major_text, covers = _extract_major(module_dynamic)

    try:
        pub_ts = int(author.get("pub_ts") or item.get("pub_ts") or 0)
    except (TypeError, ValueError):
        pub_ts = 0
    published = datetime.fromtimestamp(pub_ts, SHANGHAI_TZ) if pub_ts else None

    original = None
    original_item = item.get("orig")
    if isinstance(original_item, dict):
        parsed_original = parse_dynamic(original_item)
        if parsed_original:
            original = {
                "dynamic_id": parsed_original["dynamic_id"],
                "author_name": parsed_original["author_name"],
                "title": parsed_original["title"],
                "text": parsed_original["text"],
                "url": parsed_original["url"],
            }

    text = _first_text(desc.get("text"), major_text, major_title)
    return {
        "dynamic_id": dynamic_id,
        "published_at": published.isoformat() if published else None,
        "published_ts": pub_ts or None,
        "type": str(item.get("type", "")),
        "author_uid": str(author.get("mid") or ""),
        "author_name": _first_text(author.get("name")),
        "title": major_title,
        "text": text,
        "url": f"https://www.bilibili.com/opus/{dynamic_id}",
        "cover_urls": covers,
        "is_repost": original is not None,
        "original": original,
    }


def parse_since(value: str | None, now: datetime, lookback_hours: float) -> datetime:
    if not value:
        return now - timedelta(hours=lookback_hours)
    value = value.strip()
    try:
        if len(value) == 5 and value[2] == ":":
            hour, minute = (int(part) for part in value.split(":"))
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
        return parsed.astimezone(SHANGHAI_TZ)
    except (TypeError, ValueError) as exc:
        raise MonitorError(
            "--since 格式应为 HH:MM 或 ISO 时间，例如 08:00、2026-09-21T08:00:00+08:00。"
        ) from exc


def _request_json(
    params: dict[str, str], cookie: str, timeout: float, retries: int
) -> dict[str, Any]:
    url = f"{API_URL}?{urlencode(params)}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://space.bilibili.com/{params['host_mid']}/dynamic",
    }
    if cookie:
        headers["Cookie"] = cookie

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as response:
                raw = response.read()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise MonitorError("B站接口返回了无法识别的数据。")
            code = int(payload.get("code", -1))
            if code != 0:
                messages = {
                    -101: "账号未登录或 Cookie 已失效",
                    -352: "触发了 B站风控（-352）",
                    -412: "请求被 B站拦截（-412）",
                }
                detail = messages.get(code, str(payload.get("message") or "未知错误"))
                raise MonitorError(f"B站接口错误 {code}：{detail}")
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 8) + random.uniform(0, 0.4))
    raise MonitorError(f"请求 B站失败：{last_error}")


def fetch_account(
    account: dict[str, str], options: dict[str, Any], cookie: str, since: datetime
) -> list[dict[str, Any]]:
    offset = ""
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for page in range(options["max_pages"]):
        params = {"host_mid": account["uid"]}
        if offset:
            params.update({"offset": offset})
        payload = _request_json(
            params,
            cookie=cookie,
            timeout=options["timeout_seconds"],
            retries=options["retries"],
        )
        data = _as_dict(payload.get("data"))
        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            raise MonitorError("B站接口响应中没有动态列表。")

        page_times: list[datetime] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            parsed = parse_dynamic(raw_item)
            if not parsed or parsed["dynamic_id"] in seen:
                continue
            seen.add(parsed["dynamic_id"])
            timestamp = parsed.get("published_ts")
            if timestamp:
                published = datetime.fromtimestamp(int(timestamp), SHANGHAI_TZ)
                page_times.append(published)
                if published < since:
                    continue
            collected.append(parsed)

        has_more = bool(data.get("has_more"))
        next_offset = str(data.get("offset") or "")
        if not has_more or not next_offset or next_offset == offset:
            break
        # A pinned dynamic can be much older than its neighbours.  Stop only
        # when the entire page is older than the requested window.
        if page_times and all(published < since for published in page_times):
            break
        offset = next_offset
        if page + 1 < options["max_pages"]:
            time.sleep(options["request_interval_seconds"] + random.uniform(0, 0.35))

    return sorted(
        collected,
        key=lambda entry: int(entry.get("published_ts") or 0),
        reverse=True,
    )


def _load_seen_ids(path: Path) -> dict[str, set[str]]:
    payload = read_json(path, default={})
    if not isinstance(payload, dict):
        return {}
    accounts = payload.get("accounts", {})
    if not isinstance(accounts, dict):
        return {}
    result: dict[str, set[str]] = {}
    for uid, ids in accounts.items():
        if isinstance(ids, list):
            result[str(uid)] = {str(item) for item in ids}
    return result


def _merge_daily_snapshot(existing: Any, current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(existing, dict):
        return current
    previous_accounts = {
        str(account.get("uid")): account
        for account in existing.get("accounts", [])
        if isinstance(account, dict) and account.get("uid")
    }
    merged_accounts: list[dict[str, Any]] = []
    for account in current["accounts"]:
        uid = account["uid"]
        previous = previous_accounts.get(uid, {})
        combined = {
            str(item.get("dynamic_id")): item
            for item in previous.get("items", [])
            if isinstance(item, dict) and item.get("dynamic_id")
        }
        combined.update(
            {
                str(item["dynamic_id"]): item
                for item in account.get("items", [])
                if item.get("dynamic_id")
            }
        )
        merged = dict(account)
        merged["items"] = sorted(
            combined.values(),
            key=lambda entry: int(entry.get("published_ts") or 0),
            reverse=True,
        )
        merged["item_count"] = len(merged["items"])
        merged_accounts.append(merged)
    result = dict(current)
    result["accounts"] = merged_accounts
    return result


def build_report(
    config: dict[str, Any], since: datetime, now: datetime, cookie: str
) -> tuple[dict[str, Any], dict[str, set[str]], bool]:
    accounts: list[dict[str, Any]] = []
    successful_ids: dict[str, set[str]] = {}
    any_success = False
    for account in config["users"]:
        try:
            items = fetch_account(account, config["options"], cookie, since)
            accounts.append(
                {
                    "uid": account["uid"],
                    "name": account["name"],
                    "status": "ok",
                    "error": None,
                    "item_count": len(items),
                    "items": items,
                }
            )
            successful_ids[account["uid"]] = {
                str(item["dynamic_id"]) for item in items
            }
            any_success = True
        except MonitorError as exc:
            accounts.append(
                {
                    "uid": account["uid"],
                    "name": account["name"],
                    "status": "error",
                    "error": str(exc),
                    "item_count": 0,
                    "items": [],
                }
            )

    report = {
        "schema_version": SCHEMA_VERSION,
        "checked_at": now.isoformat(),
        "timezone": "Asia/Shanghai",
        "since": since.isoformat(),
        "accounts": accounts,
    }
    return report, successful_ids, any_success


def mark_new_items(report: dict[str, Any], seen_ids: dict[str, set[str]]) -> int:
    total = 0
    for account in report["accounts"]:
        previous = seen_ids.get(account["uid"], set())
        new_count = 0
        for item in account["items"]:
            item["is_new"] = item["dynamic_id"] not in previous
            if item["is_new"]:
                new_count += 1
        account["new_item_count"] = new_count
        total += new_count
    report["new_item_count"] = total
    return total


def save_results(
    report: dict[str, Any], output_dir: Path, fresh_ids: dict[str, set[str]]
) -> None:
    state_path = output_dir / "state.json"
    seen_ids = _load_seen_ids(state_path)
    mark_new_items(report, seen_ids)

    write_json_atomic(output_dir / "latest.json", report)
    date_name = datetime.fromisoformat(report["checked_at"]).date().isoformat() + ".json"
    daily_path = output_dir / date_name
    daily = _merge_daily_snapshot(read_json(daily_path, default={}), report)
    write_json_atomic(daily_path, daily)

    for uid, ids in fresh_ids.items():
        seen_ids.setdefault(uid, set()).update(ids)
        # Keep state bounded while retaining enough history for de-duplication.
        if len(seen_ids[uid]) > 2000:
            seen_ids[uid] = set(sorted(seen_ids[uid])[-2000:])
    state = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": report["checked_at"],
        "accounts": {uid: sorted(ids) for uid, ids in seen_ids.items()},
    }
    write_json_atomic(state_path, state)


def print_summary(report: dict[str, Any]) -> None:
    print(f"检查时间：{report['checked_at']}")
    print(f"筛选起点：{report['since']}")
    for account in report["accounts"]:
        if account["status"] == "ok":
            print(
                f"[成功] {account['name']}（{account['uid']}）："
                f"{account['item_count']} 条范围内动态，"
                f"{account.get('new_item_count', 0)} 条首次发现"
            )
        else:
            print(f"[失败] {account['name']}（{account['uid']}）：{account['error']}")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="抓取指定 B站账号的近期公开动态并输出 JSON。")
    parser.add_argument(
        "--config", default="config.json", help="配置文件路径（默认：config.json）"
    )
    parser.add_argument(
        "--output", default="output", help="输出目录（默认：output）"
    )
    parser.add_argument(
        "--since", help="只保留该时间后的动态；支持 HH:MM 或 ISO 时间"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="执行抓取但不写入任何文件"
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    base_dir = Path(__file__).resolve().parent
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = base_dir / config_path
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = base_dir / output_dir

    try:
        load_env_file(base_dir / ".env")
        raw_config = read_json(config_path)
        if not isinstance(raw_config, dict):
            raise MonitorError(f"找不到或无法读取配置文件：{config_path}")
        config = validate_config(raw_config)
        now = datetime.now(SHANGHAI_TZ)
        since = parse_since(
            args.since, now=now, lookback_hours=config["options"]["lookback_hours"]
        )
        if since > now:
            raise MonitorError("--since 不能晚于当前时间。")
        cookie = os.environ.get("BILI_COOKIE", "").strip()
        report, fresh_ids, any_success = build_report(config, since, now, cookie)
        if args.dry_run:
            mark_new_items(report, {})
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            save_results(report, output_dir, fresh_ids)
            print_summary(report)
            print(f"结果已保存：{output_dir / 'latest.json'}")
        return 0 if any_success else 1
    except MonitorError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
