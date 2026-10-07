@echo off
rem Search by meaning. Uses the GPU when there is one. Ctrl+C stops it; the next run continues.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" embed %*
pause
