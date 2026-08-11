; Script generated for Inno Setup Compiler
; Project Sera - Amas Sera Application Installer Setup Script

#define MyAppName "Amas Sera"
#define MyAppVersion "2.0"
#define MyAppPublisher "Aman Associates"
#define MyAppExeName "Amas_Sera.exe"

[Setup]
AppId={{D37F8E9C-4A2B-4F1E-9C8A-1B3D5E7F9A0B}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppPublisher}\{#MyAppName}
DefaultGroupName={#MyAppPublisher}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=Amas_Sera_Setup_v2.0
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\Amas_Sera\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.json"; Flags: uninsdeletekey

[Run]
Filename: "{sys}\cmd.exe"; Parameters: "/C ""{app}\_internal\native_host\register_native_host.bat"" --silent"; StatusMsg: "Registering browser integration..."; Flags: runhidden waituntilterminated skipifsilent
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
