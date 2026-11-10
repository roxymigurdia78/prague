@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 run.py build %*
set "task_exit_code=%errorlevel%"
pause
exit /b %task_exit_code%
