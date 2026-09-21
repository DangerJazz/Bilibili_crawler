@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 monitor.py %*
  set "exit_code=!errorlevel!"
  goto done
)

where python >nul 2>nul
if %errorlevel%==0 (
  python monitor.py %*
  set "exit_code=!errorlevel!"
  goto done
)

echo 未找到 Python。请安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。
set "exit_code=9009"

:done
echo.
if not "%exit_code%"=="0" echo 运行没有完全成功，错误码：%exit_code%
pause
exit /b %exit_code%
