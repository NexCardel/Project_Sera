# Project Sera — Implementation Guide

Internal credential vault + browser autofill + File Submission Tracker for Aman Associates.

For the current visual system, sidebar/navigation states, client-detail layout, and screen-by-screen styling rules, see [Sera_UI.md](Sera_UI.md).

The bottom-left audit confirmation system is documented in [Sera_Alert_System_Blueprint.md](../docs/blueprints/Sera_Alert_System_Blueprint.md) and fully implemented in the desktop shell.

## UI update summary

The current shell uses a red sidebar with mint active states, staff-name profile footer, admin-gated management navigation, and consistent cream/card styling across the management tools. Normal users can open the dedicated Add Client form without entering Admin Mode; Edit, Delete, and Attach/Detach remain admin-only.

Client Detail now uses compact Identity & Contacts, Security Credentials, Service Management, Notes, Autofill, and DRS sections with shared card and field rules. Back navigation is available on detail/workspace views and slide-panel tools, with visible Material Design arrow icons.

The same component rules are applied to Manage Clients, Audit Log, MCL, Settings, Services, Filing Types, Filing Periods, CSV Import, and Data Management screens. Layouts are DPI-aware and shrinkable on smaller displays.

## 1. Folder Structure

```
project_sera/
├── main.py                    # App entry point & IPC signal handler (run this)
├── database.py                # SQLCipher DB setup, CRUD, Audit Log, Filing Status, Backup/Restore
├── security.py                # Key derivation (master password) + PIN hashing
├── automation.py              # Playwright browser autofill & Extension native host bridge
├── requirements.txt
├── README.md
├── test_portal_success.html   # Local test environment for Phase 2 FST verification
├── native_host/               # Native Messaging bridge for browser extension
│   ├── com.amanassociates.sera.json
│   ├── host.bat
│   └── host.py
├── sera_extension/            # Browser extension companion (Manifest V3)
│   ├── background.js          # Active tab tracker, tab-close detection, IPC relay
│   ├── tracker.js             # Passive DOM observer for ARN capture & in-page toast
│   ├── manifest.json          # Extension permissions & content script definitions
│   ├── config.json
│   └── content_scripts/       # Helper scripts for login autofill & detectors
└── ui/
    ├── __init__.py
    ├── extension_listener.py   # Threaded TCP server socket (port 49152) for extension events
    ├── masking.py              # Password display-masking logic
    ├── search_window.py        # Search bar & results list (instant filter & startup auto-load)
    ├── client_detail_window.py # Client info, masked credentials, DRS table, autofill buttons
    ├── admin_window.py         # Admin CRUD, MCL, Services, Backup/Restore, CSV Export, Audit Log
    ├── audit_log_window.py     # System Audit Log viewer dialog
    ├── manual_credentials_dialog.py # Manual portal copy helper with 30s auto clipboard clear
    ├── components/
    │   └── toast.py            # SeraAlert non-modal floating bottom-left notification widget
    ├── services/
    │   └── alert_service.py    # ActionAlertFormatter action-to-alert matrix & safe identity formatter
    ├── dialogs/
    │   └── filing_confirmation_dialog.py # System-modal confirmation & period selector
```

At runtime, the app creates:
```
~/AmanAssociates_Sera/
├── master.db      # The encrypted database — THIS is the file to sync via Syncthing
└── sera.salt       # Non-secret salt used in key derivation — sync this too
```
Both files need to be on every employee's machine via Syncthing, pointed at the same folder (`AmanAssociates_Sera`). For step-by-step instructions, see [Syncthing_Setup_Guide.md](../docs/Syncthing_Setup_Guide.md).

---

