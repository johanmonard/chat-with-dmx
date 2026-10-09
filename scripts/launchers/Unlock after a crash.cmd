@echo off
rem Only if the machine that holds the lock is no longer working on that world's index.
set W=%~1
if "%W%"=="" set /p W=World (projects, marketing) [projects]: 
if "%W%"=="" set W=projects
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" unlock -World %W%
choice /m "Remove the lock"
if errorlevel 2 exit /b
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" unlock -World %W% -Force
pause
