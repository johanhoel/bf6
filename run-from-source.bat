@echo off
REM ============================================================================
REM  Runs BF6 Tuner directly from source with Python, instead of the built
REM  BF6Tuner.exe.
REM
REM  Why this exists: the compiled .exe bakes in a fresh encryption key on
REM  every single build (see README "About encrypted"), so it is a brand-new,
REM  never-before-seen file every time - Windows Smart App Control / SmartScreen
REM  has no reputation for it and can block it outright, with no override, on
REM  a machine where Smart App Control is enforced. Running from source sidesteps
REM  that entirely: Smart App Control evaluates standalone executables, not
REM  scripts interpreted by an already-trusted python.exe.
REM
REM  Needs Python 3.11+ and the packages in requirements.txt installed
REM  (pip install -r requirements.txt). Double-click this file to launch the
REM  GUI, or run it from a terminal with CLI arguments, e.g.:
REM      run-from-source.bat --preset competitive --print-cfg
REM ============================================================================
setlocal
cd /d "%~dp0"
set PYTHONPATH=%~dp0src

where py >nul 2>nul
if errorlevel 1 (
    python -m bf6tuner %*
) else (
    py -3 -m bf6tuner %*
)

if errorlevel 1 (
    echo.
    echo Could not start. Make sure dependencies are installed:
    echo     pip install -r requirements.txt
    pause
)
