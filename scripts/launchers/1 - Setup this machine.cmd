@echo off
rem Builds C:\dmx-rag\venv on this machine (first time, or after a code update).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" setup
pause
