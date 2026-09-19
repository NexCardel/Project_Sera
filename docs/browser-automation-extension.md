# Browser Automation and Extension

Project Sera supports browser automation through `automation.py`, Playwright, and the companion Chrome/Edge extension.

## Native Host Bridge

`automation.py` routes extension-mode services to `sera_extension` through the local TCP bridge in `native_host/host.py` on port 49153.

Settings changes, such as enabling or disabling the filing tracker, are broadcast from the desktop app to the native host so the browser extension updates immediately.

If the extension is asleep or the browser is closed when Autofill is clicked, `automation.py` opens the login URL, wakes Chrome/Edge background service workers, and retries connection for up to 10 seconds.

## Extension Injection & Cookie Management

`sera_extension/background.js` injects fill logic directly into the page using `chrome.scripting.executeScript`.

The injected `fillCredentialsInPage()` function:

- Fills the PAN/UID field once via a 1-second poll.
- Clicks the secure-access `mat-checkbox` or `mat-mdc-checkbox` if needed.
- Dispatches Angular-compatible `CompositionEvent` and `InputEvent` events so reactive forms register password input cleanly.
- Auto-clicks the Continue/Submit button 600 ms after password fill.

## Sera Clipboard Assist (SCA — Ambient Password Autofill)

- **Multi-Identifier Candidate Array (`candidate_uids`)**: Gathers all potential client identifiers (PAN, full GSTIN, custom Portal User IDs, and Client Tokens) into a unified matching pool during arming, ensuring autofill triggers seamlessly whether staff copy a PAN or a full 15-character GSTIN.
- **Service Configuration Fallback**: If a client lacks explicit rows in `client_services`, SCA falls back automatically to all active portal services (`all_services`), ensuring zero silent arming drops.
- **Single-Page AngularJS & SPA Event Dispatching**: Emits native `InputEvent` (`insertText`), `input`, `change`, and `blur` events so reactive frameworks (like AngularJS on GST Portal `services.gst.gov.in` and Angular 17 on Income Tax 2.0) synchronize model bindings (`$viewValue`) immediately and activate submit buttons.
- **MV3 Tab Host Disambiguation**: Resolves active portal host across `sender.tab.url`, `sender.url`, `pendingUrl`, and `req.portal` with explicit subdomain matching across GST (`services.gst.gov.in`, `gst.gov.in`) and Income Tax domains.
- **Ambient Trigger & Timer**: Silently arms matching client credentials in memory with a 45-second TTL timer upon copy.
- **Floating Confirmation Banner**: Simultaneously displays a sleek, non-intrusive floating card in the top-right corner of the page showing the client business name, owner name, and `"Password was autofilled for <Portal>"` confirmation.
- **Privacy & Safety**: Never persists raw clipboard text, scopes checkbox clicks away from `"Show password"` controls, and can be toggled via **Settings → General**.

## Sera Manual Tracker Injection (SMTI) & Smart Credential Combinations (SCC / MECP)

- **Obsidian & Emerald UI Widget**: An interactive floating card widget rendered in an isolated Shadow DOM on the web page.
- **Independent Field Injection**: Allows staff to independently trigger `👤 Inject User ID` and `🔑 Inject Password` with immediate visual click confirmation (`✓ Injected`).
- **Smart Credential Combinations (SCC / MECP)**:
  - Injects candidate password formula variations (dynamic PAN-derived formulas e.g. `Pan@123`, `Pan#2024`, plus 2 configurable static fallback strings) directly onto password fields.
  - Features unmasked cleartext buttons for instant single-click formula testing without losing focus.
  - Automatically tags the client record in `master.db` with `"Password verified via SCC"` upon successful login navigation.
- **Countdown Progress Bar**: Displays a live 30-second countdown indicator bar before auto-dismissal.
- **Masking Safeguards**: Keeps passwords fully masked (`••••••••`) with zero plaintext exposure in DOM attributes or screen recordings.

## Sera FST: SDC DOM Crosshair Assembler & Visual Capture

