@echo off
title V380 Pro Windows Studio
cd /d "%~dp0"
start "" pythonw v380_app.py
if errorlevel 1 (
    python v380_app.py
    pause
)
