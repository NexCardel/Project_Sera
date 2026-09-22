# Changelog

All notable changes to **Project Sera** are documented in this file.

---

## [2.10.0] - 2026-09-22

### Added
- **Sera Global Tracker (SGT) Core Engine**:
  - Implemented the crosshair-independent passive SGT engine (`core/sgt/sgt_shadow.py`) reading pages via Windows UI Automation with fallback to DirectML OCR on canvas-painted web portals.
  - Introduced declarative, zero-code field extraction rules via `core/sgt/sgt_fields.json` backed by `core/sgt/sgt_specs.py` and `core/sgt/sgt_resolver.py`.
  - Added native **TAN (Tax Deduction and Collection Account Number)** field specification with strict formatting pattern `(?<![A-Z0-9])([A-Z]{4}[0-9]{5}[A-Z])(?![A-Z0-9])`, label proximity window, and automated positive/negative test suites.
  - Implemented **SGT Shadow Mode**: Runs passively alongside VSDC/SDC without interference, writing isolated datasets tagged `SGT_shadow` into `tracker_dump` (`rawPayload.db`).
- **SGT Health & Differential Replay Tooling**:
  - `core/sgt/sgt_corpus.py`: Records live portal page text lines locally into `~/AmanAssociates_Sera/corpus/` for safe offline replay without persisting sensitive credentials.
  - `core/sgt/sgt_health.py`: Computes daily telemetry metrics (`SpecStats`), tracks OCR jump ratios (canvas detection), and flags silent spec drift.
  - `core/sgt/sgt_replay.py` & `tools/sgt_replay.py`: CLI tool for replaying recorded sessions, establishing baselines, and diffing candidate spec changes before production rollout.
  - `tests/sgt_golden/`: Deterministic golden session fixtures and reliability test suite verifying recovery under heavy OCR noise, line drops, and shuffles.
- **WebSocket IPC Bridge (`ui/ws_bridge.py`)**:
  - Hosted local WebSocket server dynamically binding to ports `48765-48768` with origin authentication and handshake verification.
  - Replaces the legacy Chrome/Firefox Native Messaging bridge (`native_host/`) and local HTTP listener (`ui/extension_listener.py`), eliminating all Windows Registry host dependencies.
- **Process Memory Diagnostic Logging (`core/memlog.py`)**:
  - Added low-overhead Win32 `kernel32` memory point logging tracking working set (`ws`) and private commit (`priv`) across startup phases, window launches, and background execution.
  - Rolling log persistence to `~/AmanAssociates_Sera/logs/memory.log`.
- **SCA Subsystem Modularization**:
  - Extracted Sera Clipboard Assist (SCA) autofill coordination into `sera_extension/sca/sca_coordinator.js` for both Chromium and Firefox.
- **Tracker Dump & Admin UI Enhancements**:
  - Added `SGT_shadow` badge indicators and Source filtering (All / Hide SGT / SGT Only) to `TrackerDumpWindow`.
  - Upgraded `AdminWindow` and settings dialogs with real-time health inspection and diagnostic triggers.

### Changed
- **Direct SDC Payload Delivery**:
  - `sdc_core.js` no longer flushes telemetry payloads over raw HTTP loopbacks (`127.0.0.1:49152`). Payloads are now handed to `background.js` via `chrome.runtime.sendMessage` and forwarded over the secure WebSocket bridge.
- **Documentation Overhaul**:
  - Updated `README.md`, `project-structure.md`, `codebase-architecture-nodes.md`, `browser-automation-extension.md`, `build-release.md`, and `sera-clipboard-assist.md` to reflect complete deprecation of Native Messaging and introduction of SGT and WebSocket IPC.

### Removed
- **Native Host Subsystem**:
  - Permanently removed `native_host/` (`host.py`, `host.bat`, `register_native_host.bat`, `com.amanassociates.sera.json`).
  - Removed native messaging permissions and registry keys from `build_tools/installer_setup.iss`.
- **Extension Listener**:
  - Removed `ui/extension_listener.py` and its legacy raw socket server.

### Fixed
- **Service Configuration Preset Lock**:
  - Resolved an aggressive UI refill loop in `ServiceEditDialog` where clearing the Login URL or selector fields while typing portal keywords would constantly regenerate default URLs before users could customize them. Applied a `_last_preset` latch and focus-aware guards.
