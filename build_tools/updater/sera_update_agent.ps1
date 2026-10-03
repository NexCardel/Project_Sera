# Amas Sera update agent.
#
# Runs as SYSTEM from the "Amas Sera\Updater" scheduled task (at boot and every 30 minutes),
# so an update installs without a UAC prompt and without anyone clicking anything.
#
#   1. Read version.json on GitHub main. Nothing to do unless it names a newer version than
#      the one installed (Inno's DisplayVersion).
#   2. Download that release's installer into %ProgramData%\AmasSera\updater (SYSTEM/admins
#      write, users read) and check its SHA-256 against version.json. The URL must be a
#      release asset of this repo; a version.json without a sha256 is ignored.
#   3. Write pending.json. A running app reads it and closes itself at the next idle moment
#      (see core/update_agent.py), handing over to a helper that reopens it afterwards.
#   4. Once no Amas_Sera.exe is running, install silently and write last_result.json.
#      At boot the app is not running yet, so a staged update always lands on a restart.

param(
    [string]$VersionUrl = "https://raw.githubusercontent.com/NexCardel/Project_Sera/main/version.json",
    [string]$StateDir = (Join-Path $env:ProgramData "AmasSera\updater"),
    [int]$WaitMinutes = 25,
    [string]$InstalledVersion = "",   # testing only: pretend this version is installed
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest's progress bar slows downloads ~10x
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$AppId = "{D37F8E9C-4A2B-4F1E-9C8A-1B3D5E7F9A0B}_is1"
$AssetPrefix = "https://github.com/NexCardel/Project_Sera/releases/download/"
$ProcessName = "Amas_Sera"
$MaxAttemptsPerVersion = 3

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$LogPath = Join-Path $StateDir "agent.log"
$PendingPath = Join-Path $StateDir "pending.json"
$InstallingPath = Join-Path $StateDir "installing.flag"
$ResultPath = Join-Path $StateDir "last_result.json"
$AttemptsPath = Join-Path $StateDir "attempts.json"

function Write-Log([string]$Message) {
    if ((Test-Path $LogPath) -and (Get-Item $LogPath).Length -gt 1MB) {
        Move-Item -Force $LogPath "$LogPath.1"
    }
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" | Add-Content -Path $LogPath -Encoding UTF8
}

function Write-Json($Path, $Object) {
    $tmp = "$Path.tmp"
    $Object | ConvertTo-Json -Compress | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force $tmp $Path
}

function ConvertTo-VersionTuple([string]$Text) {
    $parts = @()
    foreach ($p in ($Text.Trim().TrimStart("v", "V") -split "\.")) {
        $n = 0
        [void][int]::TryParse($p, [ref]$n)
        $parts += $n
    }
    while ($parts.Count -lt 4) { $parts += 0 }
    return $parts
}

function Test-Newer([string]$Candidate, [string]$Current) {
    $a = ConvertTo-VersionTuple $Candidate
    $b = ConvertTo-VersionTuple $Current
    for ($i = 0; $i -lt 4; $i++) {
        if ($a[$i] -gt $b[$i]) { return $true }
        if ($a[$i] -lt $b[$i]) { return $false }
    }
    return $false
}

function Get-InstalledVersion {
    foreach ($root in @("HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")) {
        $key = Join-Path $root $AppId
        if (Test-Path $key) {
            $v = (Get-ItemProperty $key -ErrorAction SilentlyContinue).DisplayVersion
            if ($v) { return [string]$v }
        }
    }
    return $null
}

function Get-FileSha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
}

function Test-AppRunning {
    return [bool](Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)
}

function Get-Attempts([string]$Version) {
    if (-not (Test-Path $AttemptsPath)) { return 0 }
    try {
        $a = Get-Content $AttemptsPath -Raw | ConvertFrom-Json
        if ($a.version -eq $Version -and $a.date -eq (Get-Date -Format "yyyy-MM-dd")) { return [int]$a.count }
    } catch { }
    return 0
}

