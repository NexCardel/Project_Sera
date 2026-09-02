# SDC (Sera DOM Crosshair) — Architecture & Context Notes

_Living doc capturing where SDC sits in the Sera pipeline today, why the current per-portal
approach is expensive, and the proposed uniform envelope protocol to fix it. Written for future
Claude sessions / contributors picking this up cold._

---

## 1. What Sera actually is

A practice-management pipeline for a CA/accounting firm: a browser extension that watches
government compliance portals (Income Tax e-filing, GST, MCA, TRACES) while staff work in
them, extracts filing status/ARN/PAN/GSTIN etc. from the page, and syncs it into a desktop app
(`main.py` + PySide6 UI) that tracks the firm's clients and their filing history in an encrypted
local database.

Two source trees exist in this repo (`/` and `source_2/`) — near-duplicates. Treat the root tree
as canonical unless told otherwise; `source_2/` looks like a prior snapshot/backup.

---

## 2. Capture layer status (as of this doc)

| Component | File | Status |
|---|---|---|
| **SDC** (DOM Crosshair) | `sera_extension/sdc/sdc_core.js` + `sdc/protocols/*.js` | **Active — the only live tracker.** Injected via `injectSAD()` in `background.js` on every tab load/SPA nav. |
| SAD (network interceptor) | `sera_extension/content_scripts/net_interceptor.js` | **Dead by design.** Unconditional `return;` on line 11 before any `fetch`/`XHR` hooking runs. Comment: "Permanently Retired: Deactivated for compliance & safety." Zero network interception happens. |
| Old DOM tracker | `sera_extension/tracker.js` | **Dead in practice.** Never injected by `background.js` (grepped end-to-end, zero references outside `manifest.json`'s `web_accessible_resources`). Superseded by SDC's route-gated crosshair pattern. |
| `tracker_dump_parser/` (8-stage pipeline) | `tracker_dump_parser/*.py` | **Dead code.** Only reachable from its own unit test. `tracer.py`'s docstring explicitly says it's independent of this package — built as a 5th pipeline, abandoned before UI wiring. |

**Net effect:** SDC is the sole active capture mechanism. It is purely passive DOM/text reading
on pages the extension is already injected into — no network-response snooping (that path is
dead code) and no broad-page `MutationObserver` scanning (that's the older, retired tracker.js
approach). Of the two live protocols, only ITR is at real coverage depth today — GST is live but
still early-stage, so treat "SDC is working" as portal-specific, not a blanket statement.

### Protocol coverage

| Protocol | File | Status |
|---|---|---|
| `itr_protocol.js` | Income Tax e-filing | Full — real crosshairs, 1143 lines |
| `gst_protocol.js` | GST portal | **Early stage** — real crosshairs exist (614 lines, not a stub), but coverage is partial; significant work still left to bring it to parity with ITR |
| `mca_protocol.js` | MCA portal | Stub — 24 lines, "no crosshairs registered yet" |
| `traces_protocol.js` | TRACES portal | Stub — 25 lines, same |

So today: ITR tracking is the mature/complete implementation. GST runs on SDC but is still
early — more crosshairs/flows need to be built out. MCA and TRACES tracking do not exist yet
despite the stub files being present.

---

## 3. The end-to-end pipeline (browser → screen)

```
1. Portal page loads (ITR / GST tab)
     └─ background.js: injectSAD(tabId, reason) injects sdc_core.js + protocol files
2. sdc_core.js watches URL/hash only (route-gated — "zero-idle crosshair pattern")
     └─ matches a registered crosshair (e.g. gst_filing_success) → wakes that protocol
3. Protocol (e.g. gst_protocol.js) reads the rendered DOM/text — no network hooking
     └─ builds a capture object (GSTIN, legal name, period, status, etc.)
4. sdc_core.js dispatches window CustomEvent 'SeraSDCCapture'
     └─ re-dispatched as 'SeraFSTApiCapture' so the pre-existing filing_detector.js
        pipeline needs no changes
5. background.js POSTs the JSON to http://127.0.0.1:49152 (local desktop app socket)
     (native_host/host.py is an alternate native-messaging bridge — unused for this path)
6. ui/extension_listener.py — ExtensionListener QThread binds that port, parses the
   HTTP body, emits a Qt signal:
     filing_result / audit_event  -> filing_result_received
     sdc_session_timeline         -> sdc_timeline_received
7. main.py wires those signals:
     filing_result_received  -> _handle_extension_result -> _capture_queue
                              -> _process_extension_result() -> database.py (tracker_dump)
     sdc_timeline_received   -> _handle_sdc_timeline -> db.upsert_sdc_session_timeline()
                              -> database.py (sdc_session_timelines)
8. database.py (SQLCipher-encrypted rawPayload.db):
     tracker_dump            — raw captures (raw_payload_json, arn_number, portal, status...)
     sdc_session_timelines   — pre-stitched step sequences (timeline_json)
9. ui/windows/tracker_dump_window.py — when a client's Timeline tab is opened:
     get_captures_for_container() + get_sdc_session_timelines() -> merged captures list
10. ui/utils/timeline_decoder.py — pure consumer/formatter, never touches socket/DB/extension:
     group_captures_into_sessions() -> decode_session_timeline()/decode_single_capture()
     -> collapse_consecutive_repeat_steps() -> format_timeline_flow_html/plain()
11. Display: self.txt_timeline.setHtml(...) in the capture-detail dialog
```

### Actual `tracker_dump` schema (from `database.py`)

```sql
CREATE TABLE tracker_dump (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id           INTEGER,
    unassigned_identity TEXT,
    service_id          INTEGER,
    portal              TEXT,
    period_label        TEXT,
    arn_number          TEXT,
    capture_method      TEXT DEFAULT 'DOM_Tracker',
    status              TEXT DEFAULT 'submitted',
    raw_payload_json    TEXT,
    captured_by         TEXT,
    created_at          TEXT NOT NULL
);
```

`raw_payload_json` is a dumping ground for whatever each protocol happened to scrape — this is
the root of the "hardcoded per-portal interpretation" problem described below.

There's also `client_raw_containers` — a per-identity rollup keyed on `identity_key`
(pan/gstin/etc.) that aggregates `portal_profiles`, `filing_history`, and `raw_aggregates` across
every capture for a client.

---

## 4. Why the current approach doesn't scale

Two hardcoded layers exist per portal today, not one:

1. **Extraction** — each protocol file scrapes its portal's DOM with bespoke
   selectors/regex and builds its own payload shape however it wants.
2. **Interpretation** — `main.py::_process_extension_result()` has to reverse-engineer
   whatever arrived (`scraped_data`, `form_fields`, name-splitting by guessing key
   names), and `timeline_decoder.py` separately maintains one translator function per
   field convention per portal (`_translate_form_code`, `_translate_section_code`,
   `_translate_login_mode`, `_translate_efile_status`).

Adding a new portal (MCA, TRACES) therefore isn't "write a scraper" — it's "write a scraper
**and** retrofit three separate downstream consumers to understand its JSON dialect." Effort
scales roughly with (portals × consumers), not portals alone.

---

## 5. Proposed fix: one canonical envelope, portal weirdness stays in JS

**Core rule:** every capture splits into a fixed **envelope** (the only thing Python/DB/timeline
code may reason about) and a **raw fields bag** (portal-specific, stored for
audit/backfill, never load-bearing for logic).

```json
{
  "schema_version": "1.0",
  "capture_id": "uuid-v4",
  "captured_at": "ISO-8601 timestamp",
  "source": {
    "protocol": "gst | itr | mca | traces",
    "crosshair_id": "gst_filing_success",
    "extension_version": "2.9.0"
  },
  "session_id": "string",
  "event": {
    "type": "SESSION_START | LOGIN_SUCCESS | FORM_VIEW | FILING_SUBMITTED | FILING_PENDING_VERIFICATION | FILING_VERIFIED | PAYMENT_SUCCESS | LOGOUT | ERROR",
    "status": "success | pending | failed"
  },
  "identity": {
    "pan": "string | null",
    "gstin": "string | null",
    "tan": "string | null",
    "cin": "string | null",
    "legal_name": "string | null",
    "confidence": "high | medium | low"
  },
  "fields": { "/* protocol-specific raw scrape, namespaced e.g. gst.trade_name */": null },
  "evidence": { "url": "string", "page_title": "string" }
}
```

**Why each field exists:**
- `event.type` — closed, portal-agnostic vocabulary. Collapses N bespoke translator
  functions in `timeline_decoder.py` into a single `EVENT_TYPE_LABELS` lookup.
- `identity` — always the same four fields regardless of portal; name-parsing/PAN
  extraction happens once, in JS, where the DOM context is actually known — not guessed
  at downstream from arbitrary key names.
- `capture_id` — free deduplication as a property of the envelope itself (the old,
  now-dead `net_interceptor.js` solved this ad hoc, only for one file).
- `fields` — escape hatch; anything not yet promoted to the canonical shape still gets
  captured and stored, just inert until someone decides to formalize it.

### Enforcement: one `emit` function, not a convention

The rule only sticks if it's structurally enforced. Every protocol should call a single
`SDC.emit(protocolName, crosshairId, eventType, identity, fields)` helper in `sdc_core.js`
that builds the envelope — no protocol should hand-construct its own payload or dispatch its
own event. That makes a malformed/divergent payload structurally impossible, not just
against style guidelines.

```js
SDC.emit = function (protocolName, crosshairId, eventType, identity, fields) {
  const envelope = {
    schema_version: '1.0',
    capture_id: crypto.randomUUID(),
    captured_at: new Date().toISOString(),
    source: { protocol: protocolName, crosshair_id: crosshairId, extension_version: SDC_VERSION },
    session_id: SDC.currentSessionId(),
    event: { type: eventType, status: deriveStatus(eventType) },
    identity: normalizeIdentity(identity),
    fields,
    evidence: { url: location.href, page_title: document.title }
  };
  window.dispatchEvent(new CustomEvent('SeraSDCCapture', { detail: envelope }));
};
```

### What changes downstream

| Layer | Before | After |
|---|---|---|
| `extension_listener.py` | Already generic | No change |
| `main.py::_process_extension_result` | Per-portal heuristics on `scraped_data`/`form_fields` | Switches on `event.type`, reads `identity` directly — one function, works for every portal |
| `database.py` | Schema shaped around whatever fields happened to arrive | `tracker_dump` gets fixed canonical columns (event_type, pan, gstin, capture_id) + one JSON column for `fields` |
| `timeline_decoder.py` | 4+ bespoke `_translate_*` functions | One `EVENT_TYPE_LABELS` dict lookup |
| New portal (MCA/TRACES) | Write scraper + retrofit 3 downstream consumers | Write scraper that calls `SDC.emit()` — zero downstream changes |

That last row is the actual payoff: `mca_protocol.js`/`traces_protocol.js` are stubs today
specifically because filling them in currently means touching Python interpretation code and
timeline translators too. Under this scheme, filling a stub becomes: define crosshairs → scrape
fields → map to an existing `event.type` → call `SDC.emit()`. Nothing else needs to know MCA
exists.

### Open decision

Whether `event.type` additions require a version bump (safer, stricter) or can be appended
freely (faster, riskier if two protocols silently disagree on meaning). Leaning toward a small
shared registry (`event_types.json`) that both JS and Python import from, so there's no drift
possible between the two sides.

---

## 6. Detailed design of the unified `event.type` language

This section grounds the vocabulary in what ITR and GST are *actually* producing today (not
invented from scratch) and specifies the mechanics of enforcement.

### 6.1 The `event.type` vocabulary — mapped from real statuses

Every distinct status string currently produced by the two live protocols collapses into one
shared, closed vocabulary:

| Canonical `event.type` | What it means | ITR crosshair → status today | GST crosshair → status today |
|---|---|---|---|
| `LOGIN_SUCCESS` | User authenticated | `itr_login` → "Pre-Login / Password" (fires on success) | `gst_login_logout` → "GST Pre-Login" |
| `PORTAL_VIEW` | Landing/dashboard, no action yet | `itr_landing` → "Landing Page Active" | `gst_welcome_calendar` → "GST Returns Calendar" |
| `FORM_VIEW` | User is looking at/filling a specific form | `itr_form_select` → "Form Selected", `itr_personal_info` → "Draft / Personal Info" | `gst_returns_dashboard` → "Return Selection", `gst_form_details` (pre-submit state) |
| `FILING_SUBMITTED` | Filed, not yet verified | `itr_submitted_pending` → "Submitted (Pending e-Verification)" | `gst_filing_success` → "Initiated" |
| `FILING_VERIFIED` | Filed and confirmed/verified | `itr_filed_verified` → "Filed & Verified (Portal Confirmed)" | `gst_form_details` (post-submit) → "Filed & Confirmed" |
| `RETURNS_LIST_VIEW` | Browsing history of past filings | `itr_view_filed_returns` | — (GST has no equivalent crosshair yet) |
| `LOGOUT` | Session ended | (not currently captured) | `gst_login_logout` (logout case) |
| `ERROR` | Reserved — capture attempted, page state unrecognized/failed | — | — |

8 values, closed set. Every portal-specific phrase ("Filed & Verified (Portal Confirmed)" vs
"Initiated") disappears once it leaves the JS layer — nobody downstream ever sees raw portal
wording again.

### 6.2 `status` — derived, not chosen by the protocol

Rather than let each protocol invent its own status string, `status` is mechanically derived
from `event.type` inside the one `emit()` function, so GST and ITR can never drift on what
"pending" vs "success" means:

```js
const STATUS_BY_EVENT = {
  LOGIN_SUCCESS:      'success',
  PORTAL_VIEW:         'success',
  FORM_VIEW:            'pending',
  FILING_SUBMITTED:  'pending',
  FILING_VERIFIED:    'success',
  RETURNS_LIST_VIEW: 'success',
  LOGOUT:                 'success',
  ERROR:                    'failed',
};
function deriveStatus(eventType) {
  return STATUS_BY_EVENT[eventType] || 'pending';
}
```

### 6.3 `identity` — same 4 slots, validated at the source

```js
function normalizeIdentity(raw) {
  const pan   = /^[A-Z]{5}[0-9]{4}[A-Z]$/.test(raw.pan || '')   ? raw.pan   : null;
  const gstin = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$/.test(raw.gstin || '') ? raw.gstin : null;
  const tan   = /^[A-Z]{4}[0-9]{5}[A-Z]$/.test(raw.tan || '')   ? raw.tan   : null;
  const cin   = raw.cin || null; // CIN format not yet validated — MCA still stub

  let confidence = 'low';
  if (pan || gstin) confidence = 'high';
  else if (raw.legal_name) confidence = 'medium';

  return { pan, gstin, tan, cin, legal_name: raw.legal_name || null, confidence };
}
```

Regex validation happens once, in JS, at the source — not as a downstream guess in `main.py`.
A scraped string that doesn't match the real PAN/GSTIN shape becomes `null`, not garbage
passed downstream as if it were trustworthy.

### 6.4 `fields` — namespaced, never load-bearing

Anything a protocol scrapes that doesn't fit the canonical shape still gets through, prefixed so
it can never collide across portals:

```json
"fields": {
  "gst.trade_name": "...",
  "gst.return_period": "042026",
  "gst.arn": "AA0704..."
}
```

Rule: no code outside the JS layer is allowed to branch on anything inside `fields`. It's for
display/audit/future-promotion only. The moment something in `fields` becomes something logic
actually depends on, it graduates into the canonical envelope — and that's a schema version
bump.

### 6.5 The registry — single source of truth for both languages

Instead of hardcoding the 8-value enum separately in JS and Python (where the two copies can
drift), both sides import the same file:

```json
// event_types.json
{
  "schema_version": "1.0",
  "event_types": {
    "LOGIN_SUCCESS":      { "status": "success", "label": "Logged In" },
    "PORTAL_VIEW":         { "status": "success", "label": "Viewing Portal" },
    "FORM_VIEW":            { "status": "pending", "label": "Viewing Form" },
    "FILING_SUBMITTED":  { "status": "pending", "label": "Filing Submitted" },
    "FILING_VERIFIED":    { "status": "success", "label": "Filing Verified" },
    "RETURNS_LIST_VIEW": { "status": "success", "label": "Viewing Return History" },
    "LOGOUT":                 { "status": "success", "label": "Logged Out" },
    "ERROR":                    { "status": "failed",  "label": "Error" }
  }
}
```

- `sdc_core.js` loads it to build `STATUS_BY_EVENT` and validate `eventType` before calling
  `emit()`.
- `timeline_decoder.py` loads the same file for its `EVENT_TYPE_LABELS` — the `label` column
  is what currently lives as five separate `_translate_*` functions.
- Adding a 9th event type (e.g. `PAYMENT_SUCCESS`, needed once GST/MCA payment flows are
  built) means editing one JSON file, not JS and Python separately.

### 6.6 Migration path — don't big-bang the working portals

1. Build `SDC.emit()` and `event_types.json`, wire it into **one** crosshair first (e.g.
   `itr_filed_verified` — the simplest, most stable one). Dual-write: old shape + new
   envelope, side by side.
2. Update `main.py` and `timeline_decoder.py` to read the new envelope *if present*, falling
   back to the old parsing path otherwise.
3. Once proven stable, migrate the rest of ITR's crosshairs, then GST's. GST is still
   early-stage anyway (see §2) — better to build its *remaining* work directly on the new
   format rather than migrate it twice.
4. MCA/TRACES get built **only** on the new format from day one. No migration needed there
   at all, since nothing real exists on those portals yet.

---

## 8. SUDR — Sera Unified Dialect Recognition (formal spec)

This is the name for everything in §5–§6: the canonical envelope, the enforced `SDC.emit()`
entry point, and the `event_types.json` registry, taken together as one system.

### 8.1 The three-tier change contract

| Tier | What it is | When it changes |
|---|---|---|
| **Core framework** | `extension_listener.py`, `main.py::_handle_sudr_capture`, `database.py` schema, `timeline_decoder.py` label lookups | **Never**, for any portal, once built |
| **Vocabulary** | `sera_extension/event_types.json` | Only when a genuinely new *kind* of event appears that no existing portal produces — rare |
| **Per-portal protocol** | `sera_extension/sdc/protocols/<portal>_protocol.js` | Every time — this is where actual DOM/selector knowledge about an unfamiliar site has to live, and no config file can substitute for that |

**What this buys you:** adding or improving a portal is "write/edit one self-contained
`protocol.js` that calls `SDC.emit()`" — touch the framework never, touch the vocabulary almost
never. What it does *not* buy: scraping logic itself can't be reduced to JSON, because DOM
structure/selectors are real, portal-specific knowledge that has to be written once as code.

### 8.2 What's actually implemented now

- `sera_extension/event_types.json` — the 8-value canonical vocabulary (§6.1), loaded by
  `sdc_core.js` at runtime via `chrome.runtime.getURL` + `fetch`, with a built-in fallback
  table so a fetch failure can never take crosshairs down.
- `SDC.emit(protocolName, crosshairId, eventType, identity, fields)` — the sole enforcement
  point in `sdc_core.js`. Builds the envelope, derives `status` from `event_types.json`,
  validates `identity` via `normalizeIdentity()` (regex on PAN/GSTIN/TAN shape), and sends it
  three ways: `chrome.runtime.sendMessage`, direct POST to `127.0.0.1:49152`, and a
  `SeraSUDRCapture` window event. Ships **alongside** the existing legacy `_emitCapture`/
  `_emitDual` path — nothing live (ITR, GST) was touched or migrated yet.
- `traces_protocol.js` — real implementation (not the old stub), 6 crosshairs
  (`traces_login_logout`, `traces_dashboard`, `traces_profile`, `traces_statement_upload`,
  `traces_statement_status`, `traces_form16`), built **entirely** on `SDC.emit()` since TRACES
  had zero prior downstream dependents. Status vocabulary sourced from real TRACES workflow
  documentation (§2) — "Processed without Default" → `FILING_VERIFIED`, "Processed with
  Default"/"Rejected" → `ERROR`, etc. Deductor TAN is the `identity.tan` slot; per-row
  deductee PANs (a TRACES-specific two-sided identity model, §4) are kept out of the shared
  `identity.pan` slot and namespaced instead as `fields['traces.deductee_pan']`.
- `manifest.json` — `event_types.json` added to `web_accessible_resources` so the fetch above
  actually resolves.
- `ui/extension_listener.py` — one new signal, `sudr_capture_received`, routed off
  `msg["type"] == "sudr_capture"`. Additive; every existing signal/route is untouched.
- `main.py::_handle_sudr_capture` — the portal-agnostic consumer. Reads only
  `msg["event"]["type"]` and `msg["identity"]`, never branches on `msg["source"]["protocol"]`.
  Calls the existing `database.py::insert_tracker_dump()` (real signature, does its own
  identity resolution against `master.db` internally) — **no schema migration was needed**;
  the full envelope rides in the existing `raw_payload_json` column for now.

### 8.3 Deliberately deferred (not done yet)

- ITR/GST crosshairs still emit only the legacy shape — migrating them onto `SDC.emit()` is
  §6.6 step 1–3, not started.
- `timeline_decoder.py` doesn't yet read `event_types.json` for labels — its existing
  `_translate_*` functions are untouched, since nothing live produces SUDR envelopes for
  portals it renders yet.
- `gstin`/`tan` aren't first-class args to `insert_tracker_dump()` yet — only `pan` is used for
  identity resolution today; GSTIN/TAN-based matching would need
  `_extract_identity_candidates_from_payload()` extended, not just the SUDR layer.
- No UI wiring for TRACES captures in `tracker_dump_window.py` yet — captures land in the DB
  but aren't surfaced in a Timeline tab until that window is taught to query TRACES the same
  way it queries ITR/GST today.

---

## 9. Quick reference — where to look for what

- Add/modify a portal's scraping rules → `sera_extension/sdc/protocols/<portal>_protocol.js`
- Change how a capture reaches the desktop app → `sera_extension/background.js` (`injectSAD`, POST to `:49152`)
- Change how the desktop app ingests it → `ui/extension_listener.py`, `main.py` (`_handle_extension_result`, `_handle_sdc_timeline`)
- Change storage schema → `database.py` (`tracker_dump`, `client_raw_containers`, `sdc_session_timelines`)
- Change how a timeline is rendered → `ui/utils/timeline_decoder.py`
- **Do not** spend time on `tracker_dump_parser/`, `tracker.js`, or `net_interceptor.js` — all
  confirmed dead code / permanently retired, kept in the repo but not on any live path.
