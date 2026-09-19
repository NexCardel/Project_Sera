# Project Sera

Internal credential vault, browser autofill tool, and File Submission Tracker for Aman Associates.

This README is the quick orientation page. The detailed implementation and architectural documentation files are located in the `docs/` folder:

- [Codebase Architecture & Node Representation](docs/codebase-architecture-nodes.md) ([PDF Export](docs/codebase-architecture-nodes.pdf))
- [Project Structure](docs/project-structure.md)
- [Setup & Installation](docs/setup.md)
- [Features & Security Controls](docs/features-security.md)
- [Browser Automation & Extension](docs/browser-automation-extension.md)
- [File Submission Tracker](docs/file-submission-tracker.md)
- [Tracker Dump Status Matrix & Storage Architecture](docs/tracker-status-matrix-and-storage.md)
- [Government API Payload & Interception Matrix](docs/api-payloads-matrix.md)
- [VSDC — Visual Screen Data Capture Blueprint](docs/Blueprints/Sera_Visual_Screen_Harvester_Blueprint.md)
- [VSDC-X — UI Automation Exact-Text Capture Guide](docs/vsdc-x-engine-guide.md)
- [Build & Release Guide](docs/build-release.md)
- [Operations & LAN Synchronization](docs/operations-sync.md)

For the visual design system, sidebar/navigation states, client-detail layout, and screen-by-screen styling rules, see [docs/Sera_UI.md](docs/Sera_UI.md).

---

## Latest Features (v2.9.9)

- **Sera VSDC & VSDC-X (OS-Level Zero-Footprint Optical & Exact-Text Capture)**:
  - **VSDC-X Direct UI Automation Sensor**: Direct inspection of the Chromium accessibility tree via Windows UI Automation (`UIAutomationCore.dll`). Eliminates OCR optical ambiguity and character confusion across both GST and Income Tax 2.0.
  - **ITR Form & Heading Resolution**: Reads statutory form type from the page heading (`extract_itr_form_heading`) ahead of the wizard URL and body text, preventing false form matches from descriptive copy.
  - **Statutory Filing Type Capture**: Discovers and extracts return filing reasons (`Original`, `Revised`, `Belated`, and `Updated u/s 139(8A)`) into `filing_preference` alongside form type.
  - **Personal Info & Profile Field Capture**: Uses UIA `ValuePattern` to extract full legal names, Date of Birth (DOB), primary mobile, and primary email directly into database columns with strict privacy guards (no leaks to logs, HUD, or telemetry).
  - **False Submission Protection & Scoping**: Restricts 15-digit ITR Ack extraction strictly to terminal submission crosshairs; scopes `view-filed-returns` exclusively to the topmost return card to prevent historical "Processed" states from masking fresh submissions.
  - **Window-Isolated Session State (`_WindowSession`)**: Fully encapsulates session identity, form context, and buffers per native window handle (`hwnd`), preventing multi-window and cross-portal data bleeding.
  - **Time-Based Static Polling Cutoff**: 30-second wall-clock timeout (`_STATIC_SCREEN_TIMEOUT_SEC`) that dynamically resets upon loading transitions or screen painting changes.
  - **Dynamic Ambient HUD Pill (`VsdcHudPill`)**: Desktop overlay is hidden while polling, pulses on capture (250ms expansion), and tags the detection engine source (`[VSDC-X]` vs `[VSDC]`).
  - **Zero-Leakage PAN Audio Beeper & Gemini AI Enricher**: Zero-leakage audible feedback upon client detection; optional Gemini Flash 1.5/2.0 structured compliance enricher with live in-app Token Meter.

- **Sera SDC (Smart DOM Crosshairs — Extension Layer)**:
  - Route-gated content script protocols (`itr_protocol.js`, `gst_protocol.js`, `sdc_core.js`) sleeping on non-target routes, aggregating multi-step filing fragments into `sdc_assembler`, and flushing atomic master payloads over direct local HTTP (`http://127.0.0.1:49152`).
  - *Permanent Retirement Notice*: Sera SAD (API Interceptor / `net_interceptor.js`) and Sera SDS (Dataset Scanner / `sds_core.js`) are permanently retired and completely removed from the workspace.

- **Tracker Dump Workspace (`TrackerDumpWindow`)**:
  - Dedicated desktop audit workspace managing statutory filings in SQLite table `tracker_dump` (`rawPayload.db`).
  - **Google Material Design Pill Badges**: High-contrast status pills (`mdi.*` via QtAwesome) across all filing states with darkened status cell background rendering via a dedicated `QWidget` wrapper.
  - **Monotonic Status Promotion Engine**: Submissions only upgrade their lifecycle rank (e.g. Draft -> Submitted -> Verified), ensuring statutory ARNs and valid verifications are never overwritten or downgraded by subsequent navigation passes.
  - **AI Token Meter & Diagnostics**: Real-time tracking of Gemini AI structured parsing usage and API cost metrics.
  - Features multi-field search, method filters (`VSDC-X`, `VSDC_Visual`, `SDC`), raw JSON payload inspector drawer, and CSV export.

- **Sera Clipboard Assist (SCA — Ambient Password Autofill)**:
  - Automatically arms password in memory when staff copy client User IDs from Excel, Sheets, Notepad, or CSV rosters.
  - Zero-touch autofill immediately injects matching credentials when the User ID is pasted into any recognized web portal.
  - Floats an on-page notification banner displaying client business name, owner name, and portal autofill confirmation.

- **Instant Prompt-Free Auto-Unlock & Windows Autostart**:
  - Auto-derives and decrypts vault on startup using local keyfile (`sera.key`).
  - Launches instantly into your workspace without popping up a master password login prompt on launch, while preserving full Admin PIN protection for administrative tasks.

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

Project Sera stores encrypted vault data and session state in the user profile directory:

```text
%USERPROFILE%\AmanAssociates_Sera\
|-- master.db                  # SQLCipher encrypted client vault
|-- rawPayload.db              # SQLite storage for tracker_dump filings
|-- sera.salt                  # Salt for PBKDF2 vault key derivation
|-- sera.key                   # Auto-unlock keyfile
|-- device_identity.txt        # Local machine GUID identifier
|-- gemini_token_stats.json    # Gemini AI structured parser token and cost metrics
|-- Vsdc_Captures\             # Local diagnostic captures (redirected from Program Files)
`-- native_host\
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

Compiled installer executables are saved to `installer_output\Amas_Sera_Setup_v2.9.9.exe`.
