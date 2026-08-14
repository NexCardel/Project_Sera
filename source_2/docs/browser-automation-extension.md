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

### Automatic Cookie Clearing (Every 5 Injections)

- The extension maintains an injection counter stored in `chrome.storage.local`.
- Injections across both automated fill (`injectFillScript`) and manual assist (`injectManualAssist`) increment the counter (`1/5`, `2/5`, `3/5`, `4/5`, `5/5`).
- Upon reaching **5 injections**, the extension automatically invokes `chrome.browsingData.removeCookies()` to wipe browser session cookies.
- This prevents stale session tokens, `"User Already Logged In"`, or state mismatch errors on government compliance portals (Income Tax, GST, MCA, TRACES, etc.).

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
