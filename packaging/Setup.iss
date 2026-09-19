; Floating Clock -- Inno Setup script.
;
; Builds a per-user setup.exe (no administrator prompt) from the PyInstaller
; one-folder output in .\dist\FloatingClock. build.ps1 compiles this as its
; last step when Inno Setup 6 is installed; the result lands in .\dist.

#define AppName "Floating Clock"
#define AppExe "FloatingClock.exe"
; Passed in by build.ps1 from floating_clock/__init__.py, which is the one
; place a version is written. The fallback is only for running ISCC by hand.
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#define AppPublisher "Floating Clock"
#define AppId "{{8D3F6A61-4F5B-4E0C-9B2A-7C1E2F3A4B5C}"
#define Payload "dist\FloatingClock"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\FloatingClock
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=FloatingClock-Setup-{#AppVersion}
SetupIconFile=build\FloatingClock.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start Floating Clock when I &sign in"; GroupDescription: "Startup:"

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; The same Run value the app's own "Start with Windows" switch manages, so the
; two never disagree.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "FloatingClock"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "FloatingClock"; Flags: deletevalue; Tasks: not startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "StopClock"

[Code]
// Stop a running clock before files are replaced, so the upgrade never asks
// the user to close anything themselves.
procedure StopRunningClock;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Sleep(400);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningClock;
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningClock;
  Result := True;
end;

// Saved settings live in %APPDATA%\FloatingClock. They are kept on uninstall
// unless the user says otherwise, so a reinstall restores their layout.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Settings: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Settings := ExpandConstant('{userappdata}\FloatingClock');
    if DirExists(Settings) then
      if SuppressibleMsgBox('Also delete your saved settings, alarms and timers?', mbConfirmation, MB_YESNO, IDNO) = IDYES then
        DelTree(Settings, True, True, True);
  end;
end;
