# One-line player install (tools/player-install.py), for Windows PowerShell (5.1 and 7). Published per release as
# install.ps1 with @TAG@, @SHA@ and the portable-Python pins (player-install/portable-python.txt) filled in by
# `tools/player-install.py --bundle`; never run this template directly.
#
#   & ([scriptblock]::Create((irm https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/@TAG@/install.ps1))) OWNER/REPO
#   ... OWNER/REPO --yes          (take every default, ask nothing)
#   ... OWNER/REPO --keep-python  (keep the portable Python, if one was downloaded, for the next run)
#
# It uses a Python 3.9+ that is already here (py -3, python3, python; never the Microsoft Store's python.exe aliases
# under ...\Microsoft\WindowsApps, which open the Store). When there is none, it downloads python.org's embeddable
# Python (pinned below by URL and sha256, checked before anything is unpacked) into a temporary folder, uses it for
# this install only and removes it afterwards: nothing is installed system-wide and no admin rights are needed. Then it
# downloads this release's player-install.zip, checks its sha256 against the one written below, unpacks it under
# %LOCALAPPDATA%\mmvu\player-install\@TAG@\ and runs the installer with your arguments (--keep-python is the
# bootstrap's own and is not passed on).
#
# Test hooks (CI only; a player sets none): PLAYER_INSTALL_ZIP and PLAYER_INSTALL_PYTHON_FILE are local files used in
# place of the two downloads (the sha256s below still decide).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'      # Windows PowerShell 5.1's progress bar makes a download many times slower
$Tag = '@TAG@'
$Sha = '@SHA@'
# platform, size in MB, sha256, URL
$Pins = @'
@PYTHON_PINS@
'@
$Url = if ($env:PLAYER_INSTALL_ZIP) { $env:PLAYER_INSTALL_ZIP } else { "https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/$Tag/player-install.zip" }
$Home_ = Join-Path $env:LOCALAPPDATA 'mmvu'
$Dir = Join-Path $Home_ "player-install\$Tag"
$Keep = $false
$Rest = @()
foreach ($a in $args) { if ("$a" -eq '--keep-python') { $Keep = $true } else { $Rest += $a } }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

function Test-Python($exe, $pre) {
    try {
        $v = & $exe @pre -c "import sys; print(1 if sys.version_info >= (3, 9) else 0)" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq '1')
    } catch { return $false }
}

function Find-Python {
    # py -3, python3, python -- each only if it really runs Python 3.9+, and never a Store alias
    foreach ($c in @(@('py', '-3'), @('python3'), @('python'))) {
        $cmd = Get-Command $c[0] -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $cmd) { continue }
        if ($cmd.Path -match '\\Microsoft\\WindowsApps\\') { continue }
        $pre = @($c | Select-Object -Skip 1)
        if (Test-Python $cmd.Path $pre) { return , (@($cmd.Path) + $pre) }
    }
    return $null
}

$py = Find-Python
$work = $null
if (-not $py) {
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    $plat = if ($arch -eq 'ARM64') { 'windows-arm64' } else { 'windows-amd64' }
    $pin = $null
    foreach ($l in ($Pins -split "`n")) {
        $f = @($l.Trim() -split '\s+')
        if ($f.Count -eq 4 -and $f[0] -eq $plat) { $pin = $f; break }
    }
    if (-not $pin) {
        Write-Host "player-install needs Python 3.9 or newer: install it from https://www.python.org/downloads/ (tick `"Add python.exe to PATH`"), then run this again."
        return
    }
    $mb = $pin[1]; $psha = $pin[2]; $purl = $pin[3]
    $kept = Join-Path $Home_ ('python\' + $psha.Substring(0, 16))
    if (Test-Python (Join-Path $kept 'python.exe') @()) {
        $py = @((Join-Path $kept 'python.exe'))          # kept by an earlier --keep-python
    } else {
        if ($Keep) {
            Write-Host "No Python 3.9+ here: downloading a portable Python (about $mb MB) for this install; --keep-python keeps it in $kept."
            if (Test-Path $kept) { Remove-Item -Recurse -Force $kept }
            $work = $kept
        } else {
            Write-Host "No Python 3.9+ here: downloading a portable Python (about $mb MB) just for this install; it is removed afterwards."
            $work = Join-Path ([IO.Path]::GetTempPath()) ('mmvu-python-' + [guid]::NewGuid().ToString('N'))
        }
        New-Item -ItemType Directory -Force -Path $work | Out-Null
        $zip = Join-Path $work 'python.zip'
        if ($env:PLAYER_INSTALL_PYTHON_FILE) { Copy-Item $env:PLAYER_INSTALL_PYTHON_FILE $zip } else { Invoke-WebRequest -UseBasicParsing -Uri $purl -OutFile $zip }
        $got = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
        if ($got -ne $psha) {
            Remove-Item -Recurse -Force $work
            Write-Host "player-install: the portable Python's sha256 ($got) is not the pinned one ($psha): nothing was run, nothing was installed"
            $global:LASTEXITCODE = 1
            return
        }
        Expand-Archive -Force -Path $zip -DestinationPath $work
        Remove-Item $zip
        # The embeddable build's pythonXY._pth fixes sys.path and leaves out the script's own folder: the installer adds
        # its tools folder itself, and each tool loads its neighbours by path.
        $py = @((Join-Path $work 'python.exe'))
        if (-not (Test-Python $py[0] @())) {
            Remove-Item -Recurse -Force $work
            Write-Host "player-install: the portable Python does not run on this machine ($plat): install Python 3.9+ from https://www.python.org/downloads/, then run this again."
            $global:LASTEXITCODE = 1
            return
        }
    }
}

try {
    if (-not (Test-Path (Join-Path $Dir 'tools\player-install.py'))) {
        New-Item -ItemType Directory -Force -Path $Dir | Out-Null
        $zip = Join-Path $Dir 'player-install.zip'
        if ($Url -match '^https?://') { Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $zip } else { Copy-Item $Url $zip }
        $got = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
        if ($got -ne $Sha) {
            Remove-Item $zip
            Write-Host "player-install: the download's sha256 ($got) is not this release's ($Sha): nothing was installed"
            $global:LASTEXITCODE = 1
            return
        }
        Expand-Archive -Force -Path $zip -DestinationPath $Dir
        Remove-Item $zip
    }
    $exe = $py[0]
    $pre = @($py | Select-Object -Skip 1)
    & $exe @pre (Join-Path $Dir 'tools\player-install.py') @Rest
    $rc = $LASTEXITCODE
} finally {
    if ($work -and -not $Keep -and (Test-Path $work)) { Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue }
}
$global:LASTEXITCODE = $rc
