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
  <shared>\disciplines.toml   folder rules and word lists of the engineering world (reports engineering-classification.csv and keyword-suggestions.csv go back next to the index)
  <shared>\maps\<world>.md    world maps (what each world holds, where, what it lacks), copied by pull
  <shared>\knowledge\         knowledge cards (projects\<PROJECT>.md, vocabulary.md, README.md), mirrored by pull
  <shared>\worlds\<world>\    index.sqlite3 (master), index.prev.sqlite3 (previous), LOCK,
                              register.csv (projects: machine register), corrections.csv and review.csv (offers)
  <shared>\models\            embedding model, copied to each machine once
  <shared>\tools\uv.exe       builds the local Python environment (no admin rights needed)
  <shared>\tools\tessdata\    OCR language files (copied to each machine)
  <shared>\logs\              one log per run (a world of profile offers: <shared>\worlds\<world>\logs\)

Commands. All but setup and migrate work on one world: -World <name>, or the world's name as
first argument; otherwise the script asks (Enter = projects).
  setup          build/update C:\dmx-rag\venv (GPU packages on NVIDIA machines)
  web            configuration page (folders, exclusions, scans), checked in when closed
  index          scan for new/changed/deleted files
  embed [args]   compute embeddings, e.g. embed --max-minutes 300
  ocr [args]     read scanned PDF pages (OCR), e.g. ocr --max-minutes 300 or ocr --retry
  describe [args] describe pictures with the local vision model (Ollama), e.g. describe --max-minutes 600,
                 describe --retry, or describe --compare gemma4:12b,qwen3-vl:8b (side-by-side page, index unchanged)
  offers [args]  offers world: read the offers (documents, price pages, register sources), e.g. offers --review writes
                 review.csv (copied back to the shared folder); corrections.csv in the shared folder is copied down first.
                 Does nothing for the other worlds.
  update         index, then OCR, then offers, then picture descriptions, then embed: unattended run, e.g. on the GPU machine
                 (a failing step stops the run, is named in the last message and becomes the exit code)
  pull           refresh this machine's read-only copy for Claude Desktop (no lock)
  status         index statistics of the local copy
  map            print the world map (maps\<world>.md on the shared folder + live counts of the local copy)
  search "..."   test a search on the local copy
  acl-survey [--files]  read-only: which accounts and groups can read the folders of the local copy's documents;
                 report in <shared>\worlds\<world>\acl-survey (run it under the account that will serve the world)
  permissions    worlds with access = "acl": read the reading rule of every indexed folder of the local copy and write
                 access.sqlite3 to <shared>\worlds\<world> then to the local world folder (no lock; report in
                 <shared>\worlds\<world>\acl-survey). update ends with it; pull copies the shared file down.
                 Run it under the account that may read the folders' permissions (the VM's daily task, launcher 8)
  unlock -Force  remove a stale lock left by a machine that crashed (also one of the old layout)
  migrate        one-time move from the single-index layout (every command but unlock does it first)
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('setup', 'web', 'index', 'embed', 'ocr', 'describe', 'offers', 'update', 'pull', 'status', 'map', 'search', 'acl-survey', 'permissions', 'unlock', 'migrate')]
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

# DMX_RAG_LOCAL: this machine's folder instead of C:\dmx-rag (the VM: its data disk, a machine-wide
# environment variable), or a temporary folder in the tests.
$Local = if ($env:DMX_RAG_LOCAL) { $env:DMX_RAG_LOCAL } else { 'C:\dmx-rag' }
$LocalData = Join-Path $Local 'data'
# dmx-docs reads its data_dir from the shared config (C:\dmx-rag\data): elsewhere, tell it where.
if ($env:DMX_RAG_LOCAL) { $env:DMX_DOCS_DATA_DIR = $LocalData }
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
        robocopy $SharedTessdata $LocalTessdata *.traineddata /MIR /R:2 /W:5 /NFL /NDL /NJH /NP | Out-Null
        $copied = $LASTEXITCODE   # robocopy: 0 = up to date, bit 1 = files copied, 2-7 = extras/mismatches, 8+ = failures
        if ($copied -ge 8) { Say "WARNING: could not copy the OCR language files (robocopy exit code $copied); OCR may be unavailable." }
        elseif ($copied -band 1) { Say 'OCR language files updated from the shared folder.' }
        $global:LASTEXITCODE = 0   # a robocopy code is not a step's exit code: nothing later may mistake it for one
    }
}

