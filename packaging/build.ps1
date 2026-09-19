<#
.SYNOPSIS
    Builds Floating Clock into a standalone Windows executable.

.DESCRIPTION
    Generates the icon, runs PyInstaller, then compiles Setup.iss into a
    double-clickable FloatingClock-Setup-<version>.exe when Inno Setup 6 is
    installed. The default is a one-folder build: it starts instantly and is
    what the installer ships. -OneFile
    produces a single portable .exe instead, which unpacks to %TEMP% on every
    launch and so starts a second or two slower.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -OneFile
#>
[CmdletBinding()]
param(
    [switch]$OneFile,
    [switch]$SkipIcon
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $here
# PyInstaller has to import the package as `floating_clock`, but the checkout
# is called floating-clock and a hyphen is not a module name, so the sources
# are staged under a correctly named folder and that is what goes on the path.
# This used to point two levels up at a folder that only existed in the old
# monorepo, which is why a fresh clone could not be built at all.
$stageRoot = Join-Path $buildDir "pkg"
$packageDir = Join-Path $stageRoot "floating_clock"
$buildDir = Join-Path $here "build"
$distDir = Join-Path $here "dist"
$iconPath = Join-Path $buildDir "FloatingClock.ico"
$entry = Join-Path $buildDir "entry.py"

function Get-PythonExe {
    foreach ($candidate in @("python", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw "Python was not found on PATH. Install Python 3.10+ and retry."
}

$python = Get-PythonExe
Write-Host "Python:     $python" -ForegroundColor Cyan
Write-Host "Sources:    $repoRoot" -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
if (Test-Path $packageDir) { Remove-Item -Recurse -Force $packageDir }
New-Item -ItemType Directory -Force -Path $packageDir | Out-Null
Copy-Item -Path (Join-Path $repoRoot "*.py") -Destination $packageDir -Force
Copy-Item -Path (Join-Path $repoRoot "qt") -Destination $packageDir -Recurse -Force
Get-ChildItem -Path $packageDir -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "`n[1/5] Checking build dependencies..." -ForegroundColor Yellow
& $python -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('PIL') and u.find_spec('PyInstaller') and u.find_spec('win32com') and u.find_spec('dateutil') and u.find_spec('pychromecast') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "      Installing build dependencies..." -ForegroundColor DarkGray
    # pychromecast is what plays the adhan on a Google or Nest speaker;
    # without it that one feature says so and the rest still works.
    & $python -m pip install --quiet --upgrade pillow pyinstaller pywin32 python-dateutil pychromecast
    if ($LASTEXITCODE -ne 0) { throw "Could not install build dependencies." }
}

Write-Host "[2/5] Generating icon..." -ForegroundColor Yellow
if ($SkipIcon -and (Test-Path $iconPath)) {
    Write-Host "      Reusing $iconPath" -ForegroundColor DarkGray
} else {
    $env:PYTHONPATH = $stageRoot
    & $python -m floating_clock.icon $iconPath
    if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }
}

Write-Host "[3/5] Writing entry point..." -ForegroundColor Yellow
@'
"""PyInstaller entry point -- keeps the package importable as `floating_clock`."""
import sys

from floating_clock.__main__ import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'@ | Set-Content -Path $entry -Encoding UTF8

Write-Host "[4/5] Running PyInstaller..." -ForegroundColor Yellow
$mode = if ($OneFile) { "--onefile" } else { "--onedir" }
$pyArgs = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--windowed", $mode,
    "--name", "FloatingClock",
    "--icon", $iconPath,
    "--paths", $stageRoot,
    "--distpath", $distDir,
    "--workpath", (Join-Path $buildDir "work"),
    "--specpath", $buildDir,
    # pywin32 pulls win32timezone in lazily when converting COM datetimes,
    # so PyInstaller never sees the import and the calendar dies at runtime.
    "--hidden-import", "win32timezone",
    "--hidden-import", "pythoncom",
    "--hidden-import", "win32com.client",
    "--hidden-import", "win32api",
    # ICS recurrence expansion; imported lazily inside ics.py, so PyInstaller
    # cannot see it and the Google feeds would die at runtime without this.
    "--hidden-import", "dateutil.rrule",
    "--hidden-import", "zoneinfo",
    # Casting to a Nest speaker: pychromecast finds speakers through zeroconf,
    # and cast.py imports it on use, so PyInstaller cannot see either.
    "--hidden-import", "pychromecast",
    "--hidden-import", "zeroconf",
    "--exclude-module", "numpy",
    "--exclude-module", "matplotlib",
    "--exclude-module", "scipy",
    "--exclude-module", "pandas",
    "--exclude-module", "PIL.ImageQt",
    "--exclude-module", "unittest",
    # The Qt host is for macOS and Linux; Windows ships the Tk one.
    "--exclude-module", "PySide6",
    "--exclude-module", "floating_clock.qt",
    $entry
)
& $python @pyArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$exe = if ($OneFile) {
    Join-Path $distDir "FloatingClock.exe"
} else {
    Join-Path $distDir "FloatingClock\FloatingClock.exe"
}
if (-not (Test-Path $exe)) { throw "Build finished but $exe is missing." }

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "`nBuilt $exe ($sizeMb MB)" -ForegroundColor Green

# --- setup.exe --------------------------------------------------------------
# The thing an end user actually receives. Needs Inno Setup 6
# (winget install JRSoftware.InnoSetup); skipped, with a note, when absent.
if (-not $OneFile) {
    $iscc = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($iscc) {
        Write-Host "`n[5/5] Compiling setup.exe..." -ForegroundColor Yellow
        & $iscc /Q (Join-Path $here "Setup.iss")
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }
        $setup = Get-ChildItem (Join-Path $distDir "FloatingClock-Setup-*.exe") |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        Write-Host "Installer: $($setup.FullName)" -ForegroundColor Green
        # Every tool in this repo publishes its installer under
        # OneDrive\0-Tools-Installers\<Tool>, so it is on every machine.
        $publishRoot = Join-Path $env:USERPROFILE "OneDrive\0-Tools-Installers"
        if (Test-Path $publishRoot) {
            $publishDir = Join-Path $publishRoot "FloatingClock"
            New-Item -ItemType Directory -Force -Path $publishDir | Out-Null
            Copy-Item $setup.FullName $publishDir -Force
            Write-Host "Published: $(Join-Path $publishDir $setup.Name)" -ForegroundColor Green
        }
    } else {
        Write-Host "`nInno Setup 6 not found: no setup.exe. Install it with" -ForegroundColor Yellow
        Write-Host "  winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
    }
}
