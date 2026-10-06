@echo off
rem Opens the dmx-docs configuration page in the browser. Close this window to stop it.
cd /d "%~dp0.."
".venv\Scripts\dmx-docs.exe" --config config.toml web
pause
