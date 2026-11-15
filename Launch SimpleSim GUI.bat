@echo off
rem Double-click to open the SimpleSim demo GUI in your browser.
cd /d "%~dp0"
python launch_gui.py %*
if errorlevel 1 pause
