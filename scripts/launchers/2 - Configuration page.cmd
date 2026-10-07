@echo off
echo Configuration page: folders to index, exclusions, scans.
echo STOP IT WITH CTRL+C IN THIS WINDOW (not the X), so the index is saved back to the shared folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" web
pause
