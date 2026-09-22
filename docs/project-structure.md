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
|-- clipboard_watch.py         # Sera Clipboard Assist (SCA) ambient background listener
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
|   |-- tracker-status-matrix-and-storage.md
|   |-- vsdc-x-engine-guide.md
|   |-- build-release.md
|   `-- operations-sync.md
|-- core/                      # Core OS-level subsystems
|   `-- vsdc/                  # Visual Screen Data Capture (VSDC) optical & UI Automation engine
|       |-- __init__.py
|       |-- vsdc_router.py     # Windows UIA route gater & dual-sensor execution controller
|       |-- vsdc_uia_text.py   # VSDC-X direct UI Automation Chromium accessibility exact-text reader
|       |-- vsdc_assembler.py  # Multi-screen session assembler & completed dataset emitter
|       |-- vsdc_crosshairs.py # Route-specific crosshairs (identity, forms, returns, submission)
|       |-- vsdc_name_parser.py# Taxpayer name normalizer, ligature scrubber & legal name parser
|       |-- vsdc_ocr.py        # Windows.Media.Ocr DirectML C++ engine wrapper
|       |-- vsdc_regex.py      # Deterministic tax regex patterns, form headings, and statutory filing types
|       |-- vsdc_beeper.py     # Zero-leakage local PAN audio beeper
|       |-- vsdc_gemini_parser.py # Gemini Flash AI compliance parser with Privacy Guard
|       |-- vsdc_token_tracker.py # Gemini token and cost usage tracking engine
|       |-- vsdc_session_logger.py # Diagnostic capture logger writing to user profile
|       `-- vsdc_worker.py     # Background QThread polling & frame processing worker
|-- sera_extension/            # Browser extension companion
|   |-- background.js          # Active tab tracker & IPC relay
|   |-- tracker.js             # Passive DOM observer for ARN capture (Sera DOM Detector)
|   |-- content_scripts/
|   |   |-- filing_detector.js # Filing detection & extension IPC forwarding
|   |   `-- login.js
|   |-- sdc/                   # Smart DOM Crosshairs (SDC) Subsystem
|   |   |-- sdc_core.js        # Route listener, in-memory assembler, hands captures to background.js
|   |   |-- sdc_toast.js       # On-screen visual feedback toast
|   |   `-- protocols/         # Portal crosshair definitions
|   |       |-- itr_protocol.js
|   |       |-- gst_protocol.js
|   |       |-- traces_protocol.js
|   |       `-- mca_protocol.js
|   `-- manifest.json          # Extension permissions & content script definitions
|-- tools/                     # Diagnostic, probe, and gap testing utilities
|   |-- vsdc_uia_probe.py      # Standalone Chromium accessibility tree dump tool
|   |-- vsdc_console_watch.py  # Standalone CLI capture runner for live testing
|   |-- vsdc_x_modal_test.py   # Modal dialog UIA traversal test script
|   |-- vsdc_x_router_gap_test.py # GST routing gap simulation harness
|   `-- vsdc_x_itr_router_gap_test.py # ITR form heading, filing type, and gating test script
|-- build_tools/
|   |-- build_package.py       # PyInstaller bundle & CRX extension packer
|   `-- installer_setup.iss    # Inno Setup 7 Windows installer script
|-- tests/
|   |-- test_page_gst_submission.html # Standalone GST filing & modal simulation test harness
|   |-- test_dump_injection.py    # Manual dev tool: sends a mock capture over the WebSocket bridge
|   |-- test_ws_bridge.py         # App<->extension WebSocket bridge tests (origin trust, dispatch, reply routing)
|   |-- test_cell_formatting.py
|   |-- test_undo_redo_formatting.py
|   |-- test_vsdc_assembler.py    # VSDC session assembly, filing types, & dataset sealing tests
|   |-- test_vsdc_crosshairs.py   # Crosshair trigger route & UIA/OCR consensus tests
|   |-- test_vsdc_name.py         # Taxpayer legal name normalization tests
|   |-- test_vsdc_regex.py        # Statutory regex, form headings & filing type tests
|   |-- test_vsdc_hud_pill.py     # HUD pill pulse-on-capture & lifecycle tests
|   |-- test_vsdc_beeper.py       # PAN beeper unit tests
|   `-- test_vsdc_gemini_enricher.py # Gemini AI structured compliance enricher tests
`-- ui/
    |-- ws_bridge.py           # Local WebSocket server (48765-48768) for extension events
    |-- components/
    |   |-- toast.py           # SeraAlert notification widget
    |   `-- vsdc_hud_pill.py   # Floating ambient HUD Pill overlay widget
    |-- dialogs/
    |   |-- ai_settings_dialog.py # Gemini AI settings, API key, & live token meter modal
    |   |-- sera_sync_dialog.py # Sera Sync LAN P2P management dialog
    |   |-- csv_import_dialog.py
    |   |-- mcl_manager_dialog.py
    |   |-- service_manager_dialog.py
    |   |-- settings_dialog.py # General settings & autostart configuration
    |   |-- unified_settings_dialog.py # Unified Settings Hub with tabbed drawer navigation
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
        |-- tracker_dump_window.py  # Tracker Dump workspace, status pills, & raw JSON payload inspector
        `-- admin_window.py    # Admin management, restore & Sera Sync
```

---

## Runtime Data Directory

At runtime, the application stores data and session state in `%USERPROFILE%\AmanAssociates_Sera\`:

```text
%USERPROFILE%\AmanAssociates_Sera\
|-- master.db            # SQLCipher encrypted client database
|-- rawPayload.db        # SQLite storage for tracker_dump filings
|-- sera.salt            # Salt file for PBKDF2 key derivation
|-- sera.key             # Local vault keyfile for instant prompt-free auto-unlock
|-- device_identity.txt  # Workstation identity label
|-- gemini_token_stats.json # Gemini AI token usage & cost statistics
`-- Vsdc_Captures\       # Local diagnostic captures (redirected to user profile)
```

Both `master.db` and `sera.salt` belong together and can be pushed across the local network using **Sera Sync** or synchronized using Syncthing.
