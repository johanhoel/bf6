@echo off
REM One-shot: push this project to https://github.com/johanhoel/bf6
REM Run it from the folder this file sits in. Nothing else to set up.
setlocal
cd /d "%~dp0"

set REPO=https://github.com/johanhoel/bf6.git

where git >nul 2>nul
if errorlevel 1 (
    echo Git is not installed. Get it from https://git-scm.com/download/win
    pause
    exit /b 1
)

if not exist ".git" (
    echo No git history found in this folder - initialising a fresh repository.
    echo (Your unzip tool probably skipped the hidden .git folder. The code is
    echo  all here; only the commit history is lost.^)
    git init -b main || goto :failed
    git add -A || goto :failed
    git -c user.name="Johan Hoel" -c user.email="johan.hoel@netgear.com" ^
        commit -m "Add BF6 Tuner: hardware-aware Battlefield 6 settings configurator" || goto :failed
)

git remote remove origin >nul 2>nul
git remote add origin %REPO% || goto :failed

echo.
echo Pushing to %REPO% ...
git push -u origin main
if errorlevel 1 goto :pushfailed

echo.
echo Done. Your code is at https://github.com/johanhoel/bf6
echo A Windows build starts automatically - the .exe appears under Actions in a couple of minutes.
start "" "https://github.com/johanhoel/bf6/actions"
exit /b 0

:pushfailed
echo.
echo The push was rejected. The usual causes:
echo.
echo   * The repo already has commits. To replace them:
echo         git push -u --force origin main
echo   * You are not signed in. Git will normally open a browser to authenticate;
echo     if it did not, run:  git credential-manager configure
echo   * The repo does not exist yet. Create it (empty, no README^) at:
echo         https://github.com/new
echo.
pause
exit /b 1

:failed
echo.
echo Something went wrong above.
pause
exit /b 1
