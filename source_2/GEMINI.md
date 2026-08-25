# User Rules & Project Guidelines

- **Always Ask Before Pushing**: ALWAYS ask for explicit user permission and confirmation before executing `git push` or pushing updates/code changes to GitHub or any remote repository.
- **Always Ask Before Packaging**: ALWAYS ask for explicit user permission and confirmation before building executable packages (`build_package.py`), running PyInstaller, compiling installers (`ISCC.exe` / Inno Setup), or packaging the application for release.
- **Strictly Use Google Material Icons**: ALWAYS and exclusively use Google Material Design icons (`mdi.*` via QtAwesome / Material Symbols) for all UI components, buttons, navigation items, dialogs, tables, and system tray menus across the entire application. Do not use FontAwesome or other icon libraries.
- **Synchronize Extension & Sera SAD Versions**: ALWAYS ensure that `manifest.json` version in `sera_extension/` matches the internal engine version of **Sera SAD** (`net_interceptor.js`) whenever SAD is updated or bumped.
- **Primary Active Workspace Target (`APP`)**: ALWAYS synchronize all code edits, extension scripts, and documentation updates directly to `C:\Users\Nex\Downloads\Project Sera\APP`.
