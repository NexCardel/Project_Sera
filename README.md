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

## Latest Features (v2.4.2)

- **Sera API Detection (SAD)**:
  - Passive Network Response Interceptor (`net_interceptor.js`) running in the page MAIN execution world.
  - Intercepts `fetch()` and `XMLHttpRequest` calls in real-time across GST (`status_cd: "1"`), Income Tax (`acknowledgementNumber`), TRACES (`requestNo`), and universal web portals.
  - Extracts filing ARNs, Ack numbers, and HTTP success payloads without modifying or delaying page network traffic.
- **Tracker Dump Subsystem**:
  - Dedicated desktop workspace (`TrackerDumpWindow`) logging all raw SAD captures and extension dumps into SQLite table `tracker_dump`.
  - Features real-time search, method filters (`SAD_API_Interceptor`, `DOM_Tracker`, `Manual_Fallback`), raw JSON payload inspector drawer, and CSV export.
  - Universal client resolution dynamically matches client primary keys, `client_id_token` (`CLI-00370`), MCL Serial Numbers (`No. 370`), and Name/PAN/GSTIN substring queries.
- **Modal-Free Background Filing Logging**:
  - Filing results are recorded silently in the background (`FilingConfirmationDialog` unhooked).
  - Displays non-intrusive 5-second desktop toast notifications (`Captured GST Portal Filing (SAD API Interceptor) — ARN: AA270826...`) with zero screen popups.
- **Instant Prompt-Free Auto-Unlock**:
  - Auto-derives and decrypts vault on startup using local keyfile (`sera.key`).
  - Launches instantly into your workspace without popping up a master password login prompt on launch, while preserving full Admin PIN protection for administrative tasks.
- **Windows Autostart Integration**:
  - Automatic launching on Windows PC startup via Registry (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`).
  - Toggleable anytime from Settings → General.
- **Search Grid Cell Formatting Fixes**:
  - Persistent cell fill highlights and text colors across database restarts without QSS stylesheet overrides.

---

## Quick Start

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
python main.py
```

On first launch, Project Sera auto-derives and secures your vault using `sera.key`. Admin Mode remains protected by the Admin PIN (`1234` or custom PIN).

---

## Runtime Data Directory

Project Sera stores encrypted vault data in:

```text
~/AmanAssociates_Sera/
|-- master.db
|-- sera.salt
|-- sera.key
|-- device_identity.txt
`-- native_host/
    |-- com.amanassociates.sera.json
    |-- host.bat
    `-- host.py
```

`master.db` and `sera.salt` belong together: `master.db` is encrypted with SQLCipher, and `sera.salt` is required to derive the encryption key. You can synchronize these files between staff workstations using **Sera Sync** (Admin → Sera Sync) or Syncthing.

---

## Build & Release Packaging

To package the standalone executable bundle and Windows setup installer:

```powershell
# 1. Build PyInstaller Standalone Executable Bundle
venv\Scripts\python build_tools\build_package.py

# 2. Compile Windows Installer Setup
& "C:\Program Files\Inno Setup 7\ISCC.exe" build_tools\installer_setup.iss
```

Compiled installer executables are saved to `installer_output\Amas_Sera_Setup_v2.4.2.exe`.
