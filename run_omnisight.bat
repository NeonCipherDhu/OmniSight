@echo off
title OmniSight NVR
cd /d "%~dp0"
start "" pythonw omnisight_app.py
if errorlevel 1 (
    python omnisight_app.py
    pause
)
