@echo off
rem Nightly update: index new/changed files, then embed them (5 hours max).
cd /d "%~dp0.."
".venv\Scripts\dmx-docs.exe" --config config.toml index
".venv\Scripts\dmx-docs.exe" --config config.toml embed --max-minutes 300
