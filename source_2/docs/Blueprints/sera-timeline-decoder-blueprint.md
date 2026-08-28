# Sera Raw Payload Dump — Parser, Action Decoder & Timeline Blueprint

## 0. What the dump actually looks like

Each entry is **not** pure JSON — it's a fixed-width header block followed by a JSON body, delimited by `====` rules:

```
========================================================================================
CAPTURE DUMP ENTRY #<n>
========================================================================================
Timestamp       : <ISO8601>
Portal          : <string>
Capture Method  : <SAD_API_Interceptor | DOM_Tracker | Manual_Fallback>
Status          : <string>
ARN / Ack No    : <string>
Period Label    : <string, may be blank>
Client ID       : <int or "null (//<PAN>)" >
Captured By     : <machine/user token>
----------------------------------------------------------------------------------------
RAW JSON PAYLOAD:
{ ... }
========================================================================================
```

Key observations from surveying the file (157 entries):
- All lines end in `\r\n` — normalize line endings before regex matching.
- `Client ID` is sometimes a bare int, sometimes `null (//<entityNum>)` — the PAN is the fallback identity key when `client_id` is null in the JSON.
- The JSON body's own `client_id` field can *disagree* with the header's `Client ID` (saw header `494` vs body `214` in entry #11) — both need to be captured, not silently merged.
- Multi-step flows share a `session_id` when present (only appears on later/richer entries — not all).
- Repeated identical-looking entries (e.g. `PROFILE-MOKPA1000A` appearing at 14:48:01 and 14:50:47) are **distinct lifecycle steps** (getEntity fetch → saveEntity write), not duplicates — don't dedupe on ARN alone.
- Bank account numbers and mobile numbers in `raw_payload` are base64-encoded (not encrypted) — this is PII sitting one `atob()` call away from plaintext. Treat the whole dump as sensitive regardless of the "mock" label, since the shapes (PAN format, IFSC codes, real bank names) are production-realistic.

---

## 1. Pipeline overview

```
[raw .txt] → Stage A: Entry Splitter → Stage B: Header Parser → Stage C: JSON Parser
           → Stage D: Identity Resolver → Stage E: Action Decoder → Stage F: Session Stitcher
           → Stage G: Timeline Assembler → Stage H: Output (per-client JSON timeline)
```

Each stage takes the previous stage's output list and enriches it — never mutates in place — so you can unit-test stages independently against fixtures pulled straight from this dump.

---

## 2. Stage A — Entry Splitter

Split on the `====...====` rule lines (64+ `=` chars), discard empty chunks, keep chunks that contain `CAPTURE DUMP ENTRY #`.

```python
import re

ENTRY_SPLIT_RE = re.compile(r'={10,}\s*\r?\n')

def split_entries(raw_text: str) -> list[str]:
    text = raw_text.replace('\r\n', '\n')
    chunks = ENTRY_SPLIT_RE.split(text)
    return [c.strip() for c in chunks if 'CAPTURE DUMP ENTRY #' in c]
```

## 3. Stage B — Header Parser

Fixed-width `Key : Value` lines above the `RAW JSON PAYLOAD:` marker. Use a tolerant `key: value` split rather than fixed column offsets (values are padded with spaces but lengths vary):

```python
HEADER_FIELD_RE = re.compile(r'^([A-Za-z /]+?)\s*:\s*(.*)$')

def parse_header(chunk: str) -> dict:
    header_part, _, _ = chunk.partition('RAW JSON PAYLOAD:')
    fields = {}
    for line in header_part.splitlines():
        m = HEADER_FIELD_RE.match(line.strip())
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()
    return fields
```

Post-process `Client ID` specially — it has two shapes:

```python
def parse_client_id_field(raw: str) -> tuple[int | None, str | None]:
    # "494" -> (494, None)
    # "null (//MOKPA1000A)" -> (None, "MOKPA1000A")
    m = re.match(r'(?P<id>\d+|null)\s*(?:\(//(?P<pan>[A-Z0-9]+)\))?', raw)
    if not m:
        return None, None
    cid = None if m.group('id') == 'null' else int(m.group('id'))
    return cid, m.group('pan')
```

## 4. Stage C — JSON Parser

Everything after `RAW JSON PAYLOAD:` up to the trailing `====` rule is one JSON object.

```python
import json

def parse_json_body(chunk: str) -> dict | None:
    _, _, json_part = chunk.partition('RAW JSON PAYLOAD:')
    json_part = json_part.strip()
    try:
        return json.loads(json_part)
    except json.JSONDecodeError:
        # fallback: trim to outermost braces in case of trailing junk
        start, end = json_part.find('{'), json_part.rfind('}')
        if start != -1 and end != -1:
            try:
                return json.loads(json_part[start:end+1])
            except json.JSONDecodeError:
                return None
        return None
```

Malformed entries should be routed to a **quarantine list** with the raw chunk preserved, not dropped silently — you want to know if the interceptor ever emitted truncated JSON.

## 5. Stage D — Identity Resolution

This is the piece that matters most for correctness, since `client_id` is unreliable (null, or disagreeing between header/body). Priority order, mirroring the dual-PK matching your codebase already does elsewhere (`test_dual_pk_and_sad_resolution.py`):

1. **Body `client_id`** if non-null → trust it (it's what the desktop app resolved at capture time).
2. Else **header `Client ID` int** if present.
3. Else **PAN/entityNum match** — extract from `pan`, `raw_payload.entityNum`, or `raw_payload.panNumber`/`loggedInUserId`, and resolve against the client roster.
4. Else → bucket as `unresolved_identity`, keyed by whatever PAN string is available, for manual review (same concept as the existing `unassigned_identity` toast behavior in `main.py`).

Emit a `identity_confidence` tag (`exact_id`, `header_id`, `pan_match`, `unresolved`) on every event — this makes it easy to audit false-matches later without re-running the whole pipeline.

## 6. Stage E — Action Decoder

The decoder is a **URL-pattern → semantic action** lookup table, refined by payload-shape heuristics for outcome (success/failure/pending). Build it as data, not nested if/else, so it's easy to extend as new portals get intercepted.

```python
ACTION_RULES = [
    # (url_pattern, portal, action_label, outcome_fn)
    (r'/loginapi/login$',                    'IT', 'Login',                     'outcome_generic'),
    (r'/verificationservices/auth/validateOTP$', 'IT', 'OTP Validation',        'outcome_generic'),
    (r'/servicesapi/auth/getEntity$',         'IT', 'Bank Account Lookup',      'outcome_bank_validation'),
    (r'/verificationservices/auth/getEntity$','IT', 'Profile Fetch',            'outcome_generic'),
    (r'/verificationservices/auth/saveEntity$','IT','Profile Save',             'outcome_code_desc'),
    (r'/returns/view/wzrd$',                  'IT', 'ITR Wizard — View Schedules', 'outcome_generic'),
    (r'/returns/save/wzrd$',                  'IT', 'ITR Wizard — Save Draft',  'outcome_generic'),
    (r'/returns/insertSla/wzrd$',             'IT', 'ITR Wizard — Save SLA',    'outcome_generic'),
    (r'/returns/validate/wzrd$',              'IT', 'ITR Wizard — Validate',    'outcome_success_flag'),
    (r'/returns/submit/wzrd$',                'IT', 'ITR Submission',           'outcome_success_flag'),
    (r'/returns/downloadfile$',               'IT', 'ITR Download',             'outcome_generic'),
    (r'/return/details$',                     'IT', 'ITR Details Fetch',        'outcome_generic'),
    (r'/masterservicesapi/auth/getEntity$',   'IT', 'Master Entity Fetch',      'outcome_generic'),
    (r'/gstr1/summary',                       'GST','GSTR-1 Summary Fetch',     'outcome_generic'),
    (r'/gstr1/totalsummarycount',             'GST','GSTR-1 Total Count Fetch', 'outcome_generic'),
    (r'/formdetails',                         'GST','GSTR-1 Form Details',      'outcome_generic'),
    (r'/signatory$',                          'GST','Signatory Fetch',          'outcome_generic'),
    (r'/filingsnapshot$',                     'GST','Filing Snapshot',          'outcome_generic'),
    (r'/getRcmAvl',                           'GST','RCM Available Balance',    'outcome_generic'),
    (r'/dashboard/fileIncomeTaxReturn$',      'IT', 'ITR Filing Landing',       'outcome_generic'),
]

def decode_action(url: str) -> tuple[str, str, str]:
    for pattern, portal, label, outcome_fn in ACTION_RULES:
        if re.search(pattern, url):
            return portal, label, outcome_fn
    return 'UNKNOWN', f'Unrecognized endpoint: {url}', 'outcome_generic'
```

**Outcome functions** — payload shape varies wildly by endpoint, so outcome inference needs several strategies rather than one:

| Strategy | Trigger fields | Logic |
|---|---|---|
| `outcome_success_flag` | `successFlag`, `httpStatus` | `successFlag == true` → success; else check `errors[]` non-empty → failure; else pending |
| `outcome_code_desc` | `code`, `desc` | `code` containing `SUCCESS` → success; `FAIL`/`ERROR` → failure |
| `outcome_bank_validation` | `accountStatus`, `status`, `errorCd` | `status == "A"` and no `errorCd` → success; `status == "E"` → failure, surface `errorCd`/`userAction` as the reason |
| `outcome_generic` | `errors[]`, `messages[]` | non-empty `errors` → failure; otherwise success |

Each outcome function returns `(outcome: "success"|"failure"|"pending", reason: str | None)`.

## 7. Stage F — Session Stitching

Group decoded events by `session_id` where present; fall back to `(resolved_client_id, portal, 90-second rolling window)` when `session_id` is absent (this is how you'll link the earlier entries in the dump that predate the field being added). Within a session, order by timestamp and collapse known **multi-step wizard flows** into one logical node with sub-steps, e.g.:

```
ITR Filing Session (client 214, PAN MOKPA1000A)
  ├─ 14:47:41  Validate         → pending (successFlag=false, no errors yet)
  ├─ 14:50:48  ... subsequent submit step ...
```

This is what turns 157 raw captures into a readable narrative instead of a flat log.

## 8. Stage G — Timeline Assembler

Final per-client structure:

```json
{
  "client_id": 214,
  "pan": "MOKPA1000A",
  "identity_confidence": "exact_id",
  "events": [
    {
      "timestamp": "2026-08-23T14:43:40.343841+00:00",
      "portal": "IT",
      "action": "Bank Account Lookup",
      "outcome": "success",
      "reason": null,
      "arn": "662914450160925",
      "session_id": null,
      "capture_method": "SAD_API_Interceptor",
      "source_entry": 11
    }
  ],
  "sessions": [ ... stitched wizard flows ... ],
  "flags": ["repeat_profile_save_within_3min"]
}
```

Sort `events` chronologically; sort `sessions` by session start time. Keep `source_entry` (the `#N` from the dump) on every event for traceability back to the raw text.

## 9. Data-quality flags worth emitting automatically

- **Identity mismatch**: header `Client ID` ≠ body `client_id` → flag for review, don't silently pick one.
- **Repeat action within N seconds**: e.g. same ARN/action twice inside a short window (like the two `PROFILE-MOKPA1000A` saves) — likely a retry or a UI double-submit, worth surfacing rather than treating as two independent events.
- **Failure with no visible retry**: a `failure` outcome with no later `success` for the same (client, action) — these are the ones staff actually need to act on; this is the highest-value output of the whole decoder.
- **Unknown endpoint**: any URL not matched by `ACTION_RULES` — keeps the decoder table honest as new portal endpoints get intercepted.

## 10. Handling the PII in the payloads

Since `raw_payload` carries base64-encoded bank account/mobile numbers and plaintext names/PAN/IFSC, the timeline output should **mask by default** (e.g. show last 4 of decoded bank account, redact full mobile) and only decode to plaintext behind an explicit "reveal" action in the UI, with that reveal itself logged as an audit event — consistent with how sensitive fields are already gated elsewhere in Sera (Admin PIN-protected actions).

---

## 11. Suggested file layout

```
tracker_dump_parser/
├── entry_splitter.py      # Stage A
├── header_parser.py       # Stage B
├── json_parser.py         # Stage C
├── identity_resolver.py   # Stage D
├── action_decoder.py      # Stage E — ACTION_RULES table lives here
├── session_stitcher.py    # Stage F
├── timeline_assembler.py  # Stage G
└── tests/
    └── fixtures/          # pull real chunks straight from the dump per stage
```

Each stage is a pure function (`list[dict] -> list[dict]`), which makes it trivial to snapshot-test against fixtures extracted directly from `seraRawPayloadDump_mock.txt` — you already have 157 real-shaped entries covering login, OTP, bank validation, GST summary, and the full ITR wizard flow to use as your test corpus.
