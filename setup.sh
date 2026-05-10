#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "=== Cover Letter Crew - Setup ==="
echo

# 1. Find python3 or python
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "ERROR: Python not found."
    echo "Install Python 3.10+ from https://www.python.org/downloads/"
    echo "or via your package manager: sudo apt install python3"
    exit 1
fi

echo "Using: $($PYTHON --version)"
echo

# 2. Create virtual environment if it does not exist
if [ -f ".venv/bin/activate" ]; then
    echo "Virtual environment already exists - skipping creation."
else
    echo "Creating virtual environment..."
    $PYTHON -m venv .venv || {
        echo
        echo "ERROR: Failed to create virtual environment."
        echo "Try: $PYTHON -m venv .venv"
        exit 1
    }
    echo "Done."
fi

# 3. Activate and install dependencies
echo
echo "Installing dependencies from requirements.txt..."
source .venv/bin/activate
pip install -r requirements.txt || {
    echo
    echo "ERROR: pip install failed. Check the error messages above."
    echo "Common fixes:"
    echo "  - Check your internet connection"
    echo "  - Try: pip install -r requirements.txt --upgrade"
    exit 1
}

# 4. Copy .env.example to .env if .env does not exist
echo
if [ -f ".env" ]; then
    echo ".env already exists - leaving it untouched."
else
    cp .env.example .env
    echo "Created .env from .env.example"
    echo "Open .env and add your API key (or leave blank to use Ollama)."
fi

# 5. Done
echo
echo "=== Setup complete ==="
echo
echo "Next steps:"
echo "  1. Edit .env to add your API key  (skip if using Ollama)"
echo "  2. Run the web UI:  bash Run.sh"
echo "     Or the CLI:      python cover_letter_crew.py"
echo
