# Project Sera — SMTI & Multi-User Audit Identity Blueprint

Two independent features from this note. Both are additive — neither touches
the existing autofill pipeline, the Manual Copy dialog, or the master
password / encryption model. Nothing currently working is removed or
rewired as part of either.

---

# Part A — Sera Manual Tracker Injection (SMTI)

## A.0 What this is

A **new, independent** browser-extension feature: an on-page floating widget
showing the client's name and two buttons — **User ID** and **Password** —
that fill the corresponding field on the portal page when clicked, one at a
time. This is a *manual-trigger* counterpart to the existing fully-automated
`fillCredentialsInPage()` flow, for portals where the automated fill is
unreliable or where staff want to control exactly when each field gets
filled. It does not replace or modify `background.js`'s existing autofill
function, `tracker.js`'s ARN capture, or any current content script — it is
a new, parallel code path that happens to reuse the same native-messaging
bridge that's already trusted and running.

## A.1 Trigger & flow

Per your sketch: a browser window with the usual portal page, and a small
dropped-down box reading:

```
Client Name — [≡]
[ User ID   ]
[ Password  ]
```

**Trigger**: a third per-service action button in `ClientDetailWindow`,
alongside the existing "Go to X — Autofill" (Alt+1..9) and "X — Manual Copy"
(Alt+Shift+1..9) — a new **"X — Manual Assist" (Alt+Ctrl+1..9)**. This keeps
the same per-service keyboard-shortcut convention already established in the
app rather than inventing a new interaction pattern.

**Flow**:
1. Staff clicks "GST — Manual Assist" (or presses Alt+Ctrl+1).
2. `automation.py` opens/focuses the login URL exactly as it does today for
   autofill (reuses the existing "wake the service worker, retry up to 10s"
   logic — no new connection-handling code needed).
3. Instead of the existing autofill payload, it sends a **new payload type**
   over the TCP 49153 bridge: `{"mode": "manual_assist", "client_name":
   ..., "user_id": ..., "password": ...}`.
4. `background.js`'s native-message listener checks `mode` — `"autofill"`
   (existing, untouched) still calls `fillCredentialsInPage()` as today;
   `"manual_assist"` (new) triggers a new injected content script instead.
5. The new content script renders the floating widget in a **Shadow DOM**
   root (isolates its CSS from the host page, standard practice for
   extension-injected UI, avoids clashing with portal page styles).
6. Clicking "User ID" locates the User ID field using the same
   selector-detection heuristics `fillCredentialsInPage()` already uses, and
   fills it — including the existing Angular-compatible `CompositionEvent`/
   `InputEvent` dispatch, since that's what makes it work on reactive
   portals like the Income Tax site. Clicking "Password" does the same for
   the password field. They're independent actions — pressing one doesn't
   trigger the other.

## A.2 Masking requirement (your note: "make sure to mask the userID and password")

The widget's button labels are **always the generic strings "User ID" and
"Password"** — never the actual value, never a partial reveal. The actual
credential values exist only in the injected script's in-memory closure
(same trust boundary as the existing autofill payload, which already
carries plaintext credentials over this same local bridge — SMTI doesn't
introduce a new exposure surface, it just adds a UI affordance on top of an
already-trusted channel). Nothing sensitive ever touches the visible DOM
text content or an `alt`/`title` attribute a screen-share or screen-recorder
could pick up.

**Suggested addition** (not in your note, flagging for your call): auto-expire
the widget and purge its in-memory credentials after the same timeout used
for the existing **Auto Clipboard Clear** setting (default 30s), so the
"credentials don't linger" philosophy is consistent between the desktop
app's clipboard and the browser widget. Easy to drop if you'd rather it stay
open until the tab closes.

## A.3 New files (additive only)

```
sera_extension/
└── content_scripts/
    └── manual_assist_widget.js   # NEW — Shadow DOM widget, injected only
                                   #        on "manual_assist" mode
```

`background.js` gets one new `if (msg.mode === "manual_assist")` branch in
its existing native-message listener — everything else in that file is
untouched.

`ui/windows/client_detail_window.py` gets one new button per service +
Alt+Ctrl+N shortcut, calling a new `automation.py` method
(`trigger_manual_assist(client, service)`) that mirrors the existing
`trigger_autofill()` method but sends the new payload `mode`.

## A.4 Phases

**Task 1** — `automation.py`: add `trigger_manual_assist()`, new TCP payload
shape. Verify existing `trigger_autofill()` is untouched and still works.

**Task 2** — `background.js`: add the `manual_assist` branch + new content
script injection. Verify existing autofill (`mode: "autofill"`, or no mode
field for backward compatibility) still works unchanged.

**Task 3** — `manual_assist_widget.js`: build the Shadow DOM widget, field
detection reuse, masking rule (§A.2).

**Task 4** — `client_detail_window.py`: add the third button row + Alt+Ctrl
shortcuts.

