# Build and Release

This document outlines how to build standalone executable packages, compile Windows setup installers, and publish mandatory auto-updates for Project Sera.

---

## 1. Complete Build Process

To build both the standalone desktop executable bundle and the signed Chrome/Edge extension package:

```powershell
venv\Scripts\python build_tools\build_package.py
```

Output lands in:
- **Executable Bundle**: `package_dist\Amas_Sera\Amas_Sera.exe`
- **Browser Extension CRX**: `package_assets\extension\ProjectSeraCompanion.crx`

> [!IMPORTANT]
> Always run `package_dist\Amas_Sera\Amas_Sera.exe` from inside the `package_dist\Amas_Sera\` directory. PyInstaller requires adjacent dependencies in `_internal\`.

---

## 2. Windows Setup Installer (Inno Setup 7)

Compile `build_tools\installer_setup.iss` with Inno Setup Compiler (`ISCC.exe`):

```powershell
& "C:\Program Files\Inno Setup 7\ISCC.exe" build_tools\installer_setup.iss
```

The resulting single-file installer will be generated at:

```text
installer_output\Amas_Sera_Setup_v2.3.4.1.exe
```

### Key Installer Specifications:
- **Shortcut Name**: Configured as **`CompanyInfo1`** on Desktop and Start Menu.
- **Application AppID**: `D37F8E9C-4A2B-4F1E-9C8A-1B3D5E7F9A0B`
- **Native Host Registration**: Installs persistent native host manifest to `~/AmanAssociates_Sera/native_host/` and registers registry entries under `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera`.

---

## 3. Auto-Updater & GitHub Release Workflow

When publishing a new release to staff:

1. Update `APP_VERSION` in `version.py`, e.g., `"2.3.4.1"`.
2. Update `#define MyAppVersion` in `build_tools\installer_setup.iss`.
3. Update `version.json` in the repository root:

```json
{
  "version": "2.3.4.1",
  "min_required_version": "2.3.4.1",
  "mandatory": true,
  "download_url": "https://github.com/NexCardel/Project_Sera/releases/download/v2.3.4.1/Amas_Sera_Setup_v2.3.4.1.exe",
  "release_notes": "Project Sera v2.3.4.1 release: Instant app locking and mandatory modal restart dialog upon receiving LAN database sync or manual database restore."
}
```

4. Commit and push the version updates:

```bash
git add .
git commit -m "Release v2.3.4.1"
git push origin main
```

5. Create a GitHub release tag matching `v2.3.4.1`:

```bash
git tag -a v2.3.4.1 -m "Release v2.3.4.1"
git push origin v2.3.4.1
```

6. Upload the compiled setup installer `installer_output\Amas_Sera_Setup_v2.3.4.1.exe` as the binary release asset matching `download_url`.

The next time an employee opens Project Sera, the app queries GitHub (`version.json`), presents the mandatory update modal, downloads the installer, and restarts into the new version.
