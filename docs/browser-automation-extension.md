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

- **Ambient Trigger**: When staff copy a User ID (PAN, GSTIN, UID) from Excel, Sheets, Notepad, or any client roster, Sera silently arms the matching client password in memory with a 45-second TTL timer.
- **Zero-Touch Autofill**: When the user pastes that User ID into any recognized portal login field in the browser, the password field autofills itself immediately.
- **Floating Confirmation Banner**: Simultaneously displays a sleek, non-intrusive floating card in the top-right corner of the page showing the client business name, owner name, and `"Password was autofilled for <Portal>"` confirmation.
- **Privacy & Safety**: Never persists raw clipboard text, scopes checkbox clicks away from `"Show password"` controls, and can be toggled via **Settings → General**.

## Sera Manual Tracker Injection (SMTI / Manual Assist)

- **Obsidian & Emerald UI Widget**: An interactive floating card widget rendered in an isolated Shadow DOM on the web page.
- **Independent Field Injection**: Allows staff to independently trigger `👤 Inject User ID` and `🔑 Inject Password` with immediate visual click confirmation (`✓ Injected`).
- **Countdown Progress Bar**: Displays a live 30-second countdown indicator bar before auto-dismissal.
- **Masking Safeguards**: Keeps passwords fully masked (`••••••••`) with zero plaintext exposure in DOM attributes or screen recordings.

## Sera FST: API Detector (SAD) & DOM Detector (DOM)

- **Sera SAD (`net_interceptor.js`)**:
  - Injected into the page's `MAIN` execution world at `document_start` to intercept `fetch()` and `XMLHttpRequest` traffic passively.
  - Automatically captures filing confirmations, e-verifications, statutory forms, challans, and full multi-year filed return histories from ITD, GST, and TRACES backends.
  - Dispatches `CustomEvent("SeraFSTApiCapture")` containing normalized Ack/ARN numbers, Assessment Years, Form types, and client PANs.
- **Sera Filing Detector (`filing_detector.js`)**:
  - Runs in the extension's `ISOLATED` world to bridge custom DOM events to `chrome.runtime.sendMessage()`.
  - Includes disconnection safety guards (`chrome.runtime?.id`) to gracefully survive extension reloads.
- **Sera DOM (`tracker.js`)**:
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
