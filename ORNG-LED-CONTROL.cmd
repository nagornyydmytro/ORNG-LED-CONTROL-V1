@echo off
title ORNG LED CONTROL
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\venue-start.ps1"
if errorlevel 1 pause
