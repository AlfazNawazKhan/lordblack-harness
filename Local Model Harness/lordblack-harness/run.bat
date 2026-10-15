@echo off
title LordBlack Harness
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python is required. Install it from https://python.org
    echo Be sure to check "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo First run: creating a private Python environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo Dependency install failed. Fix your internet/Python setup and run again.
        pause
        exit /b 1
    )
    echo ok > .venv\.installed
) else (
    call .venv\Scripts\activate.bat
    if not exist ".venv\.installed" (
        python -m pip install --quiet -r requirements.txt
        if errorlevel 1 (
            echo Dependency install failed. Fix your internet/Python setup and run again.
            pause
            exit /b 1
        )
        echo ok > .venv\.installed
    )
)

python app.py
pause
