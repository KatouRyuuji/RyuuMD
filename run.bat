@echo off
chcp 936 >nul
cd /d "%~dp0"
python main.py %*
if errorlevel 1 pause
