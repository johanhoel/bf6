@echo off
REM ============================================================================
REM  Runs BF6 Tuner directly from source with Python, instead of the built
REM  BF6Tuner.exe.
REM
REM  Why this exists: every real code change produces a brand-new, never-
REM  before-seen compiled .exe (see README "About the database" / "Getting the
REM  executable") - Windows Smart App Control / SmartScreen has no reputation
REM  for it and can block it outright, with no override, on a machine where
REM  Smart App Control is enforced. Running from source sidesteps that
REM  entirely: Smart App Control evaluates standalone executables, not
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
