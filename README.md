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

The bottom-left audit confirmation system is documented in [Sera_Alert_System_Blueprint.md](../docs/blueprints/Sera_Alert_System_Blueprint.md) and implemented in the desktop shell.

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
pyinstaller build_tools/Amas_Sera.spec
```

Distribute the complete `dist/Amas_Sera/` folder, not the executable alone.
