# Project Sera

Internal credential vault, browser autofill tool, and File Submission Tracker for Aman Associates.

This README is the quick orientation page. The detailed implementation and architectural documentation files are located in the `docs/` folder:

- [Codebase Architecture & Node Representation](docs/codebase-architecture-nodes.md) ([PDF Export](docs/codebase-architecture-nodes.pdf))
- [Project Structure](docs/project-structure.md)
- [Setup & Installation](docs/setup.md)
- [Features & Security Controls](docs/features-security.md)
- [Browser Automation & Extension](docs/browser-automation-extension.md)
- [File Submission Tracker](docs/file-submission-tracker.md)
- [Build & Release Guide](docs/build-release.md)
- [Operations & LAN Synchronization](docs/operations-sync.md)

For the visual design system, sidebar/navigation states, client-detail layout, and screen-by-screen styling rules, see [Sera_UI.md](Sera_UI.md).

---

## Latest Features (v2.3.4.1)

- **Sera Sync (Zero-Configuration LAN Database Pushing)**:
  - Built-in P2P LAN peer discovery over UDP broadcast (`Port 49156`).
  - Admin-accessible **Sera Sync** panel (`sera_sync_dialog.py`) displaying real-time online workstations, hostnames, and IP addresses.
  - One-way TCP database push (`Port 49157`) transferring `master.db` + `sera.salt` directly to a target peer without requiring pre-configured identical master passwords.
  - **Instant UI Locking & Modal Restart**: Receiving machine automatically locks UI interactions, pops a mandatory modal restart prompt, and cleanly auto-restarts (`os.execl`) to re-authenticate SQLCipher.
- **Smart Syncthing & Peer Backup Restore**:
  - `restore_from()` automatically scans, pairs, and decrypts Syncthing conflict files (`master.sync-conflict-*.db`, `sera.salt.sync-conflict-*`) and `sync_peer` conflict files (`.conflict-`, `.pre-sync-`).
  - Validates SQLCipher HMAC decryption (`SELECT count(*) FROM sqlite_master;`) prior to overwriting live database files.
- **Permanent Chrome Native Messaging Infrastructure**:
  - Native host manifest and scripts stored in permanent directory `~/AmanAssociates_Sera/native_host/` to prevent PyInstaller temporary `_MEIPASS` registry path leaks.
  - Handled `--native-host` CLI flag interception before `QApplication` initialization to isolate binary STDIN/STDOUT pipes from GUI logs.
- **Branding & Taskbar Grouping**:
  - Updated visual branding vector (`Flogo.svg`) rendered into multi-resolution native Windows `.ico` files (`256x256` down to `16x16`).
  - Explicit `AppUserModelID` registration (`AmanAssociates.ProjectSera.Vault.2.3.3`) for clean Windows Taskbar preview grouping.
  - Windows Desktop and Start Menu shortcut name configured as **`CompanyInfo1`**.
- **Excel-Style Grid Copying (`Ctrl+C`)**: Select cells or cell blocks and copy formatted data directly to Excel with a 500 ms green highlight flash (`#2E9B5F`).
- **Master Column List (MCL) ID Tokens**: Added `ID` field type (`"ID (Primary Key / Auto-Serial)"`) with single-column exclusivity, auto-serial numbers, and backend Client ID tokens (`CLI-XXXXX`).

---

## Quick Start

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
python main.py
```

First run prompts for a workstation user name and a master password. The master password is never stored on disk; it is used to derive the SQLCipher database encryption key via PBKDF2.

---

## Runtime Data Directory

Project Sera stores encrypted vault data in:

```text
~/AmanAssociates_Sera/
|-- master.db
|-- sera.salt
|-- device_identity.txt
`-- native_host/
    |-- com.amanassociates.sera.json
    |-- host.bat
    `-- host.py
```

`master.db` and `sera.salt` belong together: `master.db` is encrypted with SQLCipher, and `sera.salt` is required to derive the encryption key from the master password. You can synchronize these files between staff workstations using **Sera Sync** (Admin → Sera Sync) or Syncthing.

---

## Build & Release Packaging

To package the standalone executable bundle and Windows setup installer:

```powershell
# 1. Build PyInstaller Standalone Executable Bundle
venv\Scripts\python build_tools\build_package.py

# 2. Compile Windows Installer Setup
& "C:\Program Files\Inno Setup 7\ISCC.exe" build_tools\installer_setup.iss
```

Compiled installer executables are saved to `installer_output\Amas_Sera_Setup_v2.3.4.1.exe`.
