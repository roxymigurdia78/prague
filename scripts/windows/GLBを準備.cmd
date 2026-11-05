@echo off
cd /d "%~dp0..\.."
chcp 65001 >nul
if "%~1"=="" (
    echo このファイルにGLBをドラッグしてください。
    pause
    exit /b 1
)
python -X utf8 run.py prepare "%~1"
pause
