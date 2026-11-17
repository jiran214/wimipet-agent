@echo off
cd /d "%~dp0"
python apps\desktop\server.py %*
if errorlevel 1 pause
