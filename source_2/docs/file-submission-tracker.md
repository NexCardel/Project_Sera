# File Submission Tracker (FST)

The File Submission Tracker (FST) automatically captures and records tax return filings, Ack/ARN numbers, and period submission statuses directly into the vault's `filing_status` table.

> [!NOTE]
> The legacy DRS Engine UI components, DRS Dashboard window, and DRS Manager dialogs have been removed per project specifications to streamline the user interface. The core File Submission Tracker (FST) remains fully operational.

## Workflow

```text
[Desktop App: main.py] -- Autofill Payload --> [Native Host / TCP 49153] --> [Extension: background.js]
                                                                               |
                                                                               v
                                                                       Injects active session
                                                                               |
                                                                               v
[filing_status Table in DB] <-- TCP 49152 -- [ExtensionListener] <-- Filing Result -- [tracker.js on Portal]
        ^
        |
[FilingConfirmationDialog] <-- Uncertain Result when no ARN is captured before tab close/logout
```

## Tier 1: Automated ARN Capture

`tracker.js` is injected into portal tabs when an active autofill payload is set.

It monitors the page DOM with `MutationObserver` for:

- Success text such as `submitted successfully`.
- ARN identifiers such as `ARN:` or `Transaction ID:`.

When detected, it shows a green browser toast and sends `filing_result` to `ExtensionListener` on TCP port 49152.

## Tier 2: Fallback Confirmation Modal

If the user closes the browser tab or logs out without an ARN being captured during an active session, `background.js` sends an `uncertain_result` to the desktop app.

The desktop app shows `FilingConfirmationDialog` as a system-modal, always-on-top confirmation window. It includes period selection buttons based on filing frequency: Monthly, Quarterly, or Annual.

On confirmation, the app writes a `submitted` record to the `filing_status` table with timestamp and active staff attribution, then reloads `ClientDetailWindow`.

## Verification

Fallback test:

1. Click Autofill.
2. Close the browser tab without filing.
3. Confirm the desktop app opens `FilingConfirmationDialog`.

Automated capture test:

1. Click Autofill.
2. Open `tests/test_portal_success.html`.
3. Confirm the browser shows the success toast.
4. Confirm the desktop app pre-populates ARN `AA27032419827364`.
5. Close the tab and confirm no fallback prompt appears.
