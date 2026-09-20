# VSDC247 — the crosshair-independent submission safety net

**Subsystem:** `core/vsdc/vsdc247.py` (logic), wired into `core/vsdc/vsdc_router.py`
**Engine:** VSDC (screen pixels + Windows OCR) — deliberately **not** VSDC-X
**Scope:** the Income Tax e-Filing portal and the GST portal, nothing else

## Why it exists

Crosshairs recognise a page by its URL. URLs change, and pages get missed. VSDC247 does not care
what page it is on: on **every** page of the two portals it watches for the one thing that always
means the same — a submission confirmation carrying an ARN / acknowledgement number — and saves it.
It is a safety net under the crosshair pipeline, not a replacement for it.

It is strictly passive, like the rest of VSDC: it only reads the screen. It never clicks, scrolls,
types or injects anything.

## Scope gate (applies to all of VSDC and VSDC-X)

Nothing — screenshot, OCR, UI Automation page read, identity extraction, HUD event — may run for a
page outside the two portals. `core/vsdc/vsdc_scope.py` decides on the **parsed hostname** of the
address bar, never on a substring: only `incometax.gov.in` / `gst.gov.in` and their subdomains.

It refuses look-alikes (`incometax.gov.in.evil.example`, `evilincometax.gov.in`), user-info tricks
(`https://eportal.incometax.gov.in@evil.example/`), URLs that merely embed a portal URL in a query
string, and text still being typed into the address bar. Local test pages (`file://`, `localhost`,
drive-letter paths) are refused unless `VSDC_ALLOW_LOCAL_TEST=1`, which only the tests and dev tools set.

If the address bar momentarily cannot be read, a window verified on a portal within the last 8 seconds
and still titled like one stays trusted; a window never verified gets nothing (a YouTube video titled
"Income Tax Return guide" is not the portal). When a readable URL matches no crosshair, the window
title is **not** used to guess one — that page is unmapped, and belongs to VSDC247.

### If a portal moves to a new domain

The gate refuses any host it does not know, so a portal that changes domain would stop every capture
*silently*. Two safeguards:

* **Allowlist file, no rebuild.** Put a `vsdc_scope.json` next to the program (repo root when running from
  source), then restart the app:
  `{"income_tax": ["newportal.example.gov.in"], "gst": []}`. An entry adds a registered domain (its
  subdomains included) to the portal it is listed under. Wildcards, URLs, bare suffixes (`gov.in`, `nic.in`)
  and anything with fewer than three labels are rejected with a console line, so a typo cannot widen the scope.
* **Tripwire.** For a host under `gov.in` / `nic.in` that is *not* listed, VSDC reads the names of the
  **first 30 UI elements only** (at most 3 tries per host per run) and looks for the portal's own logo
  ("E Filing logo", "Goods and Services Tax Home"). If it is there, the HUD shows one amber prompt,
  *"Portal address looks new – VSDC paused"*, and the console names the host. The lines are dropped at once;
  nothing is captured or stored, and the scope is **not** widened - an admin still has to add the domain.
  Hosts outside `gov.in` / `nic.in` are never read.

## What counts as a submission

A frame is analysed by `analyze_frame()`. Every rule below exists because of a real mistake:

| Signal | Rule |
| :--- | :--- |
| **Identifier** | An ITR 15-digit ack, or a GST ARN. Must sit **next to a label** ("Acknowledgement Number", "ARN", …) — never a bare number. |
| **Ack validation** | An ITR ack ends with its own filing date as `DDMMYY` (checked against every ack in the live dumps). A live submission's ack must carry **today's** date (±1 day). A reference to an old return — the original-return ack a revised-return wizard prints — carries an old date and is rejected. An ack whose date is not a real date is a misread and is rejected. |
| **One identifier** | More than one distinct identifier on screen — labelled or not, fresh or stale — is a **list** (View Filed Returns, filing history, the e-Verify picker), never one submission. Decided *before* any date filtering. |
| **Success wording** | "has been submitted / filed successfully", "ARN generated", … within 4 lines of the identifier. Wording that describes the *future* ("you will be notified once…") does not count. |
| **Vetoes** | "original / previous return" context, failure wording, draft / "saved" wording, the e-Verify stepper labels. |
| **Corroboration** | form on screen (+10), period (+5), the identifier or wording sitting **inside a green success box** (+15). |

Score → tier: **CERTAIN** (≥ 85, needs the success wording) is saved. **PROBABLE** (≥ 55) only raises a
HUD prompt and is never saved on one read. Below that, or vetoed, it is logged and ignored.

A certain capture scoring ≥ 95 is saved from a single frame (a success toast can vanish in seconds).
Anything weaker must be read **again** on a later frame first (*temporal agreement*); agreement can never
promote a bare number that has no success wording.

**GST ARN shape.** Every ARN seen so far starts with two letters followed by digits. That is an observed
pattern, not a documented rule, so it is treated as evidence and not as a gate: a number sitting right after an
"ARN" / "Application Reference Number" label that breaks the pattern is still noticed, but is capped at a
*possible-submission* prompt, is never promoted by a second read, and is never saved. (Only tried when no
usual-shaped ARN is on screen, so it cannot turn one ARN into a "list".) Each portal only reads its own
identifier: the ITR portal ignores ARNs, the GST portal ignores 15-digit acks.

