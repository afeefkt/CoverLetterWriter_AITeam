@echo off
cd /d %~dp0
call .venv\Scripts\activate
python cover_letter_crew.py
pause