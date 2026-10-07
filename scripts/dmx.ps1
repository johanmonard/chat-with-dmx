<#
dmx-docs on several machines that share one network folder (e.g. U:\DMX-RAG).

The shared folder holds the code, the settings and the master copy of the index.
SQLite must not run on a network share, so every command that changes the index
works on a local copy:  lock -> copy master to C:\dmx-rag -> run -> copy back -> unlock.
Only one machine can hold the lock, so runs are sequential.

Shared folder layout (this script lives in <shared>\app\scripts):
  <shared>\app\           git clone of chat-with-dmx
  <shared>\config.toml    settings used by every machine (data_dir = 'C:\dmx-rag\data')
  <shared>\data\          index.sqlite3 (master), index.prev.sqlite3 (previous), LOCK
  <shared>\models\        embedding model, copied to each machine once
  <shared>\tools\uv.exe   builds the local Python environment (no admin rights needed)
  <shared>\logs\          one log per run

Commands:
  setup          build/update C:\dmx-rag\venv (GPU packages on NVIDIA machines)
  web            configuration page (folders, exclusions, scans), checked in when closed
  index          scan for new/changed/deleted files
  embed [args]   compute embeddings, e.g. embed --max-minutes 300
  pull           refresh this machine's read-only copy for Claude Desktop (no lock)
  status         index statistics of the local copy
  search "..."   test a search on the local copy
  unlock -Force  remove a stale lock left by a machine that crashed
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('setup', 'web', 'index', 'embed', 'pull', 'status', 'search', 'unlock')]
    [string]$Command,
    [switch]$Force,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
$App = Split-Path $PSScriptRoot -Parent
$Shared = Split-Path $App -Parent
$SharedConfig = Join-Path $Shared 'config.toml'
$SharedData = Join-Path $Shared 'data'
$SharedModels = Join-Path $Shared 'models'
$Logs = Join-Path $Shared 'logs'
$Uv = Join-Path $Shared 'tools\uv.exe'
$Master = Join-Path $SharedData 'index.sqlite3'
$Lock = Join-Path $SharedData 'LOCK'

$Local = 'C:\dmx-rag'
$LocalData = Join-Path $Local 'data'
$LocalDb = Join-Path $LocalData 'index.sqlite3'
$LocalModels = Join-Path $LocalData 'models'
$LocalConfig = Join-Path $Local 'config.toml'
$Venv = Join-Path $Local 'venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$Stamp = Join-Path $Local 'installed.txt'

# Keep uv's Python and cache on the local disk, not in a roaming profile.
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Local 'python'
$env:UV_CACHE_DIR = Join-Path $Local 'uv-cache'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'   # live progress through the pipe
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Say($msg) { Write-Host "[dmx] $msg" -ForegroundColor Cyan }

function Invoke-Native([string]$exe, [string[]]$arguments, [switch]$AllowFail) {
    # Output goes through the pipeline so it also lands in the log (Start-Transcript only
    # records what PowerShell writes). Windows PowerShell 5.1 turns redirected stderr lines
    # into errors, hence 'Continue' and the unwrapping.
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $exe @arguments 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
        } | Out-Host
    } finally { $ErrorActionPreference = $eap }
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) { throw "$([IO.Path]::GetFileName($exe)) exited with code $LASTEXITCODE" }
}

