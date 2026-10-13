# One-line install on native Windows. From a normal (NOT administrator) PowerShell:
#
#   irm https://raw.githubusercontent.com/jivinstev/mc-mod-version-upgrade/main/install.ps1 | iex
#
# This is only the part that has to happen before setup can run: it refuses an elevated window,
# installs Git for Windows and Python if they are missing, asks where the clone should go (default:
# the folder you ran it from), clones this repository there (or updates an existing clone), and runs
# `setup.cmd --install-missing`. Everything else -- Claude Code, the JDK and gh (with winget), long
# paths -- is done by tools/setup.py, the same setup macOS and Linux run as `./setup`, so the platforms
# cannot drift apart. Then it starts Claude Code in the clone. Re-running it is safe: what is already
# there is skipped.
#
# Why each step is here, from the first real-PC run (issue #32):
#   - winget searches every source, and when the Microsoft Store source fails (0x8a15005e, a certificate
#     pinning error behind TLS inspection, a wrong clock or old root certificates) it installs nothing.
#     Neither package needs the Store, so both installs name `--source winget`.
#   - an elevated window puts the per-user installs, ~\bin shims and ~\.claude settings under the admin
#     context, where the user's everyday shell and Claude Code do not look. So it refuses to run elevated,
#     and says what it checked: the Administrators role, and the window's integrity level (High = elevated).
#   - people keep their code in a folder of their own (~\dev, D:\src). The clone goes where they say,
#     never to a place they were not asked about.
#   - an install changes PATH in the registry, not in the running shell. The script re-reads it, so the
#     next step, and this window afterwards, see the new commands without opening a new window.
#
# Settings, as environment variables set before the line above (all optional):
#   MCMU_DIR          the clone's folder; skips the question (default: ask, offering
#                     <the folder you ran it from>\mc-mod-version-upgrade)
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
    $name = 'mc-mod-version-upgrade'
    $global:MCMU_RESULT = 'failed'

    function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
    function Fail($msg) {
        Write-Host ''
        Write-Host '==================================================================' -ForegroundColor Red
        Write-Host "STOPPED: $msg" -ForegroundColor Red
        Write-Host '==================================================================' -ForegroundColor Red
    }

    # Elevated = in the Administrators role, OR a High/System integrity level. The role check alone can
    # miss an elevated window (a token the role check does not see as admin); the integrity level is
    # what "Run as administrator" actually changes.
    function Test-Elevated {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $role = ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        $groups = (whoami /groups 2>$null) | Out-String
        $high = ($groups -match 'S-1-16-12288') -or ($groups -match 'S-1-16-16384')
        Write-Host "    user: $($id.Name); Administrators role: $(if ($role) {'yes'} else {'no'}); integrity: $(if ($high) {'High (elevated)'} elseif ($groups) {'Medium (normal)'} else {'unknown'})"
        return ($role -or $high)
    }

    # Where the clone goes. MCMU_DIR wins; a clone you are standing in is used as it is; otherwise ask,
    # offering <this folder>\mc-mod-version-upgrade -- or <home>\... when this folder is a Windows
    # system folder (elevated windows, and some normal ones, open in C:\WINDOWS\system32).
    function Get-CloneDir {
        if ($env:MCMU_DIR) { return [Environment]::ExpandEnvironmentVariables($env:MCMU_DIR) }
        $here = (Get-Location).ProviderPath
        if ((Test-Path (Join-Path $here '.git')) -and (Test-Path (Join-Path $here 'tools\setup.py')) -and (Test-Path (Join-Path $here 'setup.cmd'))) {
            Write-Host "    using the clone you are in: $here"
            return $here
        }
        $parent = $here
        if (-not $parent -or ($env:windir -and $parent.StartsWith($env:windir, [StringComparison]::OrdinalIgnoreCase))) { $parent = $HOME }
        $suggest = Join-Path $parent $name
        Write-Host ''
        Write-Host "Where should the $name folder go? Press Enter for the suggestion, or type a folder."
        Write-Host '(A folder that already has other things in it gets the clone inside it.)'
        $answer = Read-Host "Folder [$suggest]"
        if (-not $answer) { return $suggest }
        $answer = $answer.Trim().Trim('"')
        if ($answer.StartsWith('~')) { $answer = $HOME + $answer.Substring(1) }
        $answer = [Environment]::ExpandEnvironmentVariables($answer)
        if (-not [IO.Path]::IsPathRooted($answer)) { $answer = Join-Path $here $answer }
        # Typed an existing folder of their own (C:\Users\me\dev)? Put the clone in it, as `git clone` would.
        if ((Test-Path $answer) -and -not (Test-Path (Join-Path $answer '.git')) -and (Get-ChildItem -Force $answer | Select-Object -First 1)) {
            $answer = Join-Path $answer $name
        }
        return $answer
    }

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
    if ($env:OS -ne 'Windows_NT') { Fail 'this installer is for Windows. On macOS or Linux: git clone the repository, then ./setup'; return }
    Say 'Checking this window'
    if ((Test-Elevated) -and $env:MCMU_ALLOW_ADMIN -ne '1') {
        Fail ("this PowerShell is running as Administrator. Nothing was installed.`n" +
              "Close it, open PowerShell from the Start menu with a plain click (not `"Run as administrator`"),`n" +
              "and paste the line again. (Installed as Administrator, the tools and settings land where your`n" +
              "normal shell and Claude Code do not look.)")
        return
    }

    # ---- 1. what setup itself needs ----------------------------------------------------------------
    Say 'Checking Git and Python'
    if (-not (Install-WithWinget 'Git for Windows' 'Git.Git' { [bool](Get-Command git -ErrorAction SilentlyContinue) })) { return }
    if (-not (Install-WithWinget 'Python 3.12' 'Python.Python.3.12' { Test-Python })) { return }

    # ---- 2. the repository ------------------------------------------------------------------------
    $dir = Get-CloneDir
    if (Test-Path (Join-Path $dir '.git')) {
        Say "Updating $dir"
        git -C $dir pull --ff-only
        if ($LASTEXITCODE -ne 0) { Write-Host "    (could not fast-forward $dir; using it as it is)" -ForegroundColor Yellow }
    } elseif (Test-Path $dir) {
        Fail "$dir exists and is not a git clone. Move it aside or pick another folder, and run this again."
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
        Fail 'Claude Code is still not installed (see setup''s notes above). Install it with: winget install -e --source winget --id Anthropic.ClaudeCode'
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