# ------------------------------------------------------------------ worlds

function Get-Worlds {
    # World names, in the order of the [worlds.<name>] tables of the shared config.
    $text = Get-Content $SharedConfig -Raw
    @([regex]::Matches($text, '(?m)^\s*\[worlds\.([a-z0-9_-]+)\]') | ForEach-Object { $_.Groups[1].Value })
}

function Get-WorldProfile([string]$name) {
    # The profile of a world, from its [worlds.<name>] table of the shared config ('' when it sets none).
    $text = Get-Content $SharedConfig -Raw
    $table = [regex]::Match($text, '(?ms)^\s*\[worlds\.' + [regex]::Escape($name) + '\](.*?)(?=^\s*\[|\z)')
    if ($table.Success -and $table.Groups[1].Value -match '(?m)^\s*profile\s*=\s*[''"]([^''"]+)[''"]') { $Matches[1] } else { '' }
}

function Get-WorldAccess([string]$name) {
    # The access mode of a world ('world' or 'acl'), from its [worlds.<name>] table of the shared config.
    $text = Get-Content $SharedConfig -Raw
    $table = [regex]::Match($text, '(?ms)^\s*\[worlds\.' + [regex]::Escape($name) + '\](.*?)(?=^\s*\[|\z)')
    if ($table.Success -and $table.Groups[1].Value -match '(?m)^\s*access\s*=\s*[''"]([^''"]+)[''"]') { $Matches[1] } else { 'world' }
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

function Stop-NoAccess {
    Write-Host "No access to the '$World' world in the shared folder (ask for the AD group that can read it). Other worlds are not affected." -ForegroundColor Red
    $script:ExitCode = 5
    exit 5   # the finally block of the main code still closes the transcript
}

function Test-WorldAccess {
    # False when this user may not read the world's folder on the share (each world has its own AD group).
    # A folder that does not exist yet is a new world: that is fine. With access-based enumeration (or no
    # entry for the user) a folder the user may not see looks missing, so it cannot be told apart from a new
    # world here: Enter-Lock (writing the lock) and pull (no index) name that case too.
    try {
        if (Test-Path -LiteralPath $SharedWorld -ErrorAction Stop) {
            Get-ChildItem -LiteralPath $SharedWorld -Force -ErrorAction Stop | Select-Object -First 1 | Out-Null
        }
        return $true
    } catch [UnauthorizedAccessException] { return $false }
}

function Get-ServedWorld([string]$commandLine) {
    if ($commandLine -match '--world\s+"?([a-z0-9_-]+)') { $Matches[1] } else { 'projects' }  # no --world: the default
}

function Stop-LocalServers([string]$world) {
    # Claude Desktop's server of that world keeps its local index open; Claude Desktop restarts it by itself.
    # Never the central server (serve-all, every world): it is paused for the world instead (Suspend-CentralWorld).
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'dmx_docs\.cli' -and $_.CommandLine -match ' serve(\s|$)' -and
                       $_.CommandLine -match [regex]::Escape($LocalConfig) -and
                       (Get-ServedWorld $_.CommandLine) -eq $world } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Get-CentralAdminPort {
    # The admin port of the central server (serve-all) on this machine, from the [central] table of the shared
    # config (default 8766); $null when the config has no [central] table.
    $text = Get-Content $SharedConfig -Raw
    $table = [regex]::Match($text, '(?ms)^\s*\[central\](.*?)(?=^\s*\[|\z)')
    if (-not $table.Success) { return $null }
    if ($table.Groups[1].Value -match '(?m)^\s*admin_port\s*=\s*(\d+)') { [int]$Matches[1] } else { 8766 }
}

function Invoke-CentralAdmin([int]$port, [string]$action) {
    # POST /_admin/<world>/<action> on the plain-HTTP loopback listener of the central server (it answers
    # whether or not the main listener has a certificate). Status: 'ok', 'down' (nothing listens there), 'failed'.
    try {
        # A pause waits up to 120 s for the running calls of the world.
        $answer = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/_admin/$World/$action" -Headers @{ 'X-DMX-Admin' = '1' } -TimeoutSec 180 -UseBasicParsing
        [pscustomobject]@{ Status = 'ok'; Answer = $answer }
    } catch {
        $status = if ($_.Exception -is [System.Net.WebException] -and $_.Exception.Status -eq 'ConnectFailure') { 'down' } else { 'failed' }
        [pscustomobject]@{ Status = $status; Answer = $null }
    }
}

function Suspend-CentralWorld {
    # The central server stops answering for this world and lets go of its files; the other worlds go on.
    # Returns the admin port when Resume-CentralWorld must follow (also after a pause that was not confirmed,
    # the resume is harmless), $null when no central server is configured or none runs here.
    $port = Get-CentralAdminPort
    if (-not $port) { return $null }
    $r = Invoke-CentralAdmin $port 'pause'
    if ($r.Status -eq 'down') { return $null }
    if ($r.Status -eq 'ok' -and $r.Answer.paused) { Say "The central server is paused for '$World' (calls still running: $($r.Answer.running))." }
    else { Say "WARNING: the central server did not confirm the pause of '$World': the update goes on, the server is reopened at the end." }
    return $port
}

function Resume-CentralWorld([int]$port) {
    $r = Invoke-CentralAdmin $port 'resume'
    if ($r.Status -eq 'ok' -and $r.Answer.paused -eq $false) { Say "The central server answers for '$World' again." }
    else { Say "WARNING: the central server did not confirm the reopening of '$World': restart it (dmx-docs serve-all) if the world stays 'being updated' (it reopens by itself after 4 hours)." }
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
    $info = "$env:COMPUTERNAME|$env:USERNAME|$(Get-Date -Format 'yyyy-MM-dd HH:mm')|$what|$PID"
    try {
        New-Item -ItemType Directory -Force $SharedWorld | Out-Null
        $fs = [IO.File]::Open($Lock, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        $bytes = [Text.Encoding]::ASCII.GetBytes($info)
        $fs.Write($bytes, 0, $bytes.Length); $fs.Close()
        return $false   # fresh lock: start from the master copy
    } catch [UnauthorizedAccessException] {
        Stop-NoAccess   # a world the user cannot even see (access-based enumeration) looks like a new one until the lock is written
    } catch [IO.IOException] {
        $held = Get-LockInfo
        if (-not $held) { throw }   # no owner: not a lock, a disk or network error, reported as it is
        $fields = $held.Split('|')
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

function Get-FreeBytes([string]$path) {
    # DMX_RAG_FREE_BYTES: the tests pretend a nearly full disk.
    if ($env:DMX_RAG_FREE_BYTES) { return [int64]$env:DMX_RAG_FREE_BYTES }
    ([IO.DriveInfo]::new([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($path)))).AvailableFreeSpace
}

function Get-SizeOf([object[]]$items) {
    [int64](($items | Where-Object { $_ } | Measure-Object -Property Length -Sum).Sum)
}

function Test-RoomForMaster {
    # Is there room for the new copy next to the old one? If not, is there once the old one is removed?
    # The vector cache built after the copy is counted at the size of the current one.
    # Returns 'beside', 'replace' or throws (the local copy is then left as it was).
    if (-not (Test-Path $Master)) { return 'beside' }
    $margin = if ($env:DMX_RAG_SPACE_MARGIN) { [int64]$env:DMX_RAG_SPACE_MARGIN } else { 1GB }
    $vec = Get-SizeOf @(Get-ChildItem $LocalWorld -Filter 'vec_*.npy' -File -ErrorAction SilentlyContinue)
    $need = (Get-Item $Master).Length + $vec + $margin
    $free = Get-FreeBytes $LocalWorld
    if ($free -ge $need) { return 'beside' }
    $old = Get-SizeOf @(Get-Item $LocalDb, "$LocalDb-wal" -ErrorAction SilentlyContinue)
    if ($free + $old -ge $need) { return 'replace' }
    $gb = { param($b) '{0:N1} GB' -f ($b / 1GB) }
    throw ("Not enough free space on $([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($LocalWorld))) for the '$World' index: " +
           "$(& $gb $need) needed, $(& $gb $free) free ($(& $gb ($free + $old)) with the old copy removed). " +
           "The local copy was left as it was.")
}

function Copy-MasterToLocal {
    # Never copy over the local index in place: a copy interrupted by a reader corrupts it.
    New-Item -ItemType Directory -Force $LocalWorld | Out-Null
    $new = "$LocalDb.new"
    if ((Test-RoomForMaster) -eq 'replace') {
        # A small disk (the VM): the old copy goes first, its server stays stopped until the new one is in.
        Say "Not enough room for two copies of '$World': the old local copy is removed first (its server stops meanwhile)."
        if (-not (Remove-LocalDb)) {
            throw "The local '$World' index is in use (Claude Desktop?). Quit Claude Desktop completely and run this again. The current local copy was left as it was."
        }
    }
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
    # The user's corrections of the offers world: the shared file is the one that counts.
    $corrections = Join-Path $SharedWorld 'corrections.csv'
    $localCorrections = Join-Path $LocalWorld 'corrections.csv'
    if (Test-Path $corrections) { Copy-Item $corrections $localCorrections -Force }
    else { Remove-Item $localCorrections -Force -ErrorAction SilentlyContinue }
    # Verified offer records (offers world): the shared folder is the one that counts; the local copy only
    # serves when the share is out of reach, and is never copied back.
    $verified = Join-Path $SharedWorld 'verified'
    $localVerified = Join-Path $LocalWorld 'verified'
    if (Test-Path $verified) {
        New-Item -ItemType Directory -Force $localVerified | Out-Null
        Get-ChildItem $localVerified -File -ErrorAction SilentlyContinue |
            Where-Object { -not (Test-Path (Join-Path $verified $_.Name)) } | Remove-Item -Force
        Get-ChildItem $verified -File -Filter '*.json' | Copy-Item -Destination $localVerified -Force
    }
    else { Remove-Item $localVerified -Recurse -Force -ErrorAction SilentlyContinue }
    Remove-Item (Join-Path $LocalWorld 'review.csv') -Force -ErrorAction SilentlyContinue   # only a review written by this run goes back
    # Engineering world: the two reports of a run (classification, keyword suggestions) likewise
    foreach ($report in 'engineering-classification.csv', 'keyword-suggestions.csv') {
        Remove-Item (Join-Path $LocalWorld $report) -Force -ErrorAction SilentlyContinue
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
    Say "Copying the '$World' index back to the shared folder ..."
    $tmp = "$Master.tmp"
    Copy-Item $LocalDb $tmp -Force
    if (Test-Path $Master) { Move-Item $Master (Join-Path $SharedWorld 'index.prev.sqlite3') -Force }
    Move-Item $tmp $Master -Force
    $review = Join-Path $LocalWorld 'review.csv'   # offers world: the list to check, written by 'offers --review'
    if (Test-Path $review) { Copy-Item $review (Join-Path $SharedWorld 'review.csv') -Force }
    foreach ($report in 'engineering-classification.csv', 'keyword-suggestions.csv') {   # engineering world: to review
        $file = Join-Path $LocalWorld $report
        try { if (Test-Path $file) { Copy-Item $file (Join-Path $SharedWorld $report) -Force } }   # a report open in Excel must not stop the push
        catch { Say "Warning: $report could not be copied to the shared folder ($($_.Exception.Message)); close it and run the push again." }
    }
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
if ($needsWorld -and (Get-WorldProfile $World) -eq 'offers') {
    # Offers carry prices and lost deals: their transcripts stay behind the world's own permission.
    $Logs = Join-Path $SharedWorld 'logs'
    try { New-Item -ItemType Directory -Force $Logs -ErrorAction Stop | Out-Null }
    catch {
        if ($_.Exception -is [UnauthorizedAccessException] -or $_.Exception.InnerException -is [UnauthorizedAccessException]) { Stop-NoAccess }
        throw
    }
}
New-Item -ItemType Directory -Force $Logs | Out-Null
$tag = if ($needsWorld) { "$($World)_$Command" } else { $Command }
$log = Join-Path $Logs ("{0}_{1}_{2}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $env:COMPUTERNAME, $tag)
Start-Transcript -Path $log -Append | Out-Null
try {
    # unlock must work even while the layout cannot be moved (a lock of the old layout is why).
    if ($Command -ne 'unlock') { Move-LegacyLayout }
    # Before any lock or copy: a world the user may not read stops here, the other worlds are not affected.
    if ($Command -in 'web', 'index', 'embed', 'ocr', 'describe', 'offers', 'update', 'pull', 'permissions' -and -not (Test-WorldAccess)) {
        Stop-NoAccess
    }
    switch ($Command) {
        'setup' { Install-Env; Sync-Models; Sync-Tessdata }
        'migrate' { Say 'The layout is up to date.' }
        'web' { Invoke-Locked 'web' @(, (@('web') + $Rest)) }
        'index' { Invoke-Locked 'index' @(, (@('index') + $Rest)) }
        'embed' { Invoke-Locked 'embed' @(, (@('embed') + $Rest)) }
        'ocr' { Invoke-Locked 'ocr' @(, (@('ocr') + $Rest)) }
        'describe' { Invoke-Locked 'describe' @(, (@('describe') + $Rest)) }
        'offers' { Invoke-Locked 'offers' @(, (@('offers') + $Rest)) }
        'update' {
            $steps = @(@('index'), @('ocr'), @('offers'), @('describe'), @('embed'))   # offers does nothing in the other worlds
            if ((Get-WorldAccess $World) -eq 'acl') { $steps += , @('permissions', '--out', (Join-Path $SharedWorld 'access.sqlite3'), '--out', (Join-Path $LocalWorld 'access.sqlite3'), '--report-dir', (Join-Path $SharedWorld 'acl-survey')) }
            Invoke-Locked 'update' $steps
        }
        'pull' {
            Assert-Env
            Sync-Models
            $held = Get-LockInfo
            if ($held -and $held.Split('|')[0] -eq $env:COMPUTERNAME) { throw "This machine holds the lock of '$World' with unsaved work ($held): run the interrupted command again first." }
            if ($held) { Say "Note: $($held.Split('|')[0]) is working on '$World' right now; you get the last saved version." }
            if (-not (Test-Path $Master)) { throw "World '$World' has no index in the shared folder yet (index it first: launcher 3 or 7), or you have no access to it (ask for the AD group that can read it)." }
            $paused = Suspend-CentralWorld
            try {
            Copy-MasterToLocal
            $access = Join-Path $SharedWorld 'access.sqlite3'   # per-person access (worlds with access = "acl"): never copied over in place
            if (Test-Path $access) {
                $accessNew = Join-Path $LocalWorld 'access.sqlite3.new'
                Copy-Item $access $accessNew -Force
                # A running server may hold the live file open for a moment: retry, like the Python writer.
                for ($try = 1; ; $try++) {
                    try { Move-Item $accessNew (Join-Path $LocalWorld 'access.sqlite3') -Force -ErrorAction Stop; break }
                    catch { if ($try -ge 5) { throw }; Start-Sleep -Milliseconds 500 }
                }
            }
            Copy-Item $SharedConfig $LocalConfig -Force
            $th = Join-Path $Shared 'thesaurus.toml'
            if (Test-Path $th) { Copy-Item $th (Join-Path $Local 'thesaurus.toml') -Force }  # search synonyms
            $dr = Join-Path $Shared 'disciplines.toml'
            if (Test-Path $dr) { Copy-Item $dr (Join-Path $Local 'disciplines.toml') -Force }  # engineering world: rules for a local index run
            $maps = Join-Path $Shared 'maps'
            if (Test-Path $maps) {  # world maps, read by the world_map tool next to the local config
                New-Item -ItemType Directory -Force (Join-Path $Local 'maps') | Out-Null
                Copy-Item (Join-Path $maps '*.md') (Join-Path $Local 'maps') -Force
            }
            $knowledge = Join-Path $Shared 'knowledge'
            if (Test-Path $knowledge) {  # knowledge cards (catalog / project_card), mirrored so removed cards disappear here too
                $localKnowledge = Join-Path $Local 'knowledge'
                if (Test-Path $localKnowledge) { Remove-Item $localKnowledge -Recurse -Force }
                Copy-Item $knowledge $localKnowledge -Recurse -Force
            }
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
            } finally {
                if ($paused) { Resume-CentralWorld $paused }   # also when the copy failed: the world must not stay closed
            }
            $name = "dmx-$World"   # dmx-projects, dmx-marketing... (the projects entry was called dmx-docs before)
            $serverArgs = @('-m', 'dmx_docs.cli', '--config', $LocalConfig) + $WorldArgs + @('serve')
            $entry = @{ command = $Py; args = $serverArgs }
            if ($env:DMX_RAG_LOCAL) { $entry.env = @{ DMX_DOCS_DATA_DIR = $LocalData } }   # not in C:\dmx-rag
            $snippet = @{ mcpServers = @{ $name = $entry } } | ConvertTo-Json -Depth 5
            Say "Claude Desktop configuration for this machine:`n$snippet"
        }
        'status' { Assert-Env; Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + @('status')) -AllowFail }
        'map' { Assert-Env; Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + @('map')) -AllowFail }
        'search' { Assert-Env; Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + @('search') + $Rest) -AllowFail }
        'acl-survey' {
            Assert-Env
            if (-not (Test-Path $LocalDb)) { throw "No local copy of '$World' on this machine: run 'pull' (or an index run) first." }
            $out = Join-Path $SharedWorld 'acl-survey'
            Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + @('acl-survey', '--out', $out) + $Rest) -AllowFail
        }
        'permissions' {
            Assert-Env
            if ((Get-WorldAccess $World) -ne 'acl') { Say "World '$World' has access = `"world`": nothing to refresh."; $script:ExitCode = 2; break }
            if (-not (Test-Path $LocalDb)) { throw "No local copy of '$World' on this machine: run 'pull' (or an index run) first." }
            New-Item -ItemType Directory -Force $SharedWorld | Out-Null
            $outs = @('--out', (Join-Path $SharedWorld 'access.sqlite3'), '--out', (Join-Path $LocalWorld 'access.sqlite3'))
            Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + @('permissions') + $outs + @('--report-dir', (Join-Path $SharedWorld 'acl-survey')) + $Rest) -AllowFail
            if ($LASTEXITCODE -ne 0) { $script:ExitCode = $LASTEXITCODE }
        }
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
