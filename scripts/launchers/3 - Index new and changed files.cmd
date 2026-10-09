@echo off
rem Scans the root folders of a world for new, changed and deleted files. Ctrl+C stops it; the next run continues.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" index %*
pause
