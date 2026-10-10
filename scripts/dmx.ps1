<#
dmx-docs on several machines that share one network folder (e.g. U:\DMX-RAG).

A world is one family of documents with its own index and its own audience (projects,
marketing...). The shared folder holds the code, the settings and the master copy of each
world's index. SQLite must not run on a network share, so every command that changes an index
works on a local copy:  lock the world -> copy its master to C:\dmx-rag\data\<world> -> run ->
copy back -> unlock. One machine at a time per world; different worlds can run at the same time.

Shared folder layout (this script lives in <shared>\app\scripts):
  <shared>\app\               git clone of chat-with-dmx
  <shared>\config.toml        settings used by every machine (data_dir = 'C:\dmx-rag\data'),
                              with one [worlds.<name>] table per world
  <shared>\thesaurus.toml     search vocabulary shared by every world
  <shared>\worlds\<world>\    index.sqlite3 (master), index.prev.sqlite3 (previous), LOCK,
                              register.csv (projects: machine register)
  <shared>\models\            embedding model, copied to each machine once
  <shared>\tools\uv.exe       builds the local Python environment (no admin rights needed)
  <shared>\logs\              one log per run

Commands. All but setup and migrate work on one world: -World <name>, or the world's name as
first argument; otherwise the script asks (Enter = projects).
  setup          build/update C:\dmx-rag\venv (GPU packages on NVIDIA machines)
  web            configuration page (folders, exclusions, scans), checked in when closed
  index          scan for new/changed/deleted files
  embed [args]   compute embeddings, e.g. embed --max-minutes 300
  ocr [args]     read scanned PDF pages (OCR), e.g. ocr --max-minutes 300 or ocr --retry
  update         index, then OCR, then embed: unattended run, e.g. on the GPU machine (a failing step stops
                 the run, is named in the last message and becomes the exit code)
  pull           refresh this machine's read-only copy for Claude Desktop (no lock)
  status         index statistics of the local copy
  search "..."   test a search on the local copy
  unlock -Force  remove a stale lock left by a machine that crashed (also one of the old layout)
  migrate        one-time move from the single-index layout (every command but unlock does it first)
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('setup', 'web', 'index', 'embed', 'ocr', 'update', 'pull', 'status', 'search', 'unlock', 'migrate')]
    [string]$Command,
    [string]$World,
    [switch]$Force,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
$App = Split-Path $PSScriptRoot -Parent
$Shared = Split-Path $App -Parent
$SharedConfig = Join-Path $Shared 'config.toml'
$SharedModels = Join-Path $Shared 'models'
$SharedTessdata = Join-Path $Shared 'tools\tessdata'
$Logs = Join-Path $Shared 'logs'
$Uv = Join-Path $Shared 'tools\uv.exe'

# DMX_RAG_LOCAL: the tests run this script against a temporary folder instead of C:\dmx-rag.
$Local = if ($env:DMX_RAG_LOCAL) { $env:DMX_RAG_LOCAL } else { 'C:\dmx-rag' }
$LocalData = Join-Path $Local 'data'
$LocalModels = Join-Path $LocalData 'models'
$LocalTessdata = Join-Path $Local 'tessdata'
$LocalConfig = Join-Path $Local 'config.toml'
$Venv = Join-Path $Local 'venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$Stamp = Join-Path $Local 'installed.txt'
$Rest = @($Rest | Where-Object { $_ })

# Set by Set-World: the world's folders on the share and on this machine.
$SharedWorld = $Master = $Lock = $LocalWorld = $LocalDb = $null
$WorldArgs = @()
$ExitCode = 0   # set by Invoke-Locked when a step failed: the unattended run is judged by it

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
    # This script is part of the stamp: a change in how it installs triggers a reinstall.
    $files = @(Get-Item (Join-Path $App 'pyproject.toml'), $PSCommandPath) + @(Get-ChildItem (Join-Path $App 'src') -Recurse -File)
    $latest = ($files | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum
    "$($latest.Ticks)|gpu=$(Test-Gpu)"
}

function Test-Gpu { [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue) }

function Install-Env {
    if (-not (Test-Path $Uv)) { throw "uv.exe not found in $(Split-Path $Uv)" }
    New-Item -ItemType Directory -Force $Local, $LocalData, (Join-Path $Local 'exports') | Out-Null  # exports: export_image
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
            # The CPU and GPU packages share their folders (onnxruntime\, fastembed\): uninstalling
            # the CPU ones deletes files of the GPU ones, so those are always reinstalled.
            Invoke-Native $Uv @('pip', 'install', '--python', $Py, '--reinstall-package', 'onnxruntime-gpu',
                                '--reinstall-package', 'fastembed-gpu', 'fastembed-gpu>=0.8,<0.9', $ort)
            Invoke-Native $Py @('-c', "import fastembed, onnxruntime as o; p = o.get_available_providers(); print('GPU runtime check:', p); assert 'CUDAExecutionProvider' in p")
        } else {
            Say "NVIDIA GPU found but its driver is too old for CUDA 12 (nvidia-smi says CUDA $cuda): embeddings will use the CPU."
        }
    }
    Get-CodeStamp | Set-Content -Encoding ascii $Stamp
    $hasWord = Test-Path 'Registry::HKEY_CLASSES_ROOT\Word.Application\CurVer'
    $hasPpt = Test-Path 'Registry::HKEY_CLASSES_ROOT\PowerPoint.Application\CurVer'
    $hasLo = (Test-Path 'C:\Program Files\LibreOffice\program\soffice.exe') -or (Test-Path 'C:\Program Files (x86)\LibreOffice\program\soffice.exe')
    if (-not ($hasWord -or $hasLo)) { Say 'Note: neither Word nor LibreOffice is installed here, so .doc files are skipped if you index from this machine.' }
    if (-not ($hasPpt -or $hasLo)) { Say 'Note: neither PowerPoint nor LibreOffice is installed here, so .ppt files are skipped if you index from this machine.' }
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

function Sync-Tessdata {
    # OCR language files (a few MB each): mirrored from the shared folder when they change.
    if (Test-Path (Join-Path $SharedTessdata '*.traineddata')) {
        robocopy $SharedTessdata $LocalTessdata *.traineddata /MIR /NFL /NDL /NJH /NP | Out-Null
    }
}

# ------------------------------------------------------------------ worlds

function Get-Worlds {
    # World names, in the order of the [worlds.<name>] tables of the shared config.
    $text = Get-Content $SharedConfig -Raw
    @([regex]::Matches($text, '(?m)^\s*\[worlds\.([a-z0-9_-]+)\]') | ForEach-Object { $_.Groups[1].Value })
}

function Resolve-World {
    $known = @(Get-Worlds)
    if (-not $known) { throw "No [worlds.<name>] table in $SharedConfig" }
    if (-not $script:World -and $script:Rest.Count -gt 0 -and $known -contains $script:Rest[0]) {
        $script:World = $script:Rest[0]   # e.g. "7 - Update a world.cmd" marketing
        $script:Rest = @($script:Rest | Select-Object -Skip 1)
    }
    if (-not $script:World) {
        $answer = Read-Host "World ($($known -join ', ')) [projects]"
        $script:World = if ($answer.Trim()) { $answer.Trim() } else { 'projects' }
    }
    # The command line tool knows the configured (lower-case) names only.
    $script:World = $script:World.Trim().ToLowerInvariant()
    if ($known -notcontains $script:World) { throw "Unknown world '$($script:World)'. Configured worlds: $($known -join ', ')" }
}

function Set-World {
    $script:SharedWorld = Join-Path $Shared "worlds\$World"
    $script:Master = Join-Path $SharedWorld 'index.sqlite3'
    $script:Lock = Join-Path $SharedWorld 'LOCK'
    $script:LocalWorld = Join-Path $LocalData $World
    $script:LocalDb = Join-Path $LocalWorld 'index.sqlite3'
    $script:WorldArgs = @('--world', $World)
}

function Get-ServedWorld([string]$commandLine) {
    if ($commandLine -match '--world\s+"?([a-z0-9_-]+)') { $Matches[1] } else { 'projects' }  # no --world: the default
}

function Stop-LocalServers([string]$world) {
    # Claude Desktop's server of that world keeps its local index open; Claude Desktop restarts it by itself.
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'dmx_docs\.cli' -and $_.CommandLine -match ' serve' -and
                       $_.CommandLine -match [regex]::Escape($LocalConfig) -and
                       (Get-ServedWorld $_.CommandLine) -eq $world } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function New-Move([string]$from, [string]$to) { [pscustomobject]@{ From = $from; To = $to } }

function Select-Pending([object[]]$moves) {
    # The items that are still at the old place and not yet at the new one.
    $moves | Where-Object { (Test-Path $_.From) -and -not (Test-Path $_.To) }
}

function Move-One($move, [switch]$Required) {
    # A rename. One that fails is reported with the real reason and left for the next run,
    # unless it is Required: then the command stops here.
    try { Move-Item $move.From $move.To -ErrorAction Stop }
    catch {
        $reason = $_.Exception.Message
        if ($Required) { throw "Could not move $($move.From) to $($move.To): $reason" }
        Say "Could not move $($move.From) ($reason): the next run tries again."
    }
}

function Read-LockFile([string]$path) {
    $text = "$(Get-Content $path -Raw)".Trim()
    if ($text) { $text } else { '(empty lock file)' }
}

function Move-LegacyLayout {
    # Before worlds there was one index (<shared>\data, C:\dmx-rag\data): it becomes the
    # "projects" world. Renames only, never copies. Every item is moved when it is still at the
    # old place and not yet at the new one, whether or not the index itself moved before: a move
    # that was interrupted or that failed is completed by the next run, and nothing is left to do
    # once done.
    $oldData = Join-Path $Shared 'data'
    $oldLock = Join-Path $oldData 'LOCK'
    $projects = Join-Path $Shared 'worlds\projects'
    $localProjects = Join-Path $LocalData 'projects'

    $sharedIndex = @(Select-Pending @(New-Move (Join-Path $oldData 'index.sqlite3') (Join-Path $projects 'index.sqlite3')))
    $sharedMore = @(Select-Pending @(
        (New-Move (Join-Path $oldData 'index.prev.sqlite3') (Join-Path $projects 'index.prev.sqlite3')),
        (New-Move (Join-Path $Shared 'register.csv') (Join-Path $projects 'register.csv'))))
    $sharedLock = @(Select-Pending @(New-Move $oldLock (Join-Path $projects 'LOCK')))

    $localIndex = @(Select-Pending @(New-Move (Join-Path $LocalData 'index.sqlite3') (Join-Path $localProjects 'index.sqlite3')))
    $localMoves = @()
    foreach ($name in 'index.sqlite3-wal', 'index.sqlite3-shm', 'logs') {
        $localMoves += New-Move (Join-Path $LocalData $name) (Join-Path $localProjects $name)
    }
    if (Test-Path $LocalData) {
        foreach ($file in @(Get-ChildItem $LocalData -Filter 'vec_*' -File)) {   # search caches
            $localMoves += New-Move $file.FullName (Join-Path $localProjects $file.Name)
        }
    }
    $localMoves += New-Move (Join-Path $Local 'register.csv') (Join-Path $localProjects 'register.csv')
    $localMore = @(Select-Pending $localMoves)

    if ($sharedIndex.Count + $sharedMore.Count + $sharedLock.Count + $localIndex.Count + $localMore.Count -gt 0) {
        # Refuse before moving anything.
        if (@(Get-Worlds) -notcontains 'projects') {
            throw "The single index becomes the 'projects' world, but there is no [worlds.projects] table: add it to $SharedConfig first. Nothing was moved."
        }
        if ($sharedLock.Count -gt 0) {
            $held = Read-LockFile $oldLock
            $fields = $held.Split('|')
            $lockPid = if ($fields.Count -ge 5) { $fields[4] -as [int] } else { $null }
            $mine = $fields[0] -eq $env:COMPUTERNAME
            if ($mine -and $lockPid -and (Get-Process -Id $lockPid -ErrorAction SilentlyContinue | Where-Object ProcessName -match 'powershell')) {
                throw "The index is being worked on with the old layout by a run that is still going on this machine ($held). Lock file: $oldLock. Let that run finish, then run this again."
            }
            if (-not $mine) {
                throw "The index is locked with the old layout by $($fields[0]) ($held). Lock file: $oldLock. Run the interrupted command again on that machine (it will continue). If that machine crashed and its work can be dropped, run 'Unlock after a crash' for projects (or delete that file), then run this again."
            }
            # A run of this machine that was cut short: its lock moves with the index, so the next
            # locked run continues with the local copy (which is moved below) instead of replacing it.
        }
    }

    if ($sharedIndex.Count + $sharedMore.Count + $sharedLock.Count -gt 0) {
        Say 'One-time move of the shared index to worlds\projects ...'
        New-Item -ItemType Directory -Force $projects | Out-Null
        foreach ($move in $sharedIndex) { Move-One $move -Required }
        foreach ($move in $sharedMore) { Move-One $move }
        foreach ($move in $sharedLock) { Move-One $move -Required }
    }
    if ((Test-Path $oldData) -and -not (Get-ChildItem $oldData -Force)) { Remove-Item $oldData -ErrorAction SilentlyContinue }

    if ($localIndex.Count -gt 0) {
        Say 'One-time move of the local index to data\projects ...'
        Copy-Item $SharedConfig $LocalConfig -Force   # a restarted server must find the new layout
        New-Item -ItemType Directory -Force $localProjects | Out-Null
        $moved = $false
        $reason = ''
        for ($try = 1; $try -le 5 -and -not $moved; $try++) {
            Stop-LocalServers 'projects'
            Start-Sleep -Milliseconds 500
            try { Move-Item $localIndex[0].From $localIndex[0].To -ErrorAction Stop; $moved = $true } catch { $reason = $_.Exception.Message }
        }
        if (-not $moved) {
            throw "The local index is in use (Claude Desktop?). Quit Claude Desktop completely and run this again. Nothing was moved on this machine. ($reason)"
        }
    }
    if ($localMore.Count -gt 0) {
        # Search caches and logs are not essential: a failed move is reported and retried by the next run.
        if ($localIndex.Count -eq 0) { Say 'One-time move of the local files to data\projects ...' }
        New-Item -ItemType Directory -Force $localProjects | Out-Null
        foreach ($move in $localMore) { Move-One $move }
    }
}

