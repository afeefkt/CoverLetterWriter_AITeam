@echo off
:: Cover Letter Crew — diagnostic runner
:: Usage:
::   diagnose.bat               quick check (steps 1-12, ~2 min on local AI)
::   diagnose.bat --offline     offline only (steps 1-3, no Ollama needed)
::   diagnose.bat --full        full pipeline including 8-task end-to-end run (slow!)
::   diagnose.bat --model qwen3:8b
::   diagnose.bat --url http://localhost:11434

setlocal

set "SCRIPT_DIR=%~dp0"
set "VENV=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV%" (
    echo ERROR: venv not found at %VENV%
    echo Run: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

"%VENV%" "%SCRIPT_DIR%diagnose.py" %*
exit /b %ERRORLEVEL%
