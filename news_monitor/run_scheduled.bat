@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
if not exist logs mkdir logs

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 monitor.py %* >> logs\latest.log 2>&1
  exit /b !errorlevel!
)

where python >nul 2>nul
if %errorlevel%==0 (
  python monitor.py %* >> logs\latest.log 2>&1
  exit /b !errorlevel!
)

echo 未找到 Python。>> logs\latest.log
exit /b 9009
