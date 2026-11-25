# One-line player install (tools/player-install.py), for Windows PowerShell. Published per release as install.ps1 with
# @TAG@ and @SHA@ filled in by `tools/player-install.py --bundle`; never run this template directly.
#
#   & ([scriptblock]::Create((irm https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/@TAG@/install.ps1))) OWNER/REPO
#   ... OWNER/REPO --yes          (take every default, ask nothing)
#
# It finds Python 3.9+ (offering to install Python 3.12 for this user with winget when there is none), downloads this
# release's player-install.zip, checks its sha256 against the one written below, unpacks it under
# %LOCALAPPDATA%\mmvu\player-install\@TAG@\ and runs the installer with your arguments.
$ErrorActionPreference = 'Stop'
$Tag = '@TAG@'
$Sha = '@SHA@'
$Url = if ($env:PLAYER_INSTALL_ZIP) { $env:PLAYER_INSTALL_ZIP } else { "https://github.com/jivinstev/mc-mod-version-upgrade/releases/download/$Tag/player-install.zip" }
$Dir = Join-Path $env:LOCALAPPDATA "mmvu\player-install\$Tag"
$Rest = @($args)

function Find-Python {
    # py -3, python3, python -- each only if it really runs Python 3.9+ (Windows ships python.exe stubs that only open
    # the Microsoft Store)
    foreach ($c in @(@('py', '-3'), @('python3'), @('python'))) {
        try {
            $exe = $c[0]
            $pre = @($c | Select-Object -Skip 1)
            $v = & $exe @pre -c "import sys; print(1 if sys.version_info >= (3, 9) else 0)" 2>$null
            if ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq '1') { return , $c }
        } catch { }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    $auto = ($Rest -contains '--yes') -or ($Rest -contains '-y')
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'player-install needs Python 3.9 or newer: install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.'
        return
    }
    if (-not $auto) {
        $r = Read-Host 'player-install needs Python 3. Install Python 3.12 for this user with winget? [Y/n]'
        if ($r -and $r -notmatch '^[yY]') { Write-Host 'Stopped: nothing was installed.'; return }
    }
    winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $py = Find-Python
    if (-not $py) { Write-Host 'Python was installed: open a new PowerShell window and run the same command again.'; return }
}

if (-not (Test-Path (Join-Path $Dir 'tools\player-install.py'))) {
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $zip = Join-Path $Dir 'player-install.zip'
    if ($Url -match '^https?://') { Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $zip } else { Copy-Item $Url $zip }
    $got = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
    if ($got -ne $Sha) {
        Remove-Item $zip
        Write-Host "player-install: the download's sha256 ($got) is not this release's ($Sha): nothing was installed"
        return
    }
    Expand-Archive -Force -Path $zip -DestinationPath $Dir
    Remove-Item $zip
}
$exe = $py[0]
$pre = @($py | Select-Object -Skip 1)
& $exe @pre (Join-Path $Dir 'tools\player-install.py') @Rest
