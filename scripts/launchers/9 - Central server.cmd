@echo off
rem The central server (the VM): every world with access = "acl" at http(s)://<this machine>:8765/<world>/mcp, signed in with Windows (NTLM). Runs until it is stopped; no prompt, no lock. Needs a local copy of each world (launcher 5 or an index run first).
rem This is what the VM's "at startup" scheduled task runs (see the README for the schtasks line). Options go through, e.g. --worlds projects.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" serve-all %*
exit /b %errorlevel%
