@echo off
setlocal
cd /d "%~dp0"
if not exist logs mkdir logs

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 monitor.py %* >> logs\latest.log 2>&1
exit /b %errorlevel%

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python monitor.py %* >> logs\latest.log 2>&1
exit /b %errorlevel%

:no_python
echo Python was not found.>> logs\latest.log
exit /b 9009