ARN-bearing submissions are captured across the board — returns, and also applications (registration,
refund, …) — not only returns.

## Never missing

* A cheap change check (32×32 thumbnail hash) and green-region check run every tick. Full-frame OCR runs on
  every **changed** frame (rate-limited to 0.3 s) and **immediately** when a green region appears.
  Measured here: OCR ≈ 45–100 ms, green detection ≈ 9 ms, change hash ≈ 7 ms.
* The very first frame a window is ever seen gets one read (a confirmation may already be on screen when VSDC first looks); after that a static page costs nothing.
* A frame the crosshair pipeline has just handled is marked *seen* without being re-read.

## What is dispatched

`assembler.build_247_payload()` builds the payload. It does **not** require a complete dataset: an ARN is
worth saving even when the client, form or period is not yet known. It is kept out of the crosshair
pipeline's `records`, and carries **only structured fields** — never page text (`raw_text` is empty; the
`vsdc247` block holds the score and evidence labels).

* **Client known:** dispatched attributed; HUD shows "*<form> Submission Captured*".
* **Client not known:** dispatched **unattributed** (`pan: ""`, `identity_resolved: false`); the tracker stores it
  as `Pending_<ARN>` and the HUD shows an amber "*Submission captured – client unknown*" prompt. As soon as
  the window learns who the client is, the **same ARN is re-sent attributed** and the tracker **replaces** the
  pending row (`database.insert_tracker_dump`), rather than dropping it as a duplicate or leaving two rows.
* **Probable:** amber "*Possible submission seen – not saved*" prompt, once per ARN per 10 minutes.
* An ack/ARN already dispatched — by the crosshair pipeline or by VSDC247 — is never sent again.
* `capture_method` is `VSDC247_itr_ack` / `VSDC247_gst_arn`; the HUD source tag is purple *VSDC247 (Visual)*.

### Where it was seen, and the phone alert

* **`page_url`** - every VSDC247 payload carries the address-bar URL of the page the submission was
  seen on, at the top level and inside `raw_payload.vsdc247`. The query string is stripped (portal links
  can carry tokens after a `?`, even inside a `#/route?x=` hash) along with any user-info; the host, path
  and hash route stay. It is empty when the address bar could not be read - never the window title.
  Payloads from the crosshair pipeline keep their routed pages in `raw_payload.timeline`.
* **Phone alert** - when a submission is captured with **no client**, one push message goes to the
  designer's phone through [ntfy](https://ntfy.sh). Off until configured: put `{"topic": "sera-alerts-<long random string>"}`
  in `vsdc_alert.json` next to the program (git-ignored - the topic is a secret and this repository is public),
  or set `VSDC_ALERT_TOPIC` / `VSDC_ALERT_SERVER`. Install the ntfy app, subscribe to that topic, then run
  `python tools/vsdc_alert_test.py`. **The same JSON is copied to every PC.** Which PC an alert is about comes
  from that PC's own `device_identity.txt` in its `AmanAssociates_Sera` data folder (the file the app writes on
  first launch from the hostname, and staff may rename), read at the moment the alert is sent; a `machine`
  value in the JSON is ignored.
  The message carries **no client data**: PC name, form, time, and the page path with anything shaped like
  a PAN, GSTIN, ARN or long number removed. It fires once per capture (not again when the client is attached
  later), is sent from a background thread with one retry, and is limited to 20 per hour. A topic under 16
  characters, or a non-https server, disables alerts rather than sending somewhere unintended.
  If the machine is offline when the alert is due, that alert is lost - the HUD prompt and the tracker's
  `Pending_<ARN>` row are still there.

## Modes

`VSDC247_MODE`:

| Value | Behaviour |
| :--- | :--- |
| `live` (default) | Saves CERTAIN captures, prompts on PROBABLE ones. |
| `shadow` | Observes and logs to the console only — nothing is saved or shown. Recommended for the first weeks: a VSDC247 hit with no crosshair capture is a **missing crosshair**, and the console line names the page. |
| `off` | Never looks. |

While `VSDC_UIA_ONLY=1` is set (VSDC-X isolation testing), VSDC247 defaults to **off**, so it cannot capture a
submission before VSDC-X does and hide a VSDC-X failure. Set `VSDC247_MODE=live` to run it anyway.

## Verifying it

* `python -m pytest tests/test_vsdc247.py tests/test_vsdc247_integration.py tests/test_vsdc_scope.py`
* `python tools/vsdc247_e2e_test.py` — the real pipeline (Edge, real screenshot, real Windows OCR, real
  green-box detection) on pages that look like confirmations, and on pages that look similar but are not.

## Known unknowns — check on the live portals

* **Green-box colours** are tuned for a pale-green and a saturated-green banner. The live portals' exact
  shades are unconfirmed; the green box is only corroboration, so a miss lowers a score by 15 rather than
  causing a missed capture.
* **Success wording** is matched by pattern. If a portal words its confirmation differently, VSDC247 will
  raise a *possible submission* prompt (or stay silent) rather than save — the safe direction.
* **GST ARN structure:** the ITR date-in-ack check has no GST equivalent yet. A real GST ARN would allow a
  similar embedded-state / month check.
