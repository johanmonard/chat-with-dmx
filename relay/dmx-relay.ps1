# dmx-relay.ps1 - stdio MCP server that forwards each JSON-RPC line to the central dmx server
# (http(s)://host:port/<world>/mcp), signed in as the logged-on Windows user (NTLM), and carries out
# on this PC the actions the server hands back (DMX-RELAY markers: save an image, open a document).
# Windows PowerShell 5.1, no modules. stdout carries JSON-RPC lines only; diagnostics go to stderr.
param(
    [string]$Url,
    [string]$ExportDir = 'C:\dmx-rag\exports',
    [string]$OpenWith = '',
    [string]$AllowedHosts = $env:DMX_ALLOWED_HOSTS,   # UNC hosts a document may be opened from (empty = none)
    [switch]$WhoAmI
)

$ErrorActionPreference = 'Stop'
[System.Net.ServicePointManager]::Expect100Continue = $false
Add-Type -AssemblyName System.Web.Extensions
$json = New-Object System.Web.Script.Serialization.JavaScriptSerializer
$json.MaxJsonLength = [int]::MaxValue
$json.RecursionLimit = 200
$utf8 = New-Object System.Text.UTF8Encoding($false)

Add-Type -Namespace Dmx -Name Assoc -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shlwapi.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
public static extern uint AssocQueryString(uint flags, uint str, string assoc, string extra, System.Text.StringBuilder pszOut, ref uint pcchOut);
'@