try {
    $installed = if ($InstalledVersion) { $InstalledVersion } else { Get-InstalledVersion }
    if (-not $installed) {
        Write-Log "Amas Sera is not installed (no uninstall key); nothing to do."
        exit 0
    }

    $feed = Invoke-RestMethod -Uri "$VersionUrl`?_cb=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" `
        -Headers @{ "Cache-Control" = "no-cache"; "User-Agent" = "AmasSera-UpdateAgent/$installed" } -TimeoutSec 30
    $target = [string]$feed.version
    if (-not $target -or -not (Test-Newer $target $installed)) {
        if (Test-Path $PendingPath) { Remove-Item -Force $PendingPath }
        exit 0
    }

    $url = [string]$feed.download_url
    $sha = ([string]$feed.sha256).ToLowerInvariant()
    if ($sha -notmatch "^[0-9a-f]{64}$") {
        Write-Log "v$target is published without a valid sha256; not installing it."
        exit 0
    }
    if (-not $url.StartsWith("$AssetPrefix" + "v$target/") -or -not $url.EndsWith(".exe")) {
        Write-Log "v$target download_url is not a release asset of this repo: $url"
        exit 0
    }

    $attempts = Get-Attempts $target
    if ($attempts -ge $MaxAttemptsPerVersion) {
        Remove-Item -Force $PendingPath -ErrorAction SilentlyContinue
        Write-Log "v$target already failed $attempts times today; next try tomorrow."
        exit 0
    }

    # Stage the installer (re-used across runs once its hash checks out).
    $staged = Join-Path $StateDir "Amas_Sera_Setup_v$target.exe"
    if (-not ((Test-Path $staged) -and (Get-FileSha256 $staged) -eq $sha)) {
        Get-ChildItem $StateDir -Filter "Amas_Sera_Setup_v*.exe*" | Remove-Item -Force
        $part = "$staged.part"
        Write-Log "Downloading v$target (installed: v$installed)"
        $web = New-Object System.Net.WebClient   # streams to disk; Invoke-WebRequest is ~10x slower on PS 5.1
        $web.Headers.Add("User-Agent", "AmasSera-UpdateAgent/$installed")
        try { $web.DownloadFile($url, $part) } finally { $web.Dispose() }
        $got = Get-FileSha256 $part
        if ($got -ne $sha) {
            Remove-Item -Force $part
            Write-Log "v$target hash mismatch (got $got, expected $sha); discarded."
            exit 1
        }
        Move-Item -Force $part $staged
        Write-Log "v$target downloaded and verified."
    }

    # staged_at is kept across runs: the app goes ahead sooner once an update has waited long.
    $stagedAt = (Get-Date).ToString("o")
    if (Test-Path $PendingPath) {
        try {
            $prev = Get-Content $PendingPath -Raw | ConvertFrom-Json
            if ($prev.version -eq $target -and $prev.staged_at) { $stagedAt = [string]$prev.staged_at }
        } catch { }
    }
    Write-Json $PendingPath @{ version = $target; staged_at = $stagedAt }

    # The app closes itself when idle once it sees pending.json; wait for that.
    $deadline = (Get-Date).AddMinutes($WaitMinutes)
    while ((Test-AppRunning) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 10 }
    if (Test-AppRunning) {
        Write-Log "v$target staged; the app is still in use, will try again on the next run."
        exit 0
    }

    if ($DryRun) {
        Write-Log "Dry run: would install v$target from $staged"
        exit 0
    }

    Write-Json $AttemptsPath @{ version = $target; date = (Get-Date -Format "yyyy-MM-dd"); count = $attempts + 1 }
    Set-Content -Path $InstallingPath -Value $target -Encoding UTF8
    Write-Log "Installing v$target"
    $installLog = Join-Path $StateDir "install_v$target.log"
    $proc = Start-Process -FilePath $staged -Wait -PassThru -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/LOG=`"$installLog`"")
    Remove-Item -Force $InstallingPath -ErrorAction SilentlyContinue

    $now = Get-InstalledVersion
    $ok = ($proc.ExitCode -eq 0) -and ($now -eq $target)
    Write-Json $ResultPath @{ version = $target; ok = $ok; exit_code = $proc.ExitCode; installed = $now; finished_at = (Get-Date).ToString("o") }
    if ($ok) {
        Remove-Item -Force $PendingPath, $staged, $AttemptsPath -ErrorAction SilentlyContinue
        Write-Log "Installed v$target."
    } else {
        Remove-Item -Force $PendingPath -ErrorAction SilentlyContinue
        Write-Log "Install of v$target failed (exit $($proc.ExitCode), installed now v$now); see $installLog"
        exit 1
    }
} catch {
    Remove-Item -Force $InstallingPath, $PendingPath -ErrorAction SilentlyContinue
    Write-Log "Error: $($_.Exception.Message)"
    exit 1
}
