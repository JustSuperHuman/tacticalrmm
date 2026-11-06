#define SupportVersion GetEnv('SUPPORT_VERSION')

[Setup]
AppId={{4C15E346-57F9-488D-BE97-48FE46AE6744}
AppName=Tactical RMM Support
AppVersion={#SupportVersion}
VersionInfoVersion={#NumericVersion}
AppPublisher=Tactical RMM
DefaultDirName={localappdata}\TacticalRMMSupport
DefaultGroupName=Tactical RMM Support
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
DisableWelcomePage=no
WizardStyle=modern
OutputDir=..\..\release
OutputBaseFilename=TacticalSupport-windows
Compression=lzma2
SolidCompression=yes
UninstallDisplayName=Tactical RMM Support
CloseApplications=no

[Messages]
WelcomeLabel1=Get connected with your support team
WelcomeLabel2=This will set up the remote support tool for your Windows account.%n%nOnly continue if you are expecting support from a technician you trust. Open the tool after setup and follow your technician's instructions.

[Files]
Source: "..\..\payload\MeshAgent.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\payload\MeshAgent.msh"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Tactical RMM Support"; Filename: "{app}\MeshAgent.exe"
Name: "{group}\Uninstall Tactical RMM Support"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\MeshAgent.exe"; Description: "Open remote support"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Stop this user's support agent so its files can be removed. The RMM's own Mesh Agent
; service runs as SYSTEM and is never matched by this per-user filter.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM MeshAgent.exe /FI ""USERNAME eq {username}"""; Flags: runhidden; RunOnceId: "StopSupportAgent"