$IMAGE_EXTS = @('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp', '.emf', '.wmf')
$MAX_IMAGE_BYTES = 12 * 1024 * 1024
$OPENABLE =@('.pdf', '.docx', '.doc', '.pptx', '.ppt', '.md', '.txt', '.xlsx', '.xlsm', '.xls', '.one',
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
    $req.AllowAutoRedirect = $false
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
    $ext = [System.IO.Path]::GetExtension($name).ToLowerInvariant()
    if ($IMAGE_EXTS -notcontains $ext) { throw 'Only an image file can be saved.' }
    $b64 = [string]$m['data']
    if ($b64.Length -gt ([int](($MAX_IMAGE_BYTES * 4) / 3) + 16)) { throw 'This picture is too large to save.' }
    $data = [Convert]::FromBase64String($b64)
    if ($data.Length -gt $MAX_IMAGE_BYTES) { throw 'This picture is too large to save.' }
    $base = [System.IO.Path]::GetFileNameWithoutExtension($name)
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

# One argument of a command line: quoted when it holds white space; refuses a quote (it could inject
# switches) and a trailing backslash (it would escape the closing quote).
function ConvertTo-CmdArg([string]$a) {
    if ($a.Contains('"') -or $a.EndsWith('\')) { throw 'This file cannot be opened: unsafe characters in its path.' }
    if ($a.Length -eq 0 -or $a -match '\s') { return '"' + $a + '"' }
    return $a
}

function Build-CommandLine([string[]]$parts) {
    return (($parts | ForEach-Object { ConvertTo-CmdArg $_ }) -join ' ')
}

function Start-Launch([string[]]$arguments, [string]$path, [string]$verb) {
    if ($arguments) {
        $exe = $arguments[0]
        $rest = Build-CommandLine @($arguments | Select-Object -Skip 1)
        if ($OpenWith) {
            # the test launcher receives the exact command line that would be started
            & $OpenWith ((ConvertTo-CmdArg $exe) + ' ' + $rest) | Out-Null
            return
        }
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $exe
        $psi.Arguments = $rest
        $psi.UseShellExecute = $false
        [void][System.Diagnostics.Process]::Start($psi)
        return
    }
    if ($OpenWith) {
        $a = @($path)
        if ($verb -ne 'open') { $a += $verb }
        & $OpenWith @a | Out-Null
    } elseif ($verb -eq 'open') {
        Start-Process -FilePath $path | Out-Null
    } else {
        Start-Process -FilePath $path -Verb $verb | Out-Null
    }
}

# The program the user's file association (UserChoice included) names for an extension, like the local
# tool (AssocQueryString ASSOCSTR_EXECUTABLE).
function Get-DefaultApp([string]$ext) {
    $sb = New-Object System.Text.StringBuilder 1024
    $len = [uint32]1024
    if ([Dmx.Assoc]::AssocQueryString(0, 2, $ext, $null, $sb, [ref]$len) -ne 0) { return '' }
    return $sb.ToString()
}

# Throws unless the path is an existing file on a drive letter or on an allowed UNC host: never a URL,
# a protocol handler, a relative path, a stream or a path with a quote.
function Assert-OpenablePath([string]$path) {
    $refuse = 'This file cannot be opened'
    if ($path.Contains('"') -or $path.Contains('/')) { throw ($refuse + ': unsafe characters in its path.') }
    if ($path -match '^[A-Za-z]:\\') {
        if ($path.IndexOf(':', 2) -ge 0) { throw ($refuse + ': unsafe characters in its path.') }
    } elseif ($path -match '^\\\\([^\\/:]+)\\') {
        if ($path.IndexOf(':') -ge 0) { throw ($refuse + ': unsafe characters in its path.') }
        # Fail closed: only a plain DNS-style host name that is exactly in the list; an empty list (or the
        # manifest placeholder that was never substituted) refuses every network path. No lookup is made
        # before this check passes.
        $hostName = $Matches[1].TrimEnd('.')
        $list = $AllowedHosts
        if ($list -like '*${user_config.*') { $list = '' }
        $allowed = @($list -split '[,;\s]+' | ForEach-Object { $_.TrimEnd('.') } | Where-Object { $_ })
        if ($hostName -notmatch '^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*$' -or
            -not ($allowed | Where-Object { $_ -ieq $hostName })) {
            throw ($refuse + ': the server host is not allowed.')
        }
    } else {
        throw ($refuse + ': it is not a drive or network path.')
    }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw ($refuse + ': it was not found on this PC.') }
}

function Open-Local($m) {
    $path = [string]$m['path']
    Assert-OpenablePath $path
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

# Carries out the marker of a tool answer that is the answer to a forwarded export_image / open_document
# request ($expected: 'save' / 'open'). Returns $true when the message was rewritten; $false (message left
# as it is, plain text for Claude) when it is not such a marker, is the other action, or is an error result.
function Convert-Marker($msg, [string]$expected) {
    $res = $msg['result']
    if ($null -eq $res -or $res -isnot [System.Collections.IDictionary]) { return $false }
    if ($res['isError'] -eq $true) { return $false }
    $content = $res['content']
    if ($null -eq $content -or $content.Count -lt 1) { return $false }
    $first = $content[0]
    if ($first -isnot [System.Collections.IDictionary]) { return $false }
    $text = $first['text']
    if ($text -isnot [string] -or -not $text.StartsWith('DMX-RELAY ')) { return $false }
    try {
        $m = $json.DeserializeObject($text.Substring(10))
        if ($m['action'] -ne $expected) { return $false }
        if ($expected -eq 'save') { $new = Save-Image $m }
        else {
            $how = Open-Local $m
            $new = ('Opened ' + $m['path'] + ' ' + $how + '.')
        }
    } catch {
        Write-Diag ('could not carry out a relay action: ' + $_.Exception.GetType().Name)
        $new = 'The action could not be carried out on this PC: ' + $_.Exception.Message
        $res['isError'] = $true
    }
    $first['text'] = $new
    return $true
}

function Get-IdKey($id) { return ($id.GetType().Name + ':' + $id) }

# Requests forwarded as export_image / open_document, waiting for their answer: id key -> expected action.
$pending = @{}

while ($true) {
    $line = $stdin.ReadLine()
    if ($null -eq $line) { break }
    if (-not $line.Trim()) { continue }
    $id = $null
    $hasId = $false
    $key = $null
    try {
        $req = $json.DeserializeObject($line)
        if ($req -is [System.Collections.IDictionary] -and $req.ContainsKey('id') -and $null -ne $req['id']) {
            $hasId = $true; $id = $req['id']; $key = Get-IdKey $id
            $p = $req['params']
            if ($req['method'] -eq 'tools/call' -and $p -is [System.Collections.IDictionary]) {
                if ($p['name'] -eq 'export_image') { $pending[$key] = 'save' }
                elseif ($p['name'] -eq 'open_document') { $pending[$key] = 'open' }
            }
        }
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
    $expected = $null
    if ($hasId -and $pending.ContainsKey($key)) { $expected = $pending[$key]; $pending.Remove($key) }
    if ($r.Status -eq 0) {
        if ($hasId) { Write-Line (New-ErrorLine $id ('The dmx server (' + $uri.Host + ') cannot be reached. Check the network or ask IT.')) }
        continue
    }
    if ($r.Status -eq 401) {
        if ($hasId) { Write-Line (New-ErrorLine $id 'Your Windows login was not accepted by the dmx server.') }
        continue
    }
    if ($r.Status -ge 300) {
        if ($hasId) { Write-Line (New-ErrorLine $id ('dmx server error ' + $r.Status + '.')) }
        continue
    }
    $body = $r.Body
    if ($r.Status -eq 202 -or -not $body.Trim()) { continue }
    if ($expected -and $body.Contains('DMX-RELAY ')) {
        try {
            $msg = $json.DeserializeObject($body)
            # only the answer to that very request (same id) is obeyed
            if ($msg -is [System.Collections.IDictionary] -and $null -ne $msg['id'] -and (Get-IdKey $msg['id']) -eq $key) {
                if (Convert-Marker $msg $expected) { $body = $json.Serialize($msg) }
            }
        } catch {
            Write-Diag ('could not read the answer: ' + $_.Exception.GetType().Name)
        }
    }
    Write-Line ($body.Trim() -replace "[\r\n]+", ' ')
}
