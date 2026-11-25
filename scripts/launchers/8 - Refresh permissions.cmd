@echo off
rem Unattended refresh of the per-person access files (access.sqlite3) of every world that has access = "acl" in config.toml: reads the file server's reading rule of each indexed folder. No prompt, no lock. The exit code is that of the first world that failed (0 when all went well). Needs a local copy of each world (pull or an index run first).
rem This is what the VM's daily scheduled task runs (see the README for the schtasks line). Run it under the account that may read the folders' permissions.
setlocal enabledelayedexpansion
set RC=0
for /f "usebackq delims=" %%W in (`powershell -NoProfile -Command "$t = Get-Content '%~dp0config.toml' -Raw; [regex]::Matches($t, '(?ms)^\s*\[worlds\.([a-z0-9_-]+)\](.*?)(?=^\s*\[|\z)', 'IgnoreCase') | Where-Object { $_.Groups[2].Value -match '(?m)^\s*access\s*=\s*[\x22\x27]acl[\x22\x27]' } | ForEach-Object { $_.Groups[1].Value }"`) do (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" permissions -World %%W
    if errorlevel 1 if !RC! equ 0 set RC=!errorlevel!
)
exit /b !RC!
