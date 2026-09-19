<#
.SYNOPSIS
    Removes Floating Clock for the current user.

.DESCRIPTION
    Stops the clock, removes shortcuts, the sign-in entry, the Apps & Features
    registration and the install folder. Saved settings are kept unless
    -RemoveSettings is passed, so reinstalling restores your layout.

.PARAMETER RemoveSettings
    Also delete %APPDATA%\FloatingClock (themes, opacity, position).

.PARAMETER Silent
    Do not prompt or pause.
#>
[CmdletBinding()]
param(
    [switch]$RemoveSettings,
    [switch]$Silent
)

$ErrorActionPreference = "Continue"
$AppName = "FloatingClock"
$DisplayName = "Floating Clock"
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

$installDir = (Get-ItemProperty -Path $UninstallKey -Name InstallLocation -ErrorAction SilentlyContinue).InstallLocation
if (-not $installDir) { $installDir = Join-Path $env:LOCALAPPDATA "Programs\$AppName" }

Write-Host ""
Write-Host "  Uninstalling $DisplayName" -ForegroundColor Magenta

Get-Process -Name $AppName -ErrorAction SilentlyContinue | Stop-Process -Force
# A source install runs inside pythonw, so match on the command line instead.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*floating_clock*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 400

foreach ($shortcut in @(
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$DisplayName.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "$DisplayName.lnk")
)) {
    if (Test-Path $shortcut) {
        Remove-Item $shortcut -Force
        Write-Host "  Removed shortcut: $shortcut" -ForegroundColor DarkGray
    }
}

if (Get-ItemProperty -Path $RunKey -Name $AppName -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $RunKey -Name $AppName -Force
    Write-Host "  Removed sign-in entry" -ForegroundColor DarkGray
}

if (Test-Path $UninstallKey) {
    Remove-Item $UninstallKey -Recurse -Force
    Write-Host "  Removed Apps & Features entry" -ForegroundColor DarkGray
}

if ($RemoveSettings) {
    $settings = Join-Path $env:APPDATA $AppName
    if (Test-Path $settings) {
        Remove-Item $settings -Recurse -Force
        Write-Host "  Removed saved settings" -ForegroundColor DarkGray
    }
} else {
    Write-Host "  Kept saved settings (-RemoveSettings to delete them)" -ForegroundColor DarkGray
}

if (Test-Path $installDir) {
    # This script lives in the folder it is deleting, so hand the last step to
    # a detached shell that outlives us.
    $self = $MyInvocation.MyCommand.Path
    if ($self -and $self.StartsWith($installDir, [StringComparison]::OrdinalIgnoreCase)) {
        Start-Process -WindowStyle Hidden -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            "Start-Sleep -Seconds 2; Remove-Item -LiteralPath '$installDir' -Recurse -Force -ErrorAction SilentlyContinue"
        )
        Write-Host "  Removing $installDir" -ForegroundColor DarkGray
    } else {
        Remove-Item $installDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  Removed $installDir" -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "  Uninstalled." -ForegroundColor Green
Write-Host ""
if (-not $Silent) { Start-Sleep -Seconds 2 }