# --------------------------------------------------------- lock and copies

function Get-LockInfo { if (Test-Path $Lock) { (Get-Content $Lock -Raw).Trim() } }

function Enter-Lock([string]$what) {
    New-Item -ItemType Directory -Force $SharedWorld | Out-Null
    $info = "$env:COMPUTERNAME|$env:USERNAME|$(Get-Date -Format 'yyyy-MM-dd HH:mm')|$what|$PID"
    try {
        $fs = [IO.File]::Open($Lock, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        $bytes = [Text.Encoding]::ASCII.GetBytes($info)
        $fs.Write($bytes, 0, $bytes.Length); $fs.Close()
        return $false   # fresh lock: start from the master copy
    } catch [IO.IOException] {
        $held = Get-LockInfo
        $fields = if ($held) { $held.Split('|') } else { @() }
        if ($fields.Count -ge 5 -and $fields[0] -eq $env:COMPUTERNAME -and
            (Get-Process -Id ([int]$fields[4]) -ErrorAction SilentlyContinue | Where-Object ProcessName -match 'powershell')) {
            throw "'$($fields[3])' is still running on this machine for '$World' (started $($fields[2])). Wait for it to finish, or stop it first."
        }
        if ($held -and $fields[0] -eq $env:COMPUTERNAME) {
            Say "This machine already holds the lock of '$World' ($held): continuing with its local copy, which has unsaved work."
            [IO.File]::WriteAllText($Lock, $info)   # this process owns it now
            return $true
        }
        throw "The '$World' index is in use by another machine: $held`nWait for it to finish. If that machine crashed, run: dmx.ps1 unlock -World $World -Force"
    }
}

function Exit-Lock { Remove-Item $Lock -Force -ErrorAction SilentlyContinue }

function Remove-LocalDb {
    # Returns $false if the file stays in use (the old copy is then left untouched).
    for ($try = 1; $try -le 5; $try++) {
        if (-not (Test-Path $LocalDb)) { break }
        Stop-LocalServers $World
        Start-Sleep -Milliseconds 500
        try { [IO.File]::Delete($LocalDb) } catch { }
    }
    if (Test-Path $LocalDb) { return $false }
    Remove-Item "$LocalDb-wal", "$LocalDb-shm" -Force -ErrorAction SilentlyContinue
    return $true
}

function Copy-MasterToLocal {
    # Never copy over the local index in place: a copy interrupted by a reader corrupts it.
    New-Item -ItemType Directory -Force $LocalWorld | Out-Null
    $new = "$LocalDb.new"
    if (Test-Path $Master) {
        Say "Copying the '$World' index from the shared folder ..."
        Copy-Item $Master $new -Force
    }
    if (-not (Remove-LocalDb)) {
        Remove-Item $new -Force -ErrorAction SilentlyContinue
        throw "The local '$World' index is in use (Claude Desktop?). Quit Claude Desktop completely and run this again. The current local copy was left as it was."
    }
    if (Test-Path $new) { [IO.File]::Move($new, $LocalDb) }
    else { Say "No '$World' index in the shared folder yet: starting a new one." }
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
    Say "Copying the '$World' index back to the shared folder ..."
    $tmp = "$Master.tmp"
    Copy-Item $LocalDb $tmp -Force
    if (Test-Path $Master) { Move-Item $Master (Join-Path $SharedWorld 'index.prev.sqlite3') -Force }
    Move-Item $tmp $Master -Force
    Copy-Item $SharedConfig $LocalConfig -Force
    return $true
}

function Invoke-Locked([string]$what, [object[]]$steps) {
    # $steps: one argument list per dmx-docs command, run in order on the same local copy.
    # Lock first: never reinstall packages under a run that is still going on this machine.
    $resumed = Enter-Lock $what
    $working = $resumed   # true once the local copy holds this run's (or an unsaved run's) work
    $ok = $false
    $failedStep = $null   # the step that stopped the run, and its exit code
    $failedCode = 0
    try {
        Assert-Env
        Sync-Models
        Sync-Tessdata
        if (-not $resumed -or -not (Test-Path $LocalDb)) { Copy-MasterToLocal }
        $working = $true
        foreach ($dmxArgs in $steps) {
            $step = "dmx-docs $(($WorldArgs + $dmxArgs) -join ' ')"
            Say "Running: $step"
            Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + $dmxArgs) -AllowFail
            Say "dmx-docs finished (exit code $LASTEXITCODE)."
            if ($LASTEXITCODE -ne 0) {
                $failedStep = $step
                $failedCode = $LASTEXITCODE
                Say 'Stopped: the next steps are skipped.'
                break
            }
        }
        $ok = $true
    } finally {
        # Also runs after Ctrl+C: what was done so far is kept (the index is resumable).
        if (-not $working) {
            Exit-Lock; Say 'Nothing was changed; lock released.'   # failed before touching the index
        } elseif ((Test-Path $Py) -and (Save-LocalToMaster)) {
            Exit-Lock
            if ($failedStep) {
                # Not "Done": an unattended run is judged by this last message and by the exit code.
                Say "Stopped early: '$failedStep' ended with exit code $failedCode. What was done so far is saved; the '$World' index in the shared folder is unlocked."
            } else {
                Say "Done. The '$World' index in the shared folder is up to date and unlocked."
            }
        }
        if ($failedStep) { $script:ExitCode = $failedCode }
        if ($ok -and $what -in 'embed', 'update') { Publish-Models }
    }
}

