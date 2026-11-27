# dmx-relay.ps1 - stdio MCP server that forwards each JSON-RPC line to the central dmx server
# (http(s)://host:port/<world>/mcp), signed in as the logged-on Windows user (NTLM), and carries out
# on this PC the actions the server hands back (DMX-RELAY markers: save an image, open a document).
# Windows PowerShell 5.1, no modules. stdout carries JSON-RPC lines only; diagnostics go to stderr.
param(
    [string]$Url,
    [string]$ExportDir = 'C:\dmx-rag\exports',
    [string]$OpenWith = '',
    [switch]$WhoAmI
)

$ErrorActionPreference = 'Stop'
[System.Net.ServicePointManager]::Expect100Continue = $false
Add-Type -AssemblyName System.Web.Extensions
$json = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$json.MaxJsonLength = [int]::MaxValue
$json.RecursionLimit = 200
$utf8 = New-Object System.Text.UTF8Encoding($false)

$OPENABLE = @('.pdf', '.docx', '.doc', '.pptx', '.ppt', '.md', '.txt', '.xlsx', '.xlsm', '.xls', '.one',
              '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp')

function Write-Diag([string]$msg) {
    [Console]::Error.WriteLine('dmx-relay: ' + $msg)
}

if (-not $Url) { Write-Diag 'missing -Url'; exit 2 }
$uri = New-Object System.Uri($Url)
$cache = New-Object System.Net.CredentialCache
$cache.Add((New-Object System.Uri($uri.GetLeftPart([System.UriPartial]::Authority))), 'NTLM',
           [System.Net.CredentialCache]::DefaultNetworkCredentials)

# Returns @{ Status = <int>; Body = <string> }. Status 0: server not reachable.
function Send-Http([string]$target, [string]$method, [string]$body) {
    $req = [System.Net.HttpWebRequest]::Create($target)
    $req.Method = $method
    $req.Credentials = $cache
    $req.PreAuthenticate = $false
    $req.KeepAlive = $true
    $req.UnsafeAuthenticatedConnectionSharing = $true
    $req.ConnectionGroupName = 'dmx-relay'
    $req.Timeout = 300000
    $req.ReadWriteTimeout = 300000
    $req.UserAgent = 'dmx-relay'
    if ($method -eq 'POST') {
        $bytes = $utf8.GetBytes($body)
        $req.ContentType = 'application/json'
        $req.Accept = 'application/json, text/event-stream'
        $req.ContentLength = $bytes.Length
        $s = $req.GetRequestStream()
        $s.Write($bytes, 0, $bytes.Length)
        $s.Close()
    }
    $resp = $null
    try {
        $resp = $req.GetResponse()
    } catch [System.Net.WebException] {
        $resp = $_.Exception.Response
        if ($null -eq $resp) { return @{ Status = 0; Body = '' } }
    }
    try {
        $rs = $resp.GetResponseStream()
        $rd = New-Object System.IO.StreamReader($rs, $utf8)
        $text = $rd.ReadToEnd()
        $rd.Close()
        return @{ Status = [int]$resp.StatusCode; Body = $text }
    } finally {
        $resp.Close()
    }
}

if ($WhoAmI) {
    $who = New-Object System.Uri($uri, '/_whoami')
    $r = Send-Http $who.AbsoluteUri 'GET' ''
    if ($r.Status -eq 0) { Write-Diag ("The dmx server (" + $uri.Host + ") cannot be reached. Check the network or ask IT."); exit 1 }
    [Console]::Out.WriteLine($r.Body)
    if ($r.Status -ge 400) { exit 1 }
    exit 0
}

$stdin = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), $utf8)
$stdout = New-Object System.IO.StreamWriter([Console]::OpenStandardOutput(), $utf8)
$stdout.NewLine = "`n"
$stdout.AutoFlush = $true

function Write-Line([string]$s) { $stdout.WriteLine($s) }

function New-ErrorLine($id, [string]$message) {
    $o = [ordered]@{ jsonrpc = '2.0'; id = $id; error = [ordered]@{ code = -32000; message = $message } }
    return $json.Serialize($o)
}

function Save-Image($m) {
    $name = [System.IO.Path]::GetFileName([string]$m['file'])  # never a path from the server
    if (-not $name) { throw 'no file name' }
    $ext = [System.IO.Path]::GetExtension($name)
    $base = [System.IO.Path]::GetFileNameWithoutExtension($name)
    $data = [Convert]::FromBase64String([string]$m['data'])
    [void][System.IO.Directory]::CreateDirectory($ExportDir)
    $target = [System.IO.Path]::Combine($ExportDir, $base + $ext)
    $n = 2
    while ([System.IO.File]::Exists($target)) {
        $target = [System.IO.Path]::Combine($ExportDir, ($base + '_' + $n + $ext))
        $n++
    }
    [System.IO.File]::WriteAllBytes($target, $data)
    return ("Saved " + $m['what'] + " of " + $m['source'] + " to " + $target + " (" + [int][math]::Floor($data.Length / 1024) +
            " KB), in the local export folder " + $ExportDir + " on the user's PC. If you work in a sandboxed " +
            "workspace that cannot see this folder, ask the user to add it to the workspace.")
}

function Start-Launch([string[]]$arguments, [string]$path, [string]$verb) {
    if ($OpenWith) {
        $a = @()
        if ($arguments) { $a += $arguments } else { $a += $path; if ($verb -ne 'open') { $a += $verb } }
        & $OpenWith @a | Out-Null
        return
    }
    if ($arguments) {
        $exe = $arguments[0]
        $rest = @($arguments | Select-Object -Skip 1)
        Start-Process -FilePath $exe -ArgumentList $rest | Out-Null
    } elseif ($verb -eq 'open') {
        Start-Process -FilePath $path | Out-Null
    } else {
        Start-Process -FilePath $path -Verb $verb | Out-Null
    }
}

