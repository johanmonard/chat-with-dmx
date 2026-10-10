@echo off
rem Unattended update of one world: index new and changed files, read scanned pages (OCR), then compute embeddings.
rem From a cmd prompt: "7 - Update a world (index + embeddings).cmd" marketing   (double-click: it asks)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" update %*
pause
