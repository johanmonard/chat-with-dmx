@echo off
rem Unattended update of one world: index new and changed files, read scanned pages (OCR), the offers step (offers world only: price pages into the offer register, won / not won, corrections.csv), describe pictures (Ollama, when installed), compute embeddings, then (worlds with access = "acl" only) refresh the per-person access file (permissions).
rem From a cmd prompt: "7 - Update a world (index + embeddings).cmd" marketing   (double-click: it asks)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" update %*
pause
