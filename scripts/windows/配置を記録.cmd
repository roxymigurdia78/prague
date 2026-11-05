@echo off
cd /d "%~dp0..\.."
python -X utf8 run.py map %*
pause
