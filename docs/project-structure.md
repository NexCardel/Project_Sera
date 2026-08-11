# Project Structure

This document maps the main source files and runtime data locations used by Project Sera.

## Source Tree

```text
project_sera/
|-- main.py                    # App entry point and IPC signal handler
|-- database.py                # SQLCipher DB setup, CRUD, Audit Log, Filing Status, Backup/Restore
|-- security.py                # Key derivation and PIN hashing
|-- automation.py              # Playwright browser autofill and extension native-host bridge
|-- requirements.txt
|-- README.md
|-- tests/
|   `-- test_portal_success.html
|-- native_host/               # Native Messaging bridge for browser extension
|   |-- com.amanassociates.sera.json
|   |-- host.bat
|   |-- host.py
|   `-- register_native_host.bat
|-- sera_extension/            # Browser extension companion
|   |-- background.js          # Active tab tracker, tab-close detection, IPC relay
|   |-- tracker.js             # Passive DOM observer for ARN capture and in-page toast
|   |-- manifest.json          # Extension permissions and content script definitions
|   |-- config.json
|   `-- content_scripts/
`-- ui/
    |-- extension_listener.py   # Threaded TCP server socket on port 49152 for extension events
    |-- components/
    |   `-- toast.py            # SeraAlert notification widget
    |-- dialogs/
    |   `-- filing_confirmation_dialog.py
    |-- services/
    |   `-- alert_service.py
    |-- shell/
    `-- windows/
```

## Runtime Data

At runtime, the app creates:

```text
~/AmanAssociates_Sera/
|-- master.db      # Encrypted database; sync this via Syncthing
`-- sera.salt      # Non-secret key-derivation salt; sync this too
```

Both files need to be present on every employee machine through Syncthing, pointed at the same `AmanAssociates_Sera` folder. For step-by-step instructions, see `../docs/Syncthing_Setup_Guide.md`.
