@echo off
cd /d "%~dp0..\.."
chcp 65001 >nul
".venv\Scripts\python.exe" -X utf8 run.py build %*
pause