> [!NOTE]
> **Sera SAD & SDS Retirement**:
> - **Sera SAD** (Network API Interceptor / `net_interceptor.js`) has been permanently retired and removed from the active workspace.
> - **Sera SDS** (Dataset Scanner / `sds_core.js` and compromise vendor libraries) has been permanently retired and removed from the extension.
> 
> Filing capture operations rely exclusively on **Sera SDC (DOM Crosshairs)** within the extension and **Sera VSDC / VSDC-X (Visual Screen Data Capture & UI Automation)** at the OS desktop layer.

- **Sera SDC Assembler (`sdc_core.js` + protocols)**:
  - **In-Memory Aggregation**: Buffers all crosshair events during an active portal session into `sdc_assembler` without emitting premature fragmented entries.
  - **Multi-Dataset Identity**: Keeps one capture per normalized `GSTIN/PAN + filing type + period`; revisits update the same dataset while different GST forms or periods remain in `raw_payload.assembler_captures`.
  - **Atomic Session Flush**: Emits the buffered collection once on logout, timeout, client switch, or abrupt tab close. The tracker dump is therefore updated at session termination, not at each individual crosshair hit.
  - **Compressed Transport**: Optionally sends the complete lossless assembler envelope as `filing_result_compressed` (`gzip+base64`); the desktop listener restores it to the normal `filing_result` contract before database insertion.
  - **Serialized Session Persistence**: Queues browser storage writes and avoids reloading stale session snapshots during SPA route changes, preventing earlier datasets from being replaced by a later capture.
  - **Portal-Scoped Storage**: Completely isolates session memory keys across portals (`__SDC_SESSION_ITR__`, `__SDC_SESSION_GST__`, `__SDC_SESSION_TRACES__`, `__SDC_SESSION_MCA__`).
  - **Direct HTTP Loopback (Primary)**: Emits final atomic session payloads directly to `http://127.0.0.1:49152` via `fetch()`, bypassing Manifest V3 service worker sleep cycles with automatic fallback to Chrome Runtime Native Messaging.
  - **Double-Flush & Context Protection**: Guarded with `_assembler_flushed` lock and client PAN context switch monitors to prevent duplicated tracker dump rows.
  - **Ledger Card Milestone Resolver**: Evaluates milestone timelines on `view-filed-returns` to distinguish verified returns from "e-Verify Later" submissions.

### SDC Multi-Dataset Delivery Incident — 2026-09-03

The GST multi-dataset flow was not visible in Tracker Dump because the final compressed assembler message was not reaching the desktop ingestion path. The desktop listener was not active on loopback port `49152`, and its decoder also lacked the `base64` import required for `gzip+base64` messages. The source decoder and dataset expansion are now corrected. Testing must use a restarted desktop build and a reloaded extension; otherwise an older process can continue to discard or ignore the final envelope.

- **Sera DOM Tracker (`tracker.js`)**:
  - Monitors visual on-screen confirmation banners using `MutationObserver` as a fallback for legacy server-rendered HTML pages.

## Native Messaging on Another PC

The browser extension requires the native messaging host to be registered on each Windows machine.

Distribute the complete PyInstaller output folder, including `native_host/`, then run:

```bat
native_host\register_native_host.bat
```

Run it once as the logged-in Windows user. Start the desktop app once before testing the extension so it can also register the host automatically.

The packaged build includes `native_host.host`, allowing `Amas_Sera.exe --native-host` to stay alive as Chrome's stdio bridge.

If Chrome reports `Native host has exited`, check `native_host\host_error.log` and confirm that the registry entry points to the `com.amanassociates.sera.json` inside the copied application folder.

## Packaged Browser Deployment

The Windows installer deploys the signed `ProjectSeraCompanion.crx` for both Google Chrome and Microsoft Edge. It registers the extension under each browser's machine-wide `Extensions` registry key and the native host under `NativeMessagingHosts`, so staff do not need to manually load an unpacked extension.

The extension manifest (`manifest.json`) includes permissions for `"nativeMessaging"`, `"storage"`, `"activeTab"`, `"scripting"`, `"tabs"`, `"alarms"`, `"browsingData"`, and `"cookies"`.

The installer requires administrator approval. After installation, restart Chrome or Edge if it was already open; the Sera Companion extension is then available and can connect to the desktop app through `com.amanassociates.sera`.