# -------------------------------------------------------------------- main

if (-not (Test-Path $SharedConfig)) { throw "Settings not found: $SharedConfig" }
$needsWorld = $Command -notin 'setup', 'migrate'
if ($needsWorld) { Resolve-World; Set-World }
New-Item -ItemType Directory -Force $Logs | Out-Null
$tag = if ($needsWorld) { "$($World)_$Command" } else { $Command }
$log = Join-Path $Logs ("{0}_{1}_{2}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $env:COMPUTERNAME, $tag)
Start-Transcript -Path $log -Append | Out-Null
try {
    # unlock must work even while the layout cannot be moved (a lock of the old layout is why).
    if ($Command -ne 'unlock') { Move-LegacyLayout }
    switch ($Command) {
        'setup' { Install-Env; Sync-Models; Sync-Tessdata }
        'migrate' { Say 'The layout is up to date.' }
        'web' { Invoke-Locked 'web' @(, (@('web') + $Rest)) }
        'index' { Invoke-Locked 'index' @(, (@('index') + $Rest)) }
        'embed' { Invoke-Locked 'embed' @(, (@('embed') + $Rest)) }
        'ocr' { Invoke-Locked 'ocr' @(, (@('ocr') + $Rest)) }
        'update' { Invoke-Locked 'update' @(@('index'), @('ocr'), @('embed')) }
        'pull' {
            Assert-Env
            Sync-Models
            $held = Get-LockInfo
            if ($held -and $held.Split('|')[0] -eq $env:COMPUTERNAME) { throw "This machine holds the lock of '$World' with unsaved work ($held): run the interrupted command again first." }
            if ($held) { Say "Note: $($held.Split('|')[0]) is working on '$World' right now; you get the last saved version." }
            if (-not (Test-Path $Master)) { throw "World '$World' has no index in the shared folder yet: index it first (launcher 3 or 7)." }
            Copy-MasterToLocal
            Copy-Item $SharedConfig $LocalConfig -Force
            $th = Join-Path $Shared 'thesaurus.toml'
            if (Test-Path $th) { Copy-Item $th (Join-Path $Local 'thesaurus.toml') -Force }  # search synonyms
            $reg = Join-Path $SharedWorld 'register.csv'
            $stale = Join-Path $Shared 'register.csv'   # where the register was before worlds
            if ($World -eq 'projects' -and (Test-Path $stale)) {
                # The move to worlds\projects leaves a file alone when the new place already has one.
                Say "WARNING: $stale is not used. The machine register now lives in $reg. If it is the newer one (written later by the old register import), move it there, over the other, and run this again."
            }
            if (Test-Path $reg) { Copy-Item $reg (Join-Path $LocalWorld 'register.csv') -Force }  # machine register
            # Build the vector cache and facets now, so Claude's first question is fast.
            Say 'Preparing the search cache (about 30-60 s) ...'
            Invoke-Native $Py @('-c', "from dmx_docs.config import load_config; from dmx_docs.tools import DocTools; DocTools(load_config(r'$LocalConfig', world='$World')).search('warm-up', limit=1)") -AllowFail
            Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $LocalConfig) + $WorldArgs + @('status')) -AllowFail
            $name = if ($World -eq 'projects') { 'dmx-docs' } else { "dmx-$World" }
            $serverArgs = @('-m', 'dmx_docs.cli', '--config', $LocalConfig) + $WorldArgs + @('serve')
            $snippet = @{ mcpServers = @{ $name = @{ command = $Py; args = $serverArgs } } } | ConvertTo-Json -Depth 5
            Say "Claude Desktop configuration for this machine:`n$snippet"
        }
        'status' { Assert-Env; Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + @('status')) -AllowFail }
        'search' { Assert-Env; Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + @('search') + $Rest) -AllowFail }
        'unlock' {
            $held = Get-LockInfo
            # Before the move to worlds\ the lock of the projects index was <shared>\data\LOCK.
            $oldLock = Join-Path $Shared 'data\LOCK'
            $hasOld = ($World -eq 'projects') -and (Test-Path $oldLock)
            if (-not $held -and -not $hasOld) { Say "Not locked ($World)." }
            else {
                if ($held -and $Force) { Exit-Lock; Say "Lock of '$World' removed (was: $held)." }
                elseif ($held) { Say "Locked by: $held" }
                if ($hasOld) {
                    $oldHeld = Read-LockFile $oldLock
                    if ($Force) { Remove-Item $oldLock -Force; Say "Lock of the old layout removed (was: $oldHeld)." }
                    else { Say "Locked by (old layout): $oldHeld" }
                }
                if (-not $Force) { Say 'Run again with -Force to remove the lock (only if that machine is no longer working on it).' }
            }
        }
    }
} finally {
    Stop-Transcript | Out-Null
}
exit $ExitCode
