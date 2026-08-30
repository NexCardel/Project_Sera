; Script generated for Inno Setup Compiler
; Project Sera - Amas Sera Application Installer Setup Script

#define MyAppName "Amas Sera"
#define MyAppVersion "2.9.0"
#define MyAppPublisher "Aman Associates"
#define MyAppExeName "Amas_Sera.exe"
#define ShortcutName "CompanyInfo1"

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
SetupIconFile=..\assets\logo\icon_here.ico
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
Name: "{group}\{#ShortcutName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0
Name: "{group}\Uninstall {#ShortcutName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#ShortcutName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; IconIndex: 0; Tasks: desktopicon

[Registry]
Root: HKLM; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.json"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.json"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.json"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Mozilla\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.firefox.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.json"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Mozilla\NativeMessagingHosts\com.amanassociates.sera"; ValueType: string; ValueData: "{app}\_internal\native_host\com.amanassociates.sera.firefox.json"; Flags: uninsdeletekey
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

procedure CurStepChanged(CurStep: TSetupStep);
var
  JsonPath: String;
  JsonContent: String;
  ExePathEscaped: String;
  ExtId: String;
begin
  if CurStep = ssPostInstall then
  begin
    ExtId := ExtensionId('');
    ExePathEscaped := ExpandConstant('{app}\{#MyAppExeName}');
    StringChangeEx(ExePathEscaped, '\', '\\', True);

    ForceDirectories(ExpandConstant('{app}\_internal\native_host'));

    // Write Chromium native host manifest pointing directly to standalone executable
    JsonPath := ExpandConstant('{app}\_internal\native_host\com.amanassociates.sera.json');
    JsonContent := '{' + #13#10 +
      '  "name": "com.amanassociates.sera",' + #13#10 +
      '  "description": "Project Sera Native Messaging Host",' + #13#10 +
      '  "path": "' + ExePathEscaped + '",' + #13#10 +
      '  "type": "stdio",' + #13#10 +
      '  "allowed_origins": [' + #13#10 +
      '    "chrome-extension://' + ExtId + '/"' + #13#10 +
      '  ]' + #13#10 +
      '}' + #13#10;
    SaveStringToFile(JsonPath, JsonContent, False);

    // Write Firefox native host manifest
    JsonPath := ExpandConstant('{app}\_internal\native_host\com.amanassociates.sera.firefox.json');
    JsonContent := '{' + #13#10 +
      '  "name": "com.amanassociates.sera",' + #13#10 +
      '  "description": "Project Sera Native Messaging Host",' + #13#10 +
      '  "path": "' + ExePathEscaped + '",' + #13#10 +
      '  "type": "stdio",' + #13#10 +
      '  "allowed_extensions": [' + #13#10 +
      '    "sera-companion@amanassociates.com"' + #13#10 +
      '  ]' + #13#10 +
      '}' + #13#10;
    SaveStringToFile(JsonPath, JsonContent, False);
  end;
end;

[Run]
Filename: "{sys}\cmd.exe"; Parameters: "/C ""{app}\_internal\native_host\register_native_host.bat"" --silent"; StatusMsg: "Registering browser native host..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\cmd.exe"; Parameters: "/C ""{app}\_internal\native_host\unregister_native_host.bat"" --silent"; StatusMsg: "Removing browser native host..."; Flags: runhidden waituntilterminated
