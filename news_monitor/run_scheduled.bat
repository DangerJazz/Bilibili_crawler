@echo off
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs

python --version >nul 2>nul
if not errorlevel 1 goto use_python
py -3 --version >nul 2>nul
if not errorlevel 1 goto use_py
goto no_python

:use_python
python monitor.py %* >> logs\latest.log 2>&1
exit /b %errorlevel%

:use_py
py -3 monitor.py %* >> logs\latest.log 2>&1
exit /b %errorlevel%

:no_python
echo Python was not found.>> logs\latest.log
exit /b 9009
