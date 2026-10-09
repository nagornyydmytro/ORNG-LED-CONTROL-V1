@echo off
title ORNG LED venue start
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\venue-start.ps1"
if errorlevel 1 pause