## 2. Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
playwright install chromium    # or skip if using extension / edge channel
```

Run it:
```bash
python main.py
```

**First run:** You will be asked to enter your **Staff Label/Name** (for audit tracking attribution) and set a **Master Password** (min 8 characters). This password is never stored — it's re-derived into the database encryption key every time the app starts, using PBKDF2 (480,000 iterations) with a per-installation random salt (`sera.salt`). Every employee must use the **same** master password, since it's what makes everyone's local app able to open the same synced `master.db`.

**Admin PIN** is separate from the master password and is set the first time anyone clicks "Admin Mode." Keep this one to yourself (the firm owner) — it's what gates the CRUD screen, backup restore, CSV export, and audit log viewer.

---

## 3. Features & Security Controls

* **Redesigned Modern UI (Beige & Red Palette)**: A visually striking, structured interface featuring an accordion-style left sidebar with unicode icons, padded search elements, and grouped read-only panels (Identity & Contacts, 2-Column Security Credentials, Service Management).
* **Spreadsheet-Style Search**: The main screen presents a clean, dynamic data grid of all clients. Active cells are highlighted with a distinct blue focus ring for keyboard navigation, featuring a prominent top-level search bar and 'Add Client' control. In Admin Mode, the search toolbar includes a Material Design Archive action for the selected client; archiving confirms the action, hides the client from active results, and records it in the audit log without deleting data.
* **Strict Admin Mode Gating**: Edit, Delete, and other mutating controls are strictly hidden in standard user mode. Features like the System Audit Log, Backup/Restore, and Settings are exclusively available to authenticated administrators via a secure PIN.
* **Centralized Settings & Constraints**: Tabbed control over application theme, window display mode, mask modes, password reveal 'Show/Hide' buttons toggle, service manual credential copy controls toggle, spreadsheet column visibility, Quick-Copy column restrictions, and feature toggles.
* **Window Display Modes**: Choose between **Fullscreen / Maximized (Default)**, **Square Mode (1:1 Aspect Ratio)**, and **Rectangular Mode (Classic 950x680)**. Includes automatic Geometry & DPI adjustment.
* **Feature Toggles (DRS & Tracker)**: Master toggle switches in Admin Settings to enable or disable the **Deadline Reminder System (DRS)** and the **Filing Success Tracker (FST)** independently across the UI and browser extension.
* **Quick-Copy Shortcut**: When enabled, authorized fields can be directly clicked on a client's profile to copy instantly. Unauthorized fields (like passwords) will strip the copy button and pointer behavior to strictly enforce standard tracked extraction.
* **Auto Clipboard Clear**: Copied passwords and secret credentials are automatically purged from the Windows clipboard after a customizable timeout (default 30 seconds), safely verifying the text hasn't been overwritten by the user.
* **Autofill Window Minimization**: Clicking an **Autofill** or **Manual Copy** button (or using `Alt+1..9` shortcuts) keeps the app on the client detail page while automatically minimizing the main application window to bring the browser portal into focus.
* **System Audit Log**: Every client view, autofill trigger, manual copy, backup, restore, and CSV export action is recorded in an un-deletable `audit_log` table with UTC timestamps and staff attribution labels. Viewable via the **"Audit Log..."** button in Admin Mode.
* **In-App Backup & Restore**: Admins can generate timestamped backup folders or safely restore from existing backups (`master.db` + `sera.salt`) with automatic pre-restore safety snapshots.
* **Sera Alert System**: Non-modal, bottom-left floating notifications delivering instant visual feedback for user actions (autofill, manual copy, create, update, archive, delete, backup, restore, CSV export). Utilizes semantic level colors (`success`, `info`, `warning`, `error`), Google Material Design icons, safe client identity formatting, and 3-second auto-dismissal without spawning native OS windows.
* **Client Detail Dismissal**: Clicking outside the open Client Detail slide panel closes it and returns to the search view. Administrative forms remain protected from outside-click dismissal.
* **CSV Import/Export & Schema Templates**: Admin Mode supports bulk client ingestion via CSV, exporting client identity records/system logs, and generating a dynamic **Schema Import Template** that maps all currently active column headers (plus Services and Notes) perfectly for data entry.

---

## 4. Browser Automation & Extension Native Host

* `automation.py` routes extension‑mode services to the `sera_extension` via a local TCP bridge (`native_host/host.py` on port 49153).
* `automation.py` broadcasts settings changes (such as disabling/enabling the filing tracker) directly to the native host so the browser extension updates its state immediately.
* If the extension is asleep or the browser is closed when Autofill is clicked, `automation.py` automatically opens the login URL, wakes Chrome/Edge background service workers and retries connection for up to 10 seconds.
* `sera_extension/background.js` injects the fill logic directly into the page using `chrome.scripting.executeScript`. The injected `fillCredentialsInPage()`:
  - Fills the PAN/UID field **once** via a 1 second poll.
  - Clicks the secure‑access `mat‑checkbox` / `mat-mdc-checkbox` if needed.
  - Dispatches Angular-compatible events (`CompositionEvent` and `InputEvent`) so reactive form frameworks (like Income Tax ITR portal) register password inputs cleanly without raising authentication errors.
  - Auto-clicks the Continue/Submit button 600ms after password fill.

---

## 5. File Submission Tracker (FST)

The **File Submission Tracker (FST)** is a hybrid tracking engine that records tax return filings in the Data Reporting System (DRS).

### Architecture & Workflow

```
[Desktop App: main.py] ──(Autofill Payload)──> [Native Host / TCP 49153] ──> [Extension: background.js]
                                                                                     │
                                                                           Injects active session
                                                                                     │
                                                                                     ▼