function Get-CodeStamp {
    $files = @(Get-Item (Join-Path $App 'pyproject.toml')) + @(Get-ChildItem (Join-Path $App 'src') -Recurse -File)
    $latest = ($files | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum
    "$($latest.Ticks)|gpu=$(Test-Gpu)"
}

function Test-Gpu { [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue) }

function Install-Env {
    if (-not (Test-Path $Uv)) { throw "uv.exe not found in $(Split-Path $Uv)" }
    New-Item -ItemType Directory -Force $Local, $LocalData | Out-Null
    if (-not (Test-Path $Py)) {
        Say "Creating the Python 3.12 environment in $Venv ..."
        Invoke-Native $Uv @('venv', $Venv, '--python', '3.12')
    }
    Say "Installing dmx-docs from $App ..."
    Invoke-Native $Uv @('pip', 'install', '--python', $Py, '--reinstall-package', 'dmx-docs', $App)
    if (Test-Gpu) {
        # onnxruntime-gpu >= 1.28 is built for CUDA 13 (driver R580+); 1.26 is the last CUDA 12 build.
        $smi = (nvidia-smi | Out-String)
        $cuda = if ($smi -match 'CUDA Version:\s*(\d+)') { [int]$Matches[1] } else { 0 }
        $ort = switch ($cuda) { { $_ -ge 13 } { 'onnxruntime-gpu[cuda,cudnn]' } 12 { 'onnxruntime-gpu[cuda,cudnn]==1.26.*' } default { $null } }
        if ($ort) {
            Say "NVIDIA GPU found (driver supports CUDA $cuda): installing $ort ..."
            Invoke-Native $Uv @('pip', 'uninstall', '--python', $Py, 'onnxruntime', 'fastembed') -AllowFail
            Invoke-Native $Uv @('pip', 'install', '--python', $Py, 'fastembed-gpu', $ort)
        } else {
            Say "NVIDIA GPU found but its driver is too old for CUDA 12 (nvidia-smi says CUDA $cuda): embeddings will use the CPU."
        }
    }
    Get-CodeStamp | Set-Content -Encoding ascii $Stamp
    $hasWord = Test-Path 'Registry::HKEY_CLASSES_ROOT\Word.Application\CurVer'
    $hasLo = (Test-Path 'C:\Program Files\LibreOffice\program\soffice.exe') -or (Test-Path 'C:\Program Files (x86)\LibreOffice\program\soffice.exe')
    if (-not ($hasWord -or $hasLo)) { Say 'Note: neither Word nor LibreOffice is installed here, so .doc files are skipped if you index from this machine.' }
    Say 'Environment ready.'
}

function Assert-Env {
    if (-not (Test-Path $Py) -or -not (Test-Path $Stamp) -or (Get-Content $Stamp) -ne (Get-CodeStamp)) { Install-Env }
}

function Sync-Models {
    # The model is 2.2 GB: copy it from the share once instead of downloading it on every machine.
    if ((Test-Path $SharedModels) -and -not (Test-Path (Join-Path $LocalModels '*'))) {
        Say 'Copying the embedding model from the shared folder (first time on this machine) ...'
        robocopy $SharedModels $LocalModels /E /NFL /NDL /NJH /NP | Out-Null
    }
}

function Publish-Models {
    if (-not (Test-Path (Join-Path $SharedModels '*')) -and (Test-Path (Join-Path $LocalModels '*'))) {
        Say 'Saving the embedding model to the shared folder for the other machines ...'
        robocopy $LocalModels $SharedModels /E /NFL /NDL /NJH /NP | Out-Null
    }
}

function Get-LockInfo { if (Test-Path $Lock) { (Get-Content $Lock -Raw).Trim() } }

function Enter-Lock([string]$what) {
    New-Item -ItemType Directory -Force $SharedData | Out-Null
    $info = "$env:COMPUTERNAME|$env:USERNAME|$(Get-Date -Format 'yyyy-MM-dd HH:mm')|$what"
    try {
        $fs = [IO.File]::Open($Lock, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        $bytes = [Text.Encoding]::ASCII.GetBytes($info)
        $fs.Write($bytes, 0, $bytes.Length); $fs.Close()
        return $false   # fresh lock: start from the master copy
    } catch [IO.IOException] {
        $held = Get-LockInfo
        if ($held -and $held.Split('|')[0] -eq $env:COMPUTERNAME) {
            Say "This machine already holds the lock ($held): continuing with its local copy, which has unsaved work."
            return $true
        }
        throw "The index is in use by another machine: $held`nWait for it to finish. If that machine crashed, run: dmx.ps1 unlock -Force"
    }
}

function Exit-Lock { Remove-Item $Lock -Force -ErrorAction SilentlyContinue }

function Copy-MasterToLocal {
    New-Item -ItemType Directory -Force $LocalData | Out-Null
    Remove-Item "$LocalDb-wal", "$LocalDb-shm" -Force -ErrorAction SilentlyContinue
    if (Test-Path $Master) {
        Say 'Copying the index from the shared folder ...'
        Copy-Item $Master $LocalDb -Force
    } else {
        Say 'No index in the shared folder yet: starting a new one.'
        Remove-Item $LocalDb -Force -ErrorAction SilentlyContinue
    }
}

function Save-LocalToMaster {
    if (-not (Test-Path $LocalDb)) { return $true }
    Say 'Checking the local index ...'
    $check = & $Py -c @"
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
print(con.execute('PRAGMA quick_check').fetchone()[0])
con.close()
"@ $LocalDb
    if ($check -ne 'ok') {
        Say "The local index failed its integrity check ($check). It was NOT copied to the shared folder; the lock is kept."
        return $false
    }
    Say 'Copying the index back to the shared folder ...'
    $tmp = "$Master.tmp"
    Copy-Item $LocalDb $tmp -Force
    if (Test-Path $Master) { Move-Item $Master (Join-Path $SharedData 'index.prev.sqlite3') -Force }
    Move-Item $tmp $Master -Force
    Copy-Item $SharedConfig $LocalConfig -Force
    return $true
}

function Invoke-Locked([string]$what, [string[]]$dmxArgs) {
    Assert-Env
    Sync-Models
    $resumed = Enter-Lock $what
    $ok = $false
    try {
        if (-not $resumed -or -not (Test-Path $LocalDb)) { Copy-MasterToLocal }
        Say "Running: dmx-docs $($dmxArgs -join ' ')"
        Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $dmxArgs) -AllowFail
        Say "dmx-docs finished (exit code $LASTEXITCODE)."
        $ok = $true
    } finally {
        # Also runs after Ctrl+C: what was done so far is kept (the index is resumable).
        if (Save-LocalToMaster) { Exit-Lock; Say 'Done. The index in the shared folder is up to date and unlocked.' }
        if ($ok -and $what -eq 'embed') { Publish-Models }
    }
}

New-Item -ItemType Directory -Force $Logs | Out-Null
$log = Join-Path $Logs ("{0}_{1}_{2}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $env:COMPUTERNAME, $Command)
Start-Transcript -Path $log -Append | Out-Null
try {
    if (-not (Test-Path $SharedConfig)) { throw "Settings not found: $SharedConfig" }
    switch ($Command) {
        'setup' { Install-Env; Sync-Models }
        'web' { Invoke-Locked 'web' (@('web') + $Rest) }
        'index' { Invoke-Locked 'index' (@('index') + $Rest) }
        'embed' { Invoke-Locked 'embed' (@('embed') + $Rest) }
        'pull' {
            Assert-Env
            Sync-Models
            $held = Get-LockInfo
            if ($held -and $held.Split('|')[0] -eq $env:COMPUTERNAME) { throw "This machine holds the lock with unsaved work ($held): run the interrupted command again first." }
            if ($held) { Say "Note: $($held.Split('|')[0]) is working on the index right now; you get the last saved version." }
            Copy-MasterToLocal
            Copy-Item $SharedConfig $LocalConfig -Force
            Invoke-Native $Py @('-m', 'dmx_docs.cli', '--config', $LocalConfig, 'status') -AllowFail
            $snippet = @{ mcpServers = @{ 'dmx-docs' = @{ command = $Py; args = @('-m', 'dmx_docs.cli', '--config', $LocalConfig, 'serve') } } } | ConvertTo-Json -Depth 5
            Say "Claude Desktop configuration for this machine:`n$snippet"
        }
        'status' { Assert-Env; Invoke-Native $Py @('-m', 'dmx_docs.cli', '--config', $SharedConfig, 'status') -AllowFail }
        'search' { Assert-Env; Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig, 'search') + $Rest) -AllowFail }
        'unlock' {
            $held = Get-LockInfo
            if (-not $held) { Say 'Not locked.' }
            elseif (-not $Force) { Say "Locked by: $held`nRun again with -Force to remove the lock (only if that machine is no longer working on it)." }
            else { Exit-Lock; Say "Lock removed (was: $held)." }
        }
    }
} finally {
    Stop-Transcript | Out-Null
}
