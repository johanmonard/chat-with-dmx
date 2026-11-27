<#
Moves the shared DMX-RAG folder from U:\DMX-RAG (Johan's home share) to Y:\DMX-RAG (\\DMX-FS01\RAG_ClaudeAI,
open only to dmx.datahub), in two stages through the laptop's own disk: no single account can read the first
and write the second.

  Stage 1, as yourself (Y: disconnected, so that U: is reachable):
      powershell -ExecutionPolicy Bypass -File copy-shared-folder.ps1 -Stage 1
  Stage 2, with Y: connected as ROTZINGERAG\dmx.datahub:
      powershell -ExecutionPolicy Bypass -File copy-shared-folder.ps1 -Stage 2

Nothing is deleted anywhere: U:\DMX-RAG stays as it is, the staging copy stays until you remove it.
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet('1', '2')][string]$Stage,
    [string]$Source = '\\DMX-FS01.rotzingerag.local\Users$\Johan.Monard\DMX-RAG',
    [string]$Staging = 'C:\dmx-rag-staging\DMX-RAG',
    [string]$Target = '\\DMX-FS01\RAG_ClaudeAI\DMX-RAG'
)
$ErrorActionPreference = 'Stop'

function Copy-Tree([string]$from, [string]$to) {
    # /E all folders, /COPY:DAT no permissions (the target's own apply), logs\ is not worth carrying over.
    $log = Join-Path $env:TEMP ("copy-shared-folder-stage$Stage-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    robocopy $from $to /E /COPY:DAT /DCOPY:T /R:2 /W:5 /XD (Join-Path $from 'logs') /NP /TEE /LOG:$log | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy reported failures (code $LASTEXITCODE), see $log" }
    Write-Host "Copied $from -> $to (robocopy code $LASTEXITCODE, log $log)"
}

function Get-Totals([string]$root) {
    $files = Get-ChildItem $root -Recurse -File -Force | Where-Object { $_.FullName -notlike (Join-Path $root 'logs\*') }
    [pscustomobject]@{ Files = $files.Count; Bytes = ($files | Measure-Object Length -Sum).Sum }
}

if ($Stage -eq '1') {
    if (-not (Test-Path (Join-Path $Source 'config.toml'))) { throw "$Source is not reachable: disconnect Y: first (net use Y: /delete) and log on again if needed." }
    $locks = @(Get-ChildItem (Join-Path $Source 'worlds') -Filter LOCK -Recurse -File -ErrorAction SilentlyContinue)
    if ($locks) { throw "A world is being indexed right now (LOCK in $($locks[0].DirectoryName)). Wait until it is done." }
    Copy-Tree $Source $Staging
    $a = Get-Totals $Source; $b = Get-Totals $Staging
    Write-Host ("Source {0:N0} files {1:N1} GB, staging {2:N0} files {3:N1} GB" -f $a.Files, ($a.Bytes / 1GB), $b.Files, ($b.Bytes / 1GB))
    if ($a.Files -ne $b.Files -or $a.Bytes -ne $b.Bytes) { throw 'The staging copy differs from the source: run stage 1 again.' }
    Write-Host 'Stage 1 done. Now connect Y: as ROTZINGERAG\dmx.datahub and run stage 2.'
}
else {
    if (-not (Test-Path (Join-Path $Staging 'config.toml'))) { throw "No staging copy in ${Staging}: run stage 1 first." }
    if (-not (Test-Path (Split-Path $Target -Parent))) { throw "$(Split-Path $Target -Parent) is not reachable: connect Y: as ROTZINGERAG\dmx.datahub." }
    Copy-Tree $Staging $Target
    $a = Get-Totals $Staging; $b = Get-Totals $Target
    Write-Host ("Staging {0:N0} files {1:N1} GB, target {2:N0} files {3:N1} GB" -f $a.Files, ($a.Bytes / 1GB), $b.Files, ($b.Bytes / 1GB))
    if ($a.Files -ne $b.Files -or $a.Bytes -ne $b.Bytes) { throw 'The target differs from the staging copy: run stage 2 again.' }
    # The one absolute path of the shared config: the offers world's verified records.
    $cfg = Join-Path $Target 'config.toml'
    $text = [IO.File]::ReadAllText($cfg)
    $old = $Source.TrimEnd('\') + '\'
    if ($text.Contains($old)) {
        Copy-Item $cfg "$cfg.bak-before-move" -Force
        [IO.File]::WriteAllText($cfg, $text.Replace($old, $Target.TrimEnd('\') + '\'), (New-Object Text.UTF8Encoding($false)))
        Write-Host "config.toml: paths under $old now point to $Target (backup config.toml.bak-before-move)."
    }
    Write-Host 'Stage 2 done. U:\DMX-RAG and the staging copy are left as they were.'
}