[DRS Table in DB] <──(TCP 49152)── [ExtensionListener] <──(Filing Result)── [tracker.js on Portal]
        ▲                                                                            │
        │                                                                (No ARN & Tab Closed?)
        │                                                                            │
[FilingConfirmationDialog] <──(Uncertain Result)─────────────────────────────────────┘
```

### Two-Tier Operation
1. **Tier 1: Automated ARN Capture (`tracker.js`)**
   - Injected into all portal tabs when an active autofill payload is set.
   - Monitors page DOM with `MutationObserver` for success text (`submitted successfully`) and ARN identifiers (`ARN:`, `Transaction ID:`).
   - Displays a sleek green toast in the browser (`✅ Return Submitted!`) upon detection.
   - Sends `filing_result` to `ExtensionListener` on TCP port 49152.
2. **Tier 2: Fallback Confirmation Modal (`FilingConfirmationDialog`)**
   - If the user closes the browser tab or logs out **without** an ARN being captured during an active session, `background.js` sends an `uncertain_result` to the desktop app.
   - The desktop app displays a system-modal dialog (`Qt.WindowStaysOnTopHint`, `Qt.ApplicationModal`) asking if the return was filed.
   - Features period selection buttons tailored to the filing frequency (Monthly, Quarterly, Annual).
   - On confirmation, writes the record to `filing_status` table (`submitted`) with timestamp and active staff attribution, and reloads `ClientDetailWindow`.

### Verification & Testing Procedure
- **Phase 1 (Fallback Test)**: Click **Autofill** -> Close browser tab without filing -> Desktop app pops up `FilingConfirmationDialog`.
- **Phase 2 (Automated Capture Test)**: Click **Autofill** -> Open `file:///c:/Users/Nex/Downloads/Project Sera/APP/test_portal_success.html` -> Green toast appears in browser -> Desktop app pre-populates ARN `AA27032419827364` -> No fallback prompt on tab close.

---

## 6. Building Amas_Sera.exe with PyInstaller

Generate and build using PyInstaller:

```bash
pyinstaller Amas_Sera.spec
```

Output lands in `dist/Amas_Sera/Amas_Sera.exe`.

> **Important:** Run and distribute `dist/Amas_Sera/Amas_Sera.exe` together with the entire `dist/Amas_Sera/` folder. Do not run the executable from `build/` or copy the `.exe` by itself; PyInstaller needs the adjacent `_internal/` directory (including `python314.dll`) and bundled application data.

### Native messaging on another PC

The browser extension requires the native messaging host to be registered on each Windows machine. Distribute the complete PyInstaller output folder, including `native_host/`, then run `native_host\register_native_host.bat` once as the logged-in Windows user. Start the desktop app once before testing the extension so it can also register the host automatically.

The packaged build includes `native_host.host` so `CompanyInfo1.exe --native-host` stays alive as Chrome's stdio bridge. If Chrome still reports `Native host has exited`, check `native_host\host_error.log` and confirm that the registry entry points to the `com.amanassociates.sera.json` inside the copied application folder.

---

## 7. Operational & Sync Notes

- **Syncthing Restores**: Restoring a database in Admin Mode overwrites the local `master.db` and `sera.salt`. If Syncthing is running, this restored version will sync to other staff PCs. Ensure teammates pause Syncthing before performing a database restore.
- **Audit Tracking**: Each employee enters their staff name on first launch, ensuring the Audit Log accurately attributes credential access, portal autofill events, and return submission tracking.

---

## 8. Releasing Updates to Staff (Auto-Updater Workflow)

When you make changes to the app and want all employees to update:

1. Update `APP_VERSION` in `version.py` (e.g. `2.3.1`).
2. Update `version.json` in the git repository:
   ```json
   {
     "version": "2.3.1",
     "min_required_version": "2.3.1",
     "mandatory": true,
     "download_url": "https://github.com/NexCardel/Project_Sera/releases/download/v2.3.1/Amas_Sera_Setup_v2.3.1.exe",
     "release_notes": "Summary of what changed in v2.3.1"
   }
   ```
3. Commit and push your changes to GitHub:
   ```bash
   git add .
   git commit -m "Release v2.3.1"
   git push
   ```
4. Create a release tag on GitHub (`v2.3.1`) and upload the new installer executable (`Amas_Sera_Setup_v2.3.1.exe`) matching `download_url`.
5. The next time any employee opens Project Sera, the app will check GitHub, present a mandatory update modal, download the update, and restart into the new version.