**Task 5** (optional, per your call) — auto-expiry timer reusing the Auto
Clipboard Clear setting value.

---

# Part B — Multi-User Audit Identity Fix

## B.0 Root cause (confirmed in the actual code)

```python
# main.py, _ensure_user_actor()
actor = self.db.get_setting("user_actor_label")
if not actor:
    name, ok = QInputDialog.getText(...)
    actor = name.strip() if ok and name.strip() else "Staff"
    self.db.set_setting("user_actor_label", actor)   # <-- written to app_settings
return actor
```

`app_settings` is a table **inside `master.db`** — the exact file Syncthing
keeps identical across every employee's machine. So: whoever launches Sera
first gets prompted, types their name, and it's written into the *shared,
synced* database. Every other machine's `if not actor:` check then finds it
already set (because it just synced in) and **never prompts them** — every
staff member after the first silently inherits the first person's name.
This is why the audit log currently can't actually distinguish staff: it's
not that a second username is blocked, it's that only the first person's
identity ever gets recorded, team-wide.

This also explains your framing — "we will not be sharing admin password,"
i.e., the master password (which genuinely must stay identical everywhere,
since it derives the shared DB encryption key) is fine as-is; it's the
**per-person identity**, which was never supposed to be shared, that's
accidentally riding along in the synced file.

## B.1 Design

Two things need to live in two different places:

1. **The roster** — the list of up to 5 valid staff names — *should* be
   shared/synced, so everyone sees the same consistent list (prevents typos
   like "Aman" vs "aman" vs "Aman S." polluting the audit log). This stays
   in `app_settings`/a new small table, same as today.
2. **"Who is using this machine right now"** — must be **local to the
   machine**, stored outside `master.db`/`sera.salt` (the only two files
   Syncthing touches per the README), so it's never overwritten by a sync
   from someone else's machine.

### New table (in `master.db` — the roster itself)

```sql
CREATE TABLE IF NOT EXISTS staff_users (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE
);
-- capped at 5 rows at the application layer, per your note
```

### New local-only file (NOT synced)

```
~/AmanAssociates_Sera/
├── master.db          # synced
├── sera.salt           # synced
└── device_identity.txt # NEW, local-only — holds this machine's selected staff name
```

## B.2 Flow

1. **First launch on a given machine**: after the master password unlocks
   the DB, if `staff_users` is empty, prompt to seed the roster (up to 5
   names) — this can be done by whichever staff member happens to launch
   first, or reserved for Admin Mode (see below); either way it's a one-time
   setup for the whole team, same spirit as the current MCL/Services setup.
2. **Every launch, every machine**: check `device_identity.txt` locally.
   - If present and its name still exists in `staff_users` → use it
     silently, no prompt (this is what "enter your name once per machine"
     should actually feel like).
   - If absent, or its name was removed from the roster → show a **picker**
     (dropdown of the up to-5 roster names, not free text — this is the
     part of your note that prevents inconsistent naming) and save the
     selection to the local file.
3. **Master password prompt is completely unaffected** — same single shared
   password, same `security.derive_key_hex()` call, no changes here at all.

## B.3 Admin-side roster management

Fits the existing Admin-gated management pattern (parallel to Manage
Services / Manage MCL): a new **"Manage Staff Users"** admin action —
add/rename/remove up to 5 names, backed by simple CRUD on `staff_users`. If
you're following the earlier sidebar blueprint, this slots in next to
"Manage Services" in the Admin Mode nav group.

## B.4 Migration note

Since — per your note — this is prototype-stage and losing the current
database isn't a concern, the simplest implementation is: on first run of
this feature, drop/ignore the old synced `user_actor_label` setting
entirely (it's already meaningless — it only ever reflects one person) and
start `staff_users` fresh. No migration script needed.

## B.5 Phases

**Task 1** — `database.py`: add `staff_users` table + basic CRUD methods
(`add_staff_user`, `list_staff_users`, `remove_staff_user`).

**Task 2** — `main.py`: replace `_ensure_user_actor()` with the local-file
+ roster-picker flow from §B.2. Verify `self.actor` is still passed
everywhere it currently is (audit log, filing status, autofill triggers) —
this is a drop-in replacement, the rest of the app just consumes
`self.actor` as a string same as today.

**Task 3** — Admin Mode: add "Manage Staff Users" screen (§B.3).

**Task 4** — Cleanup: remove the now-dead `user_actor_label` read/write path
once Tasks 1–3 are verified working across at least two machines syncing
the same `master.db`.

## B.6 Non-breaking checklist

- [ ] Master password flow and DB key derivation completely unchanged
- [ ] Existing audit log entries (with the old shared name) remain readable
      — this fix only changes attribution going forward, doesn't rewrite history
- [ ] `self.actor` still flows into `log_action()`, `set_filing_status()`,
      and every other call site exactly as before — only *how* it's
      obtained changes
- [ ] Two machines syncing the same `master.db` correctly show two
      different, stable local identities after the fix
