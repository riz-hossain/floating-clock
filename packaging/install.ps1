<#
.SYNOPSIS
    Installs Floating Clock for the current user. No administrator rights needed.

.DESCRIPTION
    Copies the app to %LOCALAPPDATA%\Programs\FloatingClock, creates a Start
    Menu shortcut, and registers an entry in Settings > Apps so it can be
    uninstalled the normal way.

    Installs the built executable from .\dist when present; otherwise falls
    back to installing the Python source and launching it with pythonw.

.PARAMETER Desktop
    Also create a desktop shortcut.

.PARAMETER Startup
    Launch the clock automatically when you sign in.

.PARAMETER NoLaunch
    Skip starting the clock at the end of the install.

.EXAMPLE
    .\install.ps1 -Desktop -Startup
#>
[CmdletBinding()]
param(
    [switch]$Desktop,
    [switch]$Startup,
    [switch]$NoLaunch,
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$AppName = "FloatingClock"
$DisplayName = "Floating Clock"
$Version = "1.9.6"
$Publisher = "Floating Clock"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$packageRoot = Split-Path -Parent $here                       # ...\floating_clock
$toolsRoot = Split-Path -Parent $packageRoot                  # ...\tools
if (-not $InstallDir) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
}
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

Write-Host ""
Write-Host "  Floating Clock $Version" -ForegroundColor Magenta
Write-Host "  Installing for $env:USERNAME (no admin required)" -ForegroundColor DarkGray
Write-Host ""

# --- pick a payload --------------------------------------------------------
$oneDirExe = Join-Path $here "dist\$AppName\$AppName.exe"
$oneFileExe = Join-Path $here "dist\$AppName.exe"
$mode = $null
if (Test-Path $oneDirExe) { $mode = "onedir"; $source = Join-Path $here "dist\$AppName" }
elseif (Test-Path $oneFileExe) { $mode = "onefile"; $source = $oneFileExe }
elseif (Test-Path (Join-Path $packageRoot "app.py")) { $mode = "source" }
else { throw "Nothing to install: no build in .\dist and no source package found." }

$pythonw = $null
if ($mode -eq "source") {
    $cmd = Get-Command pythonw -ErrorAction SilentlyContinue
    if (-not $cmd) {
        $py = Get-Command python -ErrorAction SilentlyContinue
        if ($py) { $pythonw = Join-Path (Split-Path -Parent $py.Source) "pythonw.exe" }
    } else {
        $pythonw = $cmd.Source
    }
    if (-not $pythonw -or -not (Test-Path $pythonw)) {
        throw "No build found in .\dist and pythonw.exe is not available. Run .\build.ps1 first."
    }
    & (Join-Path (Split-Path -Parent $pythonw) "python.exe") -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('PIL') else 1)"
    if ($LASTEXITCODE -ne 0) { throw "Source install needs Pillow: python -m pip install pillow" }
    Write-Host "  Source install (launches with $pythonw)" -ForegroundColor DarkGray
} else {
    Write-Host "  Standalone build ($mode)" -ForegroundColor DarkGray
}

# --- stop any running copy -------------------------------------------------
Get-Process -Name $AppName -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "  Closing the running clock..." -ForegroundColor DarkGray
    $_ | Stop-Process -Force
    Start-Sleep -Milliseconds 400
}

# --- copy files ------------------------------------------------------------
Write-Host "  Installing to $InstallDir" -ForegroundColor Cyan
# Clear the contents rather than the folder itself. Windows keeps a handle on
# a directory for a while after the app inside it exits, and deleting the
# folder is the one step that, when it fails, leaves nothing installed at all.
if (Test-Path $InstallDir) {
    foreach ($attempt in 1..5) {
        $left = @(Get-ChildItem $InstallDir -Force -ErrorAction SilentlyContinue)
        if (-not $left) { break }
        $left | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        if ($attempt -lt 5) { Start-Sleep -Milliseconds 400 }
    }
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

switch ($mode) {
    "onedir"  { Copy-Item (Join-Path $source "*") $InstallDir -Recurse -Force }
    "onefile" { Copy-Item $source $InstallDir -Force }
    "source"  {
        New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "floating_clock") | Out-Null
        Copy-Item (Join-Path $packageRoot "*.py") (Join-Path $InstallDir "floating_clock") -Force
    }
}
Copy-Item (Join-Path $here "uninstall.ps1") $InstallDir -Force

