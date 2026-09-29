# Browser Automation and Extension

Project Sera supports browser automation through `automation.py`, Playwright, and the companion Chrome/Edge extension.

## WebSocket Bridge (2026-09-22)

`automation.py` routes extension-mode services to `sera_extension` through `ui/ws_bridge.py`, one local WebSocket server the app hosts on the first free port from `48765-48768`. `background.js` holds the connection and is the only part of either extension allowed to talk to the app; a content script or an injected card goes through it via `chrome.runtime.sendMessage`, never straight to a port. Only the Sera extension's own origin may connect - see `ui/ws_bridge.py` for how that is checked.

This replaces Chrome/Firefox Native Messaging (`native_host/`, removed) and the old direct-HTTP fallback on port 49152 (`ui/extension_listener.py`, removed): both required per-browser registry setup, sat inside Windows' own dynamic port range (so another program could already be holding the port at random), and stalled every request on a hand-rolled server. See `docs/app-extension-communication-report.md` for the full comparison and the reasoning behind the switch.

Settings changes, such as enabling clipboard assist or SCA scope, are broadcast from the desktop app to every connected browser so the extension updates immediately.

If the extension is asleep or the browser is closed when Autofill is clicked, `automation.py` opens the login URL, wakes Chrome/Edge background service workers, and retries the connection for up to 10 seconds.

## Extension Injection

`sera_extension/background.js` injects fill logic directly into the page using `chrome.scripting.executeScript`, only after staff press a button. It never clicks, submits, scrolls or dismisses anything on the portal, and it does not touch cookies.

The injected `fillCredentialsInPage()` function:

- Fills the PAN/UID field once via a 1-second poll.
- Stops after filling; staff press Continue/Login themselves.
- Dispatches Angular-compatible `CompositionEvent` and `InputEvent` events so reactive forms register password input cleanly.

## Sera Clipboard Assist (SCA — Ambient Password Autofill)

- **Multi-Identifier Candidate Array (`candidate_uids`)**: Gathers all potential client identifiers (PAN, full GSTIN, custom Portal User IDs, and Client Tokens) into a unified matching pool during arming, ensuring autofill triggers seamlessly whether staff copy a PAN or a full 15-character GSTIN.
- **Service Configuration Fallback**: If a client lacks explicit rows in `client_services`, SCA falls back automatically to all active portal services (`all_services`), ensuring zero silent arming drops.
- **Single-Page AngularJS & SPA Event Dispatching**: Emits native `InputEvent` (`insertText`), `input`, `change`, and `blur` events so reactive frameworks (like AngularJS on GST Portal `services.gst.gov.in` and Angular 17 on Income Tax 2.0) synchronize model bindings (`$viewValue`) immediately and activate submit buttons.
- **MV3 Tab Host Disambiguation**: Resolves active portal host across `sender.tab.url`, `sender.url`, `pendingUrl`, and `req.portal` with explicit subdomain matching across GST (`services.gst.gov.in`, `gst.gov.in`) and Income Tax domains.
- **Ambient Trigger & Timer**: Silently arms matching client credentials in memory with a 45-second TTL timer upon copy.
- **Floating Confirmation Banner**: Simultaneously displays a sleek, non-intrusive floating card in the top-right corner of the page showing the client business name, owner name, and `"Password was autofilled for <Portal>"` confirmation.
- **Privacy & Safety**: Never persists raw clipboard text, runs only on the approved portals, and can be toggled via **Settings → General**.

## Sera Manual Assist & Manual Copy (SMTI & MECP)

- **Obsidian & Emerald UI Widget**: An interactive floating card widget rendered in an isolated Shadow DOM on the web page.
- **Independent Field Injection (SMTI)**: Allows staff to independently trigger `👤 Inject User ID` and `🔑 Inject Password` with immediate visual click confirmation (`✓ Injected`). Per-tab assist lock prevents SCA from filling in SMTI tabs until the widget is dismissed.
- **Manual Copy Card (MECP)**: Displays User ID and/or Password copy buttons in an isolated card with a countdown timer bar (pauses on hover). The card closes after both credentials are copied or the timer expires. In SCC mode the desktop sends the Income Tax password rows; that card has no timer and closes only on ✕ or when the desktop closes it. The card uses a closed shadow root and stores nothing.
- **Per-Tab Assist Lock**: The extension maintains a per-tab map of open assist widgets, blocking SCA from firing in tabs where SMTI or MECP is active.
- **Masking Safeguards**: Keeps passwords fully masked (`••••••••`) with zero plaintext exposure in DOM attributes or screen recordings.

## Filing Capture

Filing capture is handled exclusively by the desktop application (**Sera VSDC / VSDC-X**) via UI Automation on Windows. The browser extension does not participate in filing capture and carries no tracking code.

See `docs/autofill_tweaks/` for details on the Smart Credential Combinations (SCC) desktop-driven flow for Income Tax portal password discovery and vault tagging.

## Setting Up on Another PC

Nothing to register. Install the app and load/force-install the extension (the Windows installer does the latter automatically, see below); `background.js` finds the app on its own by trying `48765-48768` in order, and reconnects if the app restarts. There is no registry key, no host manifest, and no per-browser setup step.

## Packaged Browser Deployment

The Windows installer deploys the signed `ProjectSeraCompanion.crx` for both Google Chrome and Microsoft Edge. It registers the extension under each browser's machine-wide `Extensions` registry key, so staff do not need to manually load an unpacked extension.

The extension manifest (`manifest.json`) includes permissions for `"storage"`, `"activeTab"`, `"scripting"`, `"tabs"`, and `"alarms"`.

The installer requires administrator approval. After installation, restart Chrome or Edge if it was already open; the Sera Companion extension is then available and connects to the desktop app on its own over the WebSocket bridge.
