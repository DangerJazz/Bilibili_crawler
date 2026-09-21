@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

python --version >nul 2>nul
if not errorlevel 1 goto use_python
py -3 --version >nul 2>nul
if not errorlevel 1 goto use_py
goto no_python

:use_python
python monitor.py %*
set "exit_code=%errorlevel%"
goto done

:use_py
py -3 monitor.py %*
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
