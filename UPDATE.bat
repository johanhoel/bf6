@echo off
REM ============================================================================
REM  One-shot: get the latest changes, publish them, and build a fresh
REM  BF6Tuner.exe right here in this folder.
REM
REM  Double-click it. First run takes a few minutes (it sets up Python
REM  dependencies); after that it is about a minute.
REM ============================================================================
setlocal
cd /d "%~dp0"

set STAGING=https://github.com/johanhoel/pipeline-tracker.git
set STAGING_BRANCH=bf6-standalone

where git >nul 2>nul
if errorlevel 1 (
    echo Git is not installed. Get it from https://git-scm.com/download/win
    pause & exit /b 1
)

echo.
echo [1/4] Fetching the latest changes...
git pull --no-rebase "%STAGING%" %STAGING_BRANCH%
if errorlevel 1 (
    echo.
    echo The pull did not complete cleanly - most likely a merge conflict
    echo because you edited files here too. Resolve it, then run this again.
    pause & exit /b 1
)

echo.
echo [2/4] Publishing to your own repository...
git push origin main
if errorlevel 1 (
    echo   ^(push failed - carrying on with the local build anyway^)
)

echo.
echo [3/4] Building the executables...
where py >nul 2>nul
if errorlevel 1 (
    echo.
    echo Python is not installed, so the build cannot run locally.
    echo Either install Python 3.11+ from https://www.python.org/downloads/
    echo ^(tick "Add python.exe to PATH"^), or download the exe from:
    echo     https://github.com/johanhoel/bf6/actions
    pause & exit /b 1
)

if not exist ".venv" (
    py -3 -m venv .venv || goto :failed
)
call .venv\Scripts\python.exe -m pip install --upgrade pip --quiet || goto :failed
call .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet || goto :failed
call .venv\Scripts\python.exe -m pytest tests -q || goto :failed
call .venv\Scripts\python.exe packaging\build.py || goto :failed

echo.
echo [4/4] Putting the executables in this folder...
copy /Y "dist\BF6Tuner.exe" "BF6Tuner.exe" >nul || goto :failed
copy /Y "dist\BF6Tuner-cli.exe" "BF6Tuner-cli.exe" >nul || goto :failed

echo.
echo ============================================================================
echo  Done.
echo.
echo    %CD%\BF6Tuner.exe        ^<- double-click this to run the app
echo    %CD%\BF6Tuner-cli.exe    ^<- command-line version
echo ============================================================================
echo.
pause
exit /b 0

:failed
echo.
echo BUILD FAILED - see the messages above.
echo You can still download a working exe from https://github.com/johanhoel/bf6/actions
pause
exit /b 1
