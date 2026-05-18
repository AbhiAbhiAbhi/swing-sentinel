@echo off
REM Git Push Helper - Windows batch wrapper
REM Usage: gpush [--strategy bundle|split] [--message "msg"] [--dry-run] [--no-prompt]

setlocal enabledelayedexpansion

REM Get the directory where this batch file is located
set SCRIPT_DIR=%~dp0
set PYTHON_SCRIPT=%SCRIPT_DIR%git_push_helper.py

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    exit /b 1
)

REM Check if the script exists
if not exist "!PYTHON_SCRIPT!" (
    echo Error: git_push_helper.py not found in %SCRIPT_DIR%
    exit /b 1
)

REM Run the Python script with all arguments passed through
python "!PYTHON_SCRIPT!" %*
exit /b %errorlevel%
