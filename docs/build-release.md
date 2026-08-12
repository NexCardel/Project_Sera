# Build and Release

## Build Executable

Generate the packaged app with PyInstaller:

```bash
pyinstaller build_tools/Amas_Sera.spec
```

Output lands in:

```text
package_dist/Amas_Sera/Amas_Sera.exe
```

Run and distribute `package_dist/Amas_Sera/Amas_Sera.exe` together with the entire `package_dist/Amas_Sera/` folder.

Do not run the executable from `package_build/`, and do not copy the `.exe` by itself. PyInstaller needs the adjacent `_internal/` directory, including Python DLLs, dependency binaries (`qtawesome`, `sqlcipher3`, `playwright`), and bundled application data (`assets/`, `ui/`, `native_host/`, `sera_extension/`).

## Auto-Updater Workflow

When releasing changes to staff:

1. Update `APP_VERSION` in `version.py`, for example `2.3.3`.
2. Update `version.json` in the git repository:

```json
{
  "version": "2.3.3",
  "min_required_version": "2.3.3",
  "mandatory": true,
  "download_url": "https://github.com/NexCardel/Project_Sera/releases/download/v2.3.3/Amas_Sera_Setup_v2.3.3.exe",
  "release_notes": "Project Sera v2.3.3 release: Excel-style cell Ctrl+C copying, fixed duplicate purging, modal dialog slide panel protection, MCL ID field token system, and automatic extension cookie clearing."
}
```

3. Commit and push the changes:

```bash
git add .
git commit -m "Release v2.3.3"
git push
```

4. Create a GitHub release tag such as `v2.3.3`.
5. Upload the installer executable matching `download_url`, for example `Amas_Sera_Setup_v2.3.3.exe`.

The next time an employee opens Project Sera, the app checks GitHub, presents a mandatory update modal, downloads the update, and restarts into the new version.

## Complete Windows Installer

Build the desktop bundle and its signed Chrome/Edge extension package with:

```bash
venv\Scripts\python.exe build_tools\build_package.py
```

Then compile `build_tools\installer_setup.iss` with Inno Setup Compiler (`ISCC.exe`). The resulting installer is `installer_output\Amas_Sera_Setup_v2.3.3.exe`.

The extension signing key is kept at `build_tools\sera_extension.pem` and is intentionally ignored by Git. Keep it securely: replacing it changes the extension ID and breaks browser updates/native-host allowlisting for installed users.
