@echo off
rem Copies the latest index to this machine for Claude Desktop and prints the Claude Desktop configuration.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" pull
pause
