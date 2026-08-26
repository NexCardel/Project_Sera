# Sera Clipboard Assist (SCA) Redesign Blueprint

Status: Proposed  
Target: SCA 3.0  
Scope: Desktop app, Chrome extension, Firefox extension, native messaging host

## 1. Objective

Make SCA deterministic and observable across clipboard capture, native messaging,
extension restarts, multiple tabs, and single- or two-step portal logins.

The redesign must preserve the existing security boundary: passwords remain in
the desktop vault and are delivered only after a validated client UID and portal
match.

## 2. Design principles

1. One active arm state per extension profile.
2. Every command has an ID, acknowledgement, expiry, and final result.
3. UID matching is exact after shared normalization; fuzzy matching is not used.
4. Portal-specific behavior lives in adapters, not scattered selectors.
5. All retries are idempotent.
6. A failed operation explains why it failed.
7. Chrome and Firefox consume the same protocol and test cases.

## 3. System architecture

```text
┌────────────────────┐
│ Desktop SCA service │
│ clipboard + vault  │
└─────────┬──────────┘
          │ SCA command protocol
┌─────────▼──────────┐
│ Native messaging   │
│ host / reconnect    │
└─────────┬──────────┘
          │
┌─────────▼─────────────────┐
│ Extension SCA coordinator │
│ durable state + acks       │
└─────────┬─────────────────┘
          │
┌─────────▼──────────┐
│ Portal adapter      │
│ fields + flow rules │
└─────────┬──────────┘
          │
┌─────────▼──────────┐
│ Login page/tab      │
│ detection + action  │
└─────────────────────┘
```

The desktop watcher is responsible for identifying a client and requesting an
arm. The extension is responsible for maintaining arm state, selecting the
matching tab/service, detecting login fields, and reporting the result.

## 4. Canonical arm state

The extension stores this object in `storage.local` and mirrors it in memory:

```json
{
  "schema": 1,
  "arm_id": "arm_<random>",
  "client_id": 123,
  "client_token": "<opaque-token>",
  "matched_uid": "GST_USER_01",
  "candidate_uids": ["GST_USER_01", "AIUPA2571J"],
  "services": [
    {
      "service_id": 9,
      "service_key": "gst",
      "host_patterns": ["*.gst.gov.in", "*.gst.gov.in"],
      "mode": "autofill"
    }
  ],
  "max_uses": 1,
  "uses_remaining": 1,
  "created_at": 0,
  "expires_at": 0,
  "state": "ARMED",
  "last_operation_id": null,
  "last_error": null
}
```

Only `ARMED` state can trigger a fill. Terminal states are `CONSUMED`,
`EXPIRED`, `REJECTED`, and `FAILED`.

## 5. State machine

```text
IDLE
  │ ARM_REQUEST
  ▼
ARMING ── reject/timeout ──► REJECTED
  │ ACK_ARMED
  ▼
ARMED ── expiry ────────────► EXPIRED
  │ matching UID + portal
  ▼
MATCHED ── field missing ──► WAITING_FOR_FIELDS
  │ fields ready
  ▼
FILLING ── success ─────────► CONSUMED or ARMED
  │ failure
  ▼
FAILED
```

`ARMED` may return to `ARMED` after a successful fill when `uses_remaining` is
greater than zero. A new arm always replaces an older arm atomically.

## 6. Desktop-to-extension protocol

Every message includes `protocol_version`, `command_id`, `sent_at`, and a
message-specific payload.

### Desktop → extension

```json
{
  "type": "SCA_ARM_REQUEST",
  "protocol_version": 1,
  "command_id": "cmd_<random>",
  "arm": { "...": "canonical arm state" }
}
```

Other commands:

- `SCA_DISARM_REQUEST`
- `SCA_STATE_REQUEST`
- `SCA_PING`

### Extension → desktop

- `SCA_ACK`: command received and accepted/rejected
- `SCA_STATE`: current canonical state
- `SCA_MATCHED`: UID and adapter matched on a tab
- `SCA_FILL_STARTED`
- `SCA_FILL_RESULT`
- `SCA_ERROR`

Example result:

```json
{
  "type": "SCA_FILL_RESULT",
  "protocol_version": 1,
  "command_id": "cmd_<random>",
  "operation_id": "op_<random>",
  "arm_id": "arm_<random>",
  "result": "success",
  "service_key": "gst",
  "tab_id": 42,
  "detail": "Password filled after GST two-step login"
}
```

