# One-line install on native Windows. From a normal (NOT administrator) PowerShell:
#
#   irm https://raw.githubusercontent.com/jivinstev/mc-mod-version-upgrade/main/install.ps1 | iex
#
# This is only the part that has to happen before setup can run: it refuses an elevated window,
# installs Git for Windows and Python if they are missing, clones this repository (or updates the
# clone), and runs `setup.cmd --install-missing`. Everything else -- Claude Code, the JDK, gh, long
# paths -- is installed by tools/setup.py, the same code macOS and Linux run with
# `./setup --install-missing`, so the platforms cannot drift apart. Then it starts Claude Code in
# the clone. Re-running it is safe: what is already there is skipped.
#
# Why each step is here, from the first real-PC run (issue #32):
#   - winget searches every source, and when the Microsoft Store source fails (0x8a15005e, a certificate
#     pinning error behind TLS inspection, a wrong clock or old root certificates) it installs nothing.
#     Neither package needs the Store, so both installs name `--source winget`.
#   - an elevated window puts the per-user installs, ~\bin shims and ~\.claude settings under the admin
#     context, where the user's everyday shell and Claude Code do not look. So it refuses to run elevated.
#   - an install changes PATH in the registry, not in the running shell. The script re-reads it, so the
#     next step, and this window afterwards, see the new commands without opening a new window.
#
# Settings, as environment variables set before the line above (all optional):
#   MCMU_DIR          where the clone goes                 (default: $HOME\mc-mod-version-upgrade)
#   MCMU_REF          branch to clone                      (default: main; an existing clone keeps its own)
#   MCMU_REPO         repository URL, e.g. your fork       (default: this repository)
#   MCMU_SETUP_ARGS   more arguments for setup.cmd, e.g. --migrate
#   MCMU_NO_LAUNCH=1  do not start Claude Code at the end
#   MCMU_ALLOW_ADMIN=1  run elevated anyway (CI runners are elevated; people should not need this)
#
# It runs inside the user's own PowerShell (that is what `| iex` does), so it never calls `exit`, which
# would close their window: it returns, and leaves $global:MCMU_RESULT = 'ok' or 'failed' for CI.
& {
    # Native commands report through exit codes, checked below. With 'Stop' (some hosts set it, CI does),
    # Windows PowerShell 5.1 would turn their stderr into a terminating error.
    $ErrorActionPreference = 'Continue'
    $repo = if ($env:MCMU_REPO) { $env:MCMU_REPO } else { 'https://github.com/jivinstev/mc-mod-version-upgrade' }
    $ref  = if ($env:MCMU_REF)  { $env:MCMU_REF }  else { 'main' }
    $dir  = if ($env:MCMU_DIR)  { $env:MCMU_DIR }  else { Join-Path $HOME 'mc-mod-version-upgrade' }
    $global:MCMU_RESULT = 'failed'

    function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
    function Fail($msg) { Write-Host "install: $msg" -ForegroundColor Red }

    # PATH as a new window would see it (Machine, then User, then Claude Code's ~\.local\bin), keeping
    # anything this session added itself.
    function Update-SessionPath {
        $fresh = @()
        foreach ($scope in 'Machine', 'User') {
            $p = [Environment]::GetEnvironmentVariable('Path', $scope)
            if ($p) { $fresh += $p.Split(';') | Where-Object { $_ } | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_) } }
        }
        $bin = Join-Path $HOME '.local\bin'
        if (Test-Path $bin) { $fresh += $bin }
        $keep = $env:Path.Split(';') | Where-Object { $_ -and ($fresh -notcontains $_) }
        $env:Path = (@($fresh) + @($keep) | Select-Object -Unique) -join ';'
    }

    # Python 3 the way setup.cmd finds it: python3, python, py -3 -- each only if it really runs
    # Python 3 (Windows ships python.exe/python3.exe stubs that only open the Microsoft Store).
    function Test-Python {
        foreach ($c in @(@('python3'), @('python'), @('py', '-3'))) {
            if (Get-Command $c[0] -ErrorAction SilentlyContinue) {
                $pyArgs = @($c | Select-Object -Skip 1) + @('-c', 'import sys; sys.exit(sys.version_info[0] != 3)')
                & $c[0] @pyArgs 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { return $true }
            }
        }
        return $false
    }

    function Install-WithWinget($what, $id, [scriptblock]$present) {
        if (& $present) { Write-Host "    $what`: already installed"; return $true }
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Fail "$what is missing and winget is not available. Update 'App Installer' from the Microsoft Store (or install $what yourself), then run this again."
            return $false
        }
        Say "Installing $what (winget: $id). Windows may ask for permission: click Yes."
        winget install -e --source winget --accept-package-agreements --accept-source-agreements --id $id
        Update-SessionPath
        if (& $present) { return $true }
        Fail "$what did not install (winget exit code $LASTEXITCODE). Try it by hand: winget install -e --source winget --id $id"
        return $false
    }

    # ---- 0. not elevated ---------------------------------------------------------------------------
    if ($env:OS -ne 'Windows_NT') { Fail 'this installer is for Windows. On macOS or Linux: git clone the repository, then ./setup --install-missing'; return }
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) -and $env:MCMU_ALLOW_ADMIN -ne '1') {
        Fail 'this PowerShell is running as Administrator. Close it, open PowerShell from the Start menu with a plain click (not "Run as administrator"), and paste the line again.'
        Fail '(Installed as Administrator, the tools and settings land where your normal shell and Claude Code do not look.)'
        return
    }

    # ---- 1. what setup itself needs ----------------------------------------------------------------
    Say 'Checking Git and Python'
    if (-not (Install-WithWinget 'Git for Windows' 'Git.Git' { [bool](Get-Command git -ErrorAction SilentlyContinue) })) { return }
    if (-not (Install-WithWinget 'Python 3.12' 'Python.Python.3.12' { Test-Python })) { return }

    # ---- 2. the repository ------------------------------------------------------------------------
    if (Test-Path (Join-Path $dir '.git')) {
        Say "Updating $dir"
        git -C $dir pull --ff-only
        if ($LASTEXITCODE -ne 0) { Write-Host "    (could not fast-forward $dir; using it as it is)" -ForegroundColor Yellow }
    } elseif (Test-Path $dir) {
        Fail "$dir exists and is not a git clone. Move it aside, or set MCMU_DIR to another folder, and run this again."
        return
    } else {
        Say "Cloning into $dir"
        git clone --branch $ref $repo $dir
        if ($LASTEXITCODE -ne 0) { Fail "git clone of $repo ($ref) failed"; return }
    }

    # ---- 3. setup: Claude Code, the JDK, gh and long paths are installed from here ----------------
    Say 'Running setup'
    Set-Location $dir
    $setupArgs = @('--install-missing')
    if ($env:MCMU_SETUP_ARGS) { $setupArgs += $env:MCMU_SETUP_ARGS.Split(' ') | Where-Object { $_ } }
    & (Join-Path $dir 'setup.cmd') @setupArgs
    $setupCode = $LASTEXITCODE
    Update-SessionPath          # what setup installed, visible in this window too
    if ($setupCode -ne 0) {
        Fail "setup reported a problem (see the PROBLEM lines above). Fix it, then run .\setup.cmd --install-missing in $dir"
        return
    }
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Fail 'Claude Code is still not installed (see setup''s notes above). Install it with: irm https://claude.ai/install.ps1 | iex'
        return
    }
    $global:MCMU_RESULT = 'ok'

    # ---- 4. Claude Code ---------------------------------------------------------------------------
    if ($env:MCMU_NO_LAUNCH -eq '1') {
        Say "Done. To start: cd $dir; claude"
        return
    }
    Say "Starting Claude Code. Next time: open PowerShell, then  cd $dir; claude"
    claude
}
