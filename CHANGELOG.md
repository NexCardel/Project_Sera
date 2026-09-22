# Changelog

## [2.10.0] - 2026-09-22

### Added
- **Sera Global Tracker (SGT) Core Engine**: Introduced the crosshair-independent SGT engine (`SGT_shadow`) with an accompanying config-driven spec system (`sgt_fields.json`).
- **SGT Shadow Mode**: Deployed SGT in a passive "shadow mode" capable of routing parallel datasets into the tracker dump workspace alongside VSDC/SDC without interference.
- **WebSocket IPC Bridge**: Completely replaced Chrome/Firefox Native Messaging and the local HTTP polling loop (port 49152) with a resilient `ui/ws_bridge.py` WebSocket server running on ports `48765-48768`.
- **SCA Reorganization**: Extracted Sera Clipboard Assist (SCA) autofill scripts into their own dedicated `sca/` module directories in both Chromium and Firefox extensions.
- **Service Configuration Presets**: Automatic detection of portals and injection of optimal selectors/login flows via the Service Manager Dialog (with fix for aggressive autofill un-locking).

### Changed
- **Direct SDC Payload Delivery**: `sdc_core.js` no longer flushes telemetry payloads over raw HTTP loopbacks. All tracking data is securely forwarded to `background.js`, which negotiates transmission across the WebSocket bridge. 
- **Documentation Overhaul**: Updated `browser-automation-extension.md`, `project-structure.md`, `build-release.md`, `codebase-architecture-nodes.md`, and others to reflect the total removal of Native Messaging and the adoption of WebSockets.

### Removed
- **Native Host Directory**: Permanently removed the `native_host/` registry scripts, `host.py`, and `host.bat`. Project Sera no longer depends on Windows registry mutations to communicate with its browser companions.
- **Extension Listener**: Removed `ui/extension_listener.py` and its legacy `QThread` HTTP socket handler.

### Fixed
- **Service Dialog Locking**: Remedied an aggressive behavior in the Service Configuration dialog that would endlessly reconstruct prefilled URLs when users attempted to clear text inputs for manual portal configurations.
