@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || goto :no_python
)

".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :failed

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env. Add your bot token and server ID, then run this again.
    goto :failed
)

:run
".venv\Scripts\python.exe" -m marketmule
echo.
echo Bot stopped. Restarting in 5 seconds. Close this window to quit.
timeout /t 5 /nobreak >nul
goto :run

:no_python
echo Python 3.10 or newer is required: https://www.python.org/downloads/

:failed
pause
exit /b 1
