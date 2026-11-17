@echo off
cd /d "%~dp0"
python -m silence_cut gui %*
if errorlevel 1 pause