function Get-DefaultApp([string]$ext) {
    try {
        $k = Get-ItemProperty -Path ('Registry::HKEY_CLASSES_ROOT\' + $ext) -ErrorAction Stop
        $prog = $k.'(default)'
        $c = Get-ItemProperty -Path ('Registry::HKEY_CLASSES_ROOT\' + $prog + '\shell\open\command') -ErrorAction Stop
        $cmd = [string]$c.'(default)'
        if ($cmd -match '^\s*"([^"]+)"') { return $Matches[1] }
        if ($cmd -match '^\s*(\S+)') { return $Matches[1] }
    } catch { }
    return ''
}

function Open-Local($m) {
    $path = [string]$m['path']
    $ext = [System.IO.Path]::GetExtension($path).ToLowerInvariant()
    if ($OPENABLE -notcontains $ext) { throw 'This type of file cannot be opened.' }
    $page = $null
    if ($null -ne $m['page']) { $page = [int]$m['page'] }
    if ($ext -eq '.pdf' -and $page -and $page -gt 1) {
        $app = Get-DefaultApp '.pdf'
        $leaf = [System.IO.Path]::GetFileName($app).ToLowerInvariant()
        if ($OpenWith -or @('acrobat.exe', 'acrord32.exe', 'acrord64.exe') -contains $leaf) {
            $exe = $app; if ($OpenWith) { $exe = 'acrobat.exe' }
            Start-Launch @($exe, '/A', ('page=' + $page), $path) '' 'open'
            return "at page $page"
        }
        if (@('msedge.exe', 'chrome.exe', 'firefox.exe') -contains $leaf) {
            $u = (New-Object System.Uri($path)).AbsoluteUri + '#page=' + $page
            Start-Launch @($app, $u) '' 'open'
            return "at page $page"
        }
        Start-Launch $null $path 'open'
        return "(the default PDF viewer cannot jump to a page: go to page $page)"
    }
    if ($ext -eq '.docx' -or $ext -eq '.doc') {
        try {
            Start-Launch $null $path 'OpenAsReadOnly'
            if ($page) { return "read-only (Word cannot jump to a page: go to page ~$page)" }
            return 'read-only'
        } catch { }
    }
    Start-Launch $null $path 'open'
    if (-not $page -or $page -eq 1) { return '' }
    return "(go to page $page)"
}

# Carries out the action of a DMX-RELAY marker; returns the new first text, or $null when the answer is
# not a marker (or the marker could not be understood: then it is passed through unchanged).
function Convert-Marker($msg) {
    $res = $msg['result']
    if ($null -eq $res -or $res -isnot [System.Collections.IDictionary]) { return $false }
    $content = $res['content']
    if ($null -eq $content -or $content.Count -lt 1) { return $false }
    $first = $content[0]
    if ($first -isnot [System.Collections.IDictionary]) { return $false }
    $text = $first['text']
    if ($text -isnot [string] -or -not $text.StartsWith('DMX-RELAY ')) { return $false }
    try {
        $m = $json.DeserializeObject($text.Substring(10))
        if ($m['action'] -eq 'save') { $new = Save-Image $m }
        elseif ($m['action'] -eq 'open') {
            $how = Open-Local $m
            $new = ('Opened ' + $m['path'] + ' ' + $how + '.')
        } else { throw 'unknown action' }
    } catch {
        Write-Diag ('could not carry out a relay action: ' + $_.Exception.GetType().Name)
        $new = 'The action could not be carried out on this PC: ' + $_.Exception.Message
        $res['isError'] = $true
    }
    $first['text'] = $new
    return $true
}

while ($true) {
    $line = $stdin.ReadLine()
    if ($null -eq $line) { break }
    if (-not $line.Trim()) { continue }
    $id = $null
    $hasId = $false
    try {
        $req = $json.DeserializeObject($line)
        if ($req -is [System.Collections.IDictionary] -and $req.ContainsKey('id')) { $hasId = $true; $id = $req['id'] }
    } catch {
        Write-Line (New-ErrorLine $null 'The relay received a message that is not valid JSON.')
        continue
    }
    try {
        $r = Send-Http $uri.AbsoluteUri 'POST' $line
    } catch {
        Write-Diag ('request failed: ' + $_.Exception.GetType().Name)
        $r = @{ Status = 0; Body = '' }
    }
    if ($r.Status -eq 0) {
        if ($hasId) { Write-Line (New-ErrorLine $id ('The dmx server (' + $uri.Host + ') cannot be reached. Check the network or ask IT.')) }
        continue
    }
    if ($r.Status -eq 401) {
        if ($hasId) { Write-Line (New-ErrorLine $id 'Your Windows login was not accepted by the dmx server.') }
        continue
    }
    if ($r.Status -ge 400) {
        if ($hasId) { Write-Line (New-ErrorLine $id ('dmx server error ' + $r.Status + '.')) }
        continue
    }
    $body = $r.Body
    if ($r.Status -eq 202 -or -not $body.Trim()) { continue }
    if ($body.Contains('DMX-RELAY ')) {
        try {
            $msg = $json.DeserializeObject($body)
            if (Convert-Marker $msg) { $body = $json.Serialize($msg) }
        } catch {
            Write-Diag ('could not read the answer: ' + $_.Exception.GetType().Name)
        }
    }
    Write-Line ($body.Trim() -replace "[\r\n]+", ' ')
}
