@echo off
rem Only if the machine that holds the lock is no longer working on the index.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" unlock
choice /m "Remove the lock"
if errorlevel 2 exit /b
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" unlock -Force
pause
