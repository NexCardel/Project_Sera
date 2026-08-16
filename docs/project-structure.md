# Project Structure

This document maps the main source files, documentation artifacts, and runtime data locations used by Project Sera. For an interactive visual node graph and file-by-file dependency matrix, see [Codebase Architecture & Node Representation](codebase-architecture-nodes.md) ([PDF Export](codebase-architecture-nodes.pdf)).

---

## Source Tree

```text
project_sera/
|-- main.py                    # App entry point, signal bridge, update checker & event loop
|-- database.py                # SQLCipher DB setup, CRUD, Audit Log, MCL, Cell Formatting, Tracker Dump & Backup/Restore
|-- security.py                # Key derivation (PBKDF2), salt management & Argon2id PIN verification
|-- sync_peer.py               # Built-in Sera Sync LAN peer discovery (UDP 49156) & P2P push (TCP 49157)
|-- version.py                 # Version metadata & GitHub release check/download service
|-- version.json               # GitHub auto-updater release definition
|-- requirements.txt
|-- README.md
|-- GEMINI.md                  # Project rules & AI developer guidelines
|-- docs/                      # Technical documentation & architecture guides
|   |-- codebase-architecture-nodes.md
|   |-- codebase-architecture-nodes.pdf
|   |-- project-structure.md
|   |-- setup.md
|   |-- features-security.md
|   |-- browser-automation-extension.md
|   |-- file-submission-tracker.md
|   |-- build-release.md
|   `-- operations-sync.md
|-- native_host/               # Native Messaging bridge for browser extension
|   |-- com.amanassociates.sera.json
|   |-- host.bat
|   `-- host.py
|-- sera_extension/            # Browser extension companion
|   |-- background.js          # Active tab tracker & IPC relay
|   |-- tracker.js             # Passive DOM observer for ARN capture (File Submission Tracker - FST)
|   |-- content_scripts/
|   |   |-- net_interceptor.js # MAIN world passive Network Response Interceptor (SAD)
|   |   |-- filing_detector.js# Filing detection & extension IPC forwarding
|   |   `-- login.js
|   `-- manifest.json          # Extension permissions & content script definitions
|-- build_tools/
|   |-- build_package.py       # PyInstaller bundle & CRX extension packer
|   `-- installer_setup.iss    # Inno Setup 7 Windows installer script
|-- tests/
|   |-- test_sad_interceptor.html # Offline SAD simulation bench
|   |-- test_dump_injection.py    # Direct TCP 49152 payload injection test script
|   |-- test_cell_formatting.py
|   `-- test_undo_redo_formatting.py
`-- ui/
    |-- extension_listener.py  # Local TCP socket server on port 49152 for extension events
    |-- components/
    |   `-- toast.py           # SeraAlert notification widget
    |-- dialogs/
    |   |-- sera_sync_dialog.py # Sera Sync LAN P2P management dialog
    |   |-- csv_import_dialog.py
    |   |-- mcl_manager_dialog.py
    |   |-- service_manager_dialog.py
    |   |-- settings_dialog.py # General settings & autostart configuration
    |   `-- update_dialog.py
    |-- services/
    |   `-- alert_service.py   # Toast alert message formatter
    |-- shell/
    |   |-- app_shell.py       # Main shell container & tab router
    |   |-- sidebar.py         # Navigation bar & sub-nav items
    |   `-- slide_panel.py     # Animated client detail drawer
    |-- utils/
    |   |-- autostart.py       # Windows Registry PC startup helper
    |   `-- theme.py           # Global QSS Theme system (White cell grids, bl dark headers & scrollbars)
    `-- windows/
        |-- search_window.py   # Client search & Excel Ctrl+C grid & formatting toolbar
        |-- client_detail_window.py # Client workspace & File Submission Tracker (FST)
        |-- tracker_dump_window.py  # Tracker Dump workspace & raw JSON payload inspector
        `-- admin_window.py    # Admin management, restore & Sera Sync
```

---

## Runtime Data Directory

At runtime, the application stores data in `~/AmanAssociates_Sera/`:

```text
~/AmanAssociates_Sera/
|-- master.db            # SQLCipher encrypted database
|-- sera.salt            # Salt file for PBKDF2 key derivation
|-- sera.key             # Local vault keyfile for instant prompt-free auto-unlock
|-- device_identity.txt  # Workstation identity label
`-- native_host/         # Permanent Native Messaging host scripts
    |-- com.amanassociates.sera.json
    |-- host.bat
    `-- host.py
```

Both `master.db` and `sera.salt` belong together and can be pushed across the local network using **Sera Sync** or synchronized using Syncthing.
