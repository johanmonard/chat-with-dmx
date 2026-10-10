@echo off
rem Only if the machine that holds the lock is no longer working on that world's index.
rem Usage: "Unlock after a crash.cmd" [world]   or   "Unlock after a crash.cmd" -World world
set W=%~1
if /i "%W%"=="-World" set W=%~2
if "%W%"=="" set /p W=World (projects, marketing, documentation) [projects]: 
if "%W%"=="" set W=projects
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" unlock -World %W%
choice /m "Remove the lock"
if errorlevel 2 exit /b
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" unlock -World %W% -Force
pause
