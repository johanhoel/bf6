@echo off
REM One-shot Windows build. Creates a virtual environment, installs everything,
REM produces dist\BF6Tuner.exe, and opens the folder.
setlocal
cd /d "%~dp0\.."

where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher not found. Install Python 3.11 or newer from python.org
    echo and tick "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist ".venv" (
    echo [1/4] Creating virtual environment...
    py -3 -m venv .venv || goto :failed
) else (
    echo [1/4] Reusing existing virtual environment.
)

echo [2/4] Installing dependencies...
call .venv\Scripts\python.exe -m pip install --upgrade pip --quiet || goto :failed
call .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet || goto :failed

echo [3/4] Running tests...
call .venv\Scripts\python.exe -m pytest tests -q || goto :failed

echo [4/4] Building BF6Tuner.exe...
call .venv\Scripts\python.exe packaging\build.py %* || goto :failed

echo.
echo Build complete: %CD%\dist\BF6Tuner.exe
start "" "%CD%\dist"
exit /b 0

:failed
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
