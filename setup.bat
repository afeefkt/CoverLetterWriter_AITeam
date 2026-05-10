@echo off
setlocal

cd /d "%~dp0"

echo === Cover Letter Crew - Setup ===
echo.

:: 1. Check Python is available
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python not found on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: 2. Create virtual environment if it does not exist
if exist ".venv\Scripts\activate.bat" (
    echo Virtual environment already exists - skipping creation.
) else (
    echo Creating virtual environment...
    python -m venv .venv
    if %ERRORLEVEL% neq 0 (
        echo ERROR: Failed to create virtual environment.
        echo Try running: python -m venv .venv
        echo.
        pause
        exit /b 1
    )
    echo Done.
)

:: 3. Activate and install dependencies
echo.
echo Installing dependencies from requirements.txt...
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo ERROR: pip install failed. Check the error messages above.
    echo Common fixes:
    echo   - Make sure you have a working internet connection
    echo   - Try: pip install -r requirements.txt --upgrade
    echo.
    pause
    exit /b 1
)

:: 4. Copy .env.example to .env if .env does not exist
echo.
if exist ".env" (
    echo .env already exists - leaving it untouched.
) else (
    copy ".env.example" ".env" >nul
    echo Created .env from .env.example
    echo Open .env and add your API key ^(or leave blank to use Ollama^).
)

:: 5. Done
echo.
echo === Setup complete ===
echo.
echo Next steps:
echo   1. Edit .env to add your API key  ^(skip if using Ollama^)
echo   2. Run the web UI:   double-click Run_Streamlit.bat
echo      Or the CLI:       double-click Run.bat
echo.
pause
