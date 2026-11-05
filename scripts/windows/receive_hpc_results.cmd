@echo off
cd /d "%~dp0..\.."
python -X utf8 -u run.py hpc watch-results
pause