The desktop retries an unacknowledged command twice with the same
`command_id`. The extension treats duplicate command IDs as safe no-ops and
resends the prior acknowledgement.

## 7. Shared UID normalization

Both desktop and extension must use the same algorithm:

1. Convert to string.
2. Trim leading/trailing whitespace.
3. Replace repeated whitespace with one space.
4. Normalize Unicode compatibility characters.
5. Convert to uppercase.
6. Reject empty values, control characters, and values over the configured limit.
7. Compare exact normalized values only.

Mapped service user-ID columns are always indexed as UIDs, regardless of their
display label (`User ID`, `GST Username`, `Login`, `User`, etc.).

## 8. Portal adapter contract

Each adapter implements:

```text
matches_url(url) -> bool
find_uid_fields(document) -> fields
find_password_fields(document) -> fields
is_two_step(document) -> bool
is_login_ready(document) -> bool
fill_password(field, password) -> result
find_continue_button(document) -> button | null
success_signal(document) -> bool
```

Initial adapters:

- `gst`
- `income_tax`
- `traces`
- `mca`
- generic configured service adapter

Adapters use ordered selectors, visibility checks, input/change dispatch, and a
bounded retry schedule. They must never fill a password on an unmatched host.

## 9. Field detection strategy

The content script listens to:

- `input`
- `change`
- `paste`
- DOM mutations
- SPA route changes
- a low-frequency fallback scan while an arm is active

When a UID is detected, it sends one `SCA_MATCH_CANDIDATE` event. The
coordinator deduplicates it by `arm_id + tab_id + normalized_uid`.

Two-step pages remain in `WAITING_FOR_FIELDS` after UID confirmation and are
rechecked after the continue action, route change, or password-field mutation.

## 10. Reliability and failure policy

- Native host disconnect: reconnect with exponential backoff, preserving the
  active arm locally until expiry.
- Extension restart: restore `storage.local` state and request a desktop sync.
- Desktop restart: rebuild the UID index and send state only after native host
  acknowledgement.
- Multiple tabs: select the tab whose host matches an attached service; never
  autofill an unrelated tab.
- Multiple clients: newest arm replaces the old arm and records the replacement.
- Missing password field: show widget mode or return `WAITING_FOR_FIELDS`, not a
  silent failure.
- Duplicate paste/input events: deduplicate by operation ID.
- Expired state: clear memory and storage atomically.

## 11. Diagnostics

Add an SCA diagnostics view showing:

- native host connection state
- extension protocol version
- current arm state and expiry
- last command and acknowledgement
- active UID candidates
- detected service adapter
- matched tab
- last operation result and error
- reconnect and retry counters

Each event should include a correlation chain:

`arm_id → command_id → operation_id → result`

Passwords and decrypted credentials must never be written to diagnostics.

## 12. Migration plan

### Phase 1 — Protocol foundation

- Add protocol envelope and command IDs.
- Add canonical arm state.
- Add acknowledgement/retry handling.
- Keep the current autofill code behind the coordinator.

### Phase 2 — Detection and adapters

- Move GST and Income Tax behavior into adapters.
- Add deterministic UID normalization.
- Replace scattered selectors with adapter contracts.
- Add two-step field lifecycle handling.

### Phase 3 — Reliability and observability

- Add restart recovery.
- Add diagnostics view.
- Add operation deduplication and structured error reporting.
- Remove legacy parallel SCA state paths after a compatibility period.

## 13. Acceptance criteria

SCA 3.0 is ready when:

- 99% of valid arm requests receive an acknowledgement within 1 second in local
  testing.
- No duplicate fill occurs for one operation ID.
- Extension restart preserves a non-expired arm.
- Native host restart recovers without requiring app restart.
- GST single-step and two-step login flows both work.
- Income Tax, TRACES, and MCA adapters pass their login fixtures.
- An unmatched UID or host never triggers autofill.
- Every failed attempt produces a visible diagnostic reason.
- Chrome and Firefox pass the same protocol and adapter test matrix.

## 14. First implementation slice

The first coding slice should be deliberately small:

1. Create `sca_protocol.py` with message/state constants and validators.
2. Add `arm_id`, `command_id`, and acknowledgement handling to the native host.
3. Replace direct SCA arm state writes in `background.js` with the coordinator.
4. Add a structured `SCA_STATE` response.
5. Add tests for arm, duplicate arm, expiry, reconnect, and state recovery.

This slice improves consistency without changing portal selectors yet, making it
safe to validate before migrating individual portal adapters.
