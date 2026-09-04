# Workspace Layout

The root is intentionally reserved for the application entry points and configuration.

- `main.py`, `database.py`, `sync_peer.py`, and related runtime modules: application code
- `sera_extension/`, `sera_extension_firefox/`: browser-extension source
- `source_2/`: source mirror/archive
- `ui/`, `services/`, `native_host/`: application subsystems
- `docs/`: project documentation and design notes
- `tools/maintenance/`: maintenance and versioning scripts
- `tools/patches/`: one-off patch scripts and patch files
- `data/raw_payloads/`: local payload dumps and analysis data
- `artifacts/extensions/`: packaged browser extensions
- `artifacts/archives/`: source archives
- `build/`, `dist/`, `package_*`, `installer_output/`: existing build and packaging workspaces
- `.restore_points/`: local restore-point material

Generated caches and virtual environments remain in place because tooling may rely on their conventional names.
