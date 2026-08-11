; Script generated for Inno Setup Compiler
; Project Sera - Amas Sera Application Installer Setup Script

#define MyAppName "Amas Sera"
#define MyAppVersion "2.3.0"
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
OutputDir=..\installer_output
OutputBaseFilename=Amas_Sera_Setup_v{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\package_dist\Amas_Sera\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\package_assets\extension\ProjectSeraCompanion.crx"; DestDir: "{app}\extension"; Flags: ignoreversion
Source: "..\package_assets\extension\extension_id.txt"; DestDir: "{app}\extension"; Flags: ignoreversion
Source: "..\package_assets\extension\extension_version.txt"; DestDir: "{app}\extension"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKLM; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.json"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.json"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Google\Chrome\Extensions\{code:ExtensionId}"; ValueType: string; ValueName: "path"; ValueData: "{app}\extension\ProjectSeraCompanion.crx"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Google\Chrome\Extensions\{code:ExtensionId}"; ValueType: string; ValueName: "version"; ValueData: "{code:ExtensionVersion}"
Root: HKLM; Subkey: "Software\Microsoft\Edge\Extensions\{code:ExtensionId}"; ValueType: string; ValueName: "path"; ValueData: "{app}\extension\ProjectSeraCompanion.crx"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Microsoft\Edge\Extensions\{code:ExtensionId}"; ValueType: string; ValueName: "version"; ValueData: "{code:ExtensionVersion}"

[Code]
function ExtensionId(Param: String): String;
var
  Value: AnsiString;
begin
  if not LoadStringFromFile(ExpandConstant('{app}\extension\extension_id.txt'), Value) then
    RaiseException('The Project Sera browser extension ID could not be read.');
  Result := Trim(Value);
end;

function ExtensionVersion(Param: String): String;
var
  Value: AnsiString;
begin
  if not LoadStringFromFile(ExpandConstant('{app}\extension\extension_version.txt'), Value) then
    RaiseException('The Project Sera browser extension version could not be read.');
  Result := Trim(Value);
end;

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
