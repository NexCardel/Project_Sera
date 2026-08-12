# Project Sera

Internal credential vault, browser autofill tool, and File Submission Tracker for Aman Associates.

This README is the quick orientation page. The detailed implementation notes have been split into focused documents:

- [Project Structure](docs/project-structure.md)
- [Setup](docs/setup.md)
- [Features and Security Controls](docs/features-security.md)
- [Browser Automation and Extension](docs/browser-automation-extension.md)
- [File Submission Tracker](docs/file-submission-tracker.md)
- [Build and Release](docs/build-release.md)
- [Operations and Sync](docs/operations-sync.md)

For the current visual system, sidebar/navigation states, client-detail layout, and screen-by-screen styling rules, see [Sera_UI.md](Sera_UI.md).

## Version 2.3.3 Features

- **Excel-Style Grid Copying (`Ctrl+C`)**: Select cells or cell blocks and copy formatted data directly to Excel with a 500 ms green highlight flash (`#2E9B5F`).
- **Master Column List (MCL) ID Tokens**: Added `ID` field type (`"ID (Primary Key / Auto-Serial)"`) with single-column exclusivity, auto-serial numbers, and backend Client ID tokens (`CLI-XXXXX`) for anonymous cloud identification.
- **Client Detail Token Badge**: Interactive `Token: CLI-XXXXX` badge in client detail headers with click-to-copy capability.
- **Automatic Extension Cookie Clearing**: Extension automatically clears browser session cookies after every 5 injections to eliminate stale session errors on government portals.
- **Smart Duplicate Purging**: Case-insensitive, punctuation-insensitive string normalization for duplicate detection, ignoring serial number columns.
- **Modal Dialog Protection**: Prevents active slide panels from closing during modal dialog interactions.
- **Branding Integration**: Official `icon_here` branding assets applied across application window, taskbar, sidebar, splash loading dialog, companion extension, and setup installer executable icon.

## Quick Start

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
python main.py
```

First run asks for a staff label/name and a master password. The master password is never stored; it is used to derive the SQLCipher database key.

## Runtime Data

Project Sera stores runtime data in:

```text
~/AmanAssociates_Sera/
|-- master.db
`-- sera.salt
```

Both files must sync to every employee machine via Syncthing. They belong together: `master.db` is encrypted, and `sera.salt` is required to derive the key from the shared master password.

## Build

```bash
venv\Scripts\python build_tools\build_package.py
& "C:\Program Files\Inno Setup 7\ISCC.exe" build_tools\installer_setup.iss
```

Distribute the compiled installer `installer_output\Amas_Sera_Setup_v2.3.3.exe` or the standalone bundle in `package_dist\Amas_Sera\`.
