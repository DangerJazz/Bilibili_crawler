@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 monitor.py %*
set "exit_code=%errorlevel%"
goto done

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python monitor.py %*
set "exit_code=%errorlevel%"
goto done

:no_python
echo Python was not found. Install Python 3.10 or newer and enable Add Python to PATH.
set "exit_code=9009"

:done
echo.
if not "%exit_code%"=="0" echo The monitor did not finish successfully. Exit code: %exit_code%
pause
exit /b %exit_code%