# Icon: reuse the build's, or generate one if we can.
$iconPath = Join-Path $InstallDir "$AppName.ico"
$builtIcon = Join-Path $here "build\$AppName.ico"
if (Test-Path $builtIcon) {
    Copy-Item $builtIcon $iconPath -Force
} elseif ($mode -eq "source") {
    $env:PYTHONPATH = $toolsRoot
    & (Join-Path (Split-Path -Parent $pythonw) "python.exe") -m floating_clock.icon $iconPath 2>$null
}

if ($mode -eq "source") {
    $target = $pythonw
    $arguments = "-m floating_clock"
} else {
    $target = Join-Path $InstallDir "$AppName.exe"
    $arguments = ""
}
if (-not (Test-Path $iconPath)) { $iconPath = $target }

# --- shortcuts -------------------------------------------------------------
function New-Shortcut([string]$Path, [string]$Description) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $target
    $link.Arguments = $arguments
    $link.WorkingDirectory = $InstallDir
    $link.IconLocation = $iconPath
    $link.Description = $Description
    $link.Save()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null
}

$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Shortcut (Join-Path $startMenu "$DisplayName.lnk") "A floating always-on-top clock"
Write-Host "  Start Menu shortcut created" -ForegroundColor DarkGray

if ($Desktop) {
    New-Shortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "$DisplayName.lnk") "A floating always-on-top clock"
    Write-Host "  Desktop shortcut created" -ForegroundColor DarkGray
}

# --- run at sign-in --------------------------------------------------------
if ($Startup) {
    $command = if ($arguments) { "`"$target`" $arguments" } else { "`"$target`"" }
    New-ItemProperty -Path $RunKey -Name $AppName -Value $command -PropertyType String -Force | Out-Null
    Write-Host "  Will start automatically at sign-in" -ForegroundColor DarkGray
}

# --- Apps & Features entry -------------------------------------------------
$sizeKb = [int]((Get-ChildItem $InstallDir -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
New-Item -Path $UninstallKey -Force | Out-Null
$uninstallCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}\uninstall.ps1"' -f $InstallDir
@{
    DisplayName     = $DisplayName
    DisplayVersion  = $Version
    DisplayIcon     = $iconPath
    Publisher       = $Publisher
    InstallLocation = $InstallDir
    UninstallString = $uninstallCommand
    QuietUninstallString = "$uninstallCommand -Silent"
    EstimatedSize   = $sizeKb
    NoModify        = 1
    NoRepair        = 1
}.GetEnumerator() | ForEach-Object {
    $type = if ($_.Value -is [int]) { "DWord" } else { "String" }
    New-ItemProperty -Path $UninstallKey -Name $_.Key -Value $_.Value -PropertyType $type -Force | Out-Null
}
Write-Host "  Registered in Settings > Apps" -ForegroundColor DarkGray

Write-Host ""
Write-Host "  Installed." -ForegroundColor Green
Write-Host "  Right-click the clock for themes, opacity and settings." -ForegroundColor DarkGray
Write-Host "  Meetings, alarms and timers live in Settings." -ForegroundColor DarkGray
Write-Host ""

if (-not $NoLaunch) {
    if ($arguments) { Start-Process -FilePath $target -ArgumentList $arguments -WorkingDirectory $InstallDir }
    else { Start-Process -FilePath $target -WorkingDirectory $InstallDir }
    Write-Host "  Launched." -ForegroundColor Green
}
