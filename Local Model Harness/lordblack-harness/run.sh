#!/usr/bin/env bash
# LordBlack Harness launcher (Linux/macOS)
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.10+ is required. Install it from https://python.org or your package manager."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "First run: creating a private Python environment..."
    python3 -m venv .venv
    . .venv/bin/activate
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    echo ok > .venv/.installed
else
    . .venv/bin/activate
    if [ ! -f ".venv/.installed" ]; then
        python -m pip install --quiet -r requirements.txt
        echo ok > .venv/.installed
    fi
fi

python app.py
