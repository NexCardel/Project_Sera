# Sera Sync v3 — Blueprint

| | |
|---|---|
| **Status** | Design approved 2026-09-23. Nothing implemented yet. Doc version **1.3** (changelog in §10). |
| **Agent table** | `docs/sera-sync-v3-agents.xlsx`: which model may do which WP, and status tracking. Agents update it only through `tools/sync_v3_tracker.py` (§8). |
| **Owner** | Nex |
| **Baseline** | commit `4963ab8` (line numbers below refer to this commit) |
| **Replaces** | the design in `sync_peer.py` ("Sera Sync v2") and `docs/operations-sync.md` §1–2 |
| **Hard requirement** | Everything stays offline and on the office LAN. No server, no internet, no cloud relay. |

This document is written so a work package (WP) can be handed to an implementing model one at a time. Read §0 before starting any WP.

---

## 0. Rules for whoever implements this

1. **Do one WP at a time, in the order of the table in §3.** Each WP lists its dependencies. Do not start a WP whose dependencies are not merged.
2. **Never touch the real data folder** `~/AmanAssociates_Sera/` (that is `APP_DIR` in `main.py`) from tests, scripts or experiments. Tests use pytest's `tmp_path`. The real folder holds the office's client data. (The `GEMINI.md` rule about copying `sera_extension/` there is unrelated to sync work; no WP here changes the extension.)
3. **Never delete a database, salt or key file.** When something must be replaced, rename or copy the old one to a `*.bak-<YYYYmmdd_HHMMSS>` file or a backup folder first.
4. **Never weaken, skip or delete an acceptance test to make it pass.** If a test can't pass as written, stop and report why.
5. **Don't bump version numbers, build installers, publish releases or `git push`.** Ask the owner first, every time. The owner does releases (versions in `version.py`, `build_tools/installer_setup.iss` and both extension manifests must agree, and `version.json` must not move before a release is published).
6. **New libraries are imported lazily** (inside the function that needs them), never at module top level in `main.py`/`database.py`. Start-up speed is a tracked requirement.
7. **`sync_peer.py` and every new `sync_*.py` module must not import PySide6.** UI talks to them through callbacks, as today (`main.py` wires Qt signals).
8. **`SUDR/` is not shipped. Don't edit it**, even though `SUDR/main.py` contains an old copy of the sync wiring. Only `main.py` at the repo root matters.
9. `source_2/`, `backups/`, `.restore_points/`, `build/`, `package_*` are old copies. Don't edit them.
10. At the end of every WP run the full suite: `venv\Scripts\python -m pytest tests -q`. Report the result honestly, including failures that were already there before you started.
11. **When a T3 step in a WP says "stop and ask", do exactly that.**
12. **Client privacy:** logs, test fixtures, reports and chat output may show only PAN, client name, status, ARNs/reference codes, form types, periods and timestamps. Never print or copy email addresses, phone numbers, bank details, addresses or passwords (same rule as `GEMINI.md`). Test data is invented, never copied from a real DB.
13. **Keep the doc in step with the code:** see §8.3. You record progress and deviations; you don't rewrite the spec yourself.
14. **Update the tracker spreadsheet only with `tools/sync_v3_tracker.py`**, never by editing the xlsx directly. If the tool says `REFUSED`, don't work around it: report the message to the owner.

### Intelligence tiers used in this document

| Tier | Meaning | Suitable model class |
|---|---|---|
| **T1 – Mechanical** | The edit is spelled out. Little judgement, small blast radius, and a test catches mistakes. | Claude Haiku 4.5 and up. Any current Gemini text model (Flash-Lite included). |
| **T2 – Standard** | Requires reading the surrounding code and making local decisions. Spec and tests are provided. Mistakes are visible (tests fail, UI breaks). | Claude Sonnet 5 and up. Gemini 3.8 Flash, Gemini 3.1 Pro (preview), Gemini 3.7 Flash. Older Gemini models: see the xlsx. |
| **T3 – Expert** | Cryptography, concurrency, distributed consistency or migration of live data. **Mistakes are silent**: data loss, divergent PCs or a security hole that no test notices. | Claude Fable 5.1 or Opus 5.5 only (owner decision: no Gemini on T3). If a weaker model writes it anyway, Fable/Opus or a human must review the diff against this spec before merge. |

The full model × tier matrix, with every Gemini model, is in `docs/sera-sync-v3-agents.xlsx`, sheet **Models**.

---

## 1. What is broken today (evidence)

| # | Finding | Where | Effect |
|---|---|---|---|
| F1 | `get_sync_metrics` counts `clients WHERE is_deleted = 0`, but `clients` has no `is_deleted` column (it has `is_archived`). The query throws and the `except` returns all zeros. Verified on a scratch DB 2026-09-23. | `database.py:2667-2670`, `2711-2720` | Every PC reports 0 clients / Rev 0. Every PC thinks it is "bootstrapping" forever, so the quarantine blocks every non-forced push (`sync_peer.py:723-728`), and the auto-pull never fires (it needs `client_count > 0`). **Automatic sync has been effectively off since v2.4.1.** Only the dialog's forced manual push (`ui/dialogs/sera_sync_dialog.py:522`) moves data. |
| F2 | New PC: a random salt is created at launch, then the default password `admin123` creates an empty DB and is saved to `sera.key`. Later launches return `sera.key` without checking it. | `main.py:189-190`, `1079-1111` | After the office DB + salt arrive, the key is wrong unless the office password is `admin123`. The app shows "Database Error" and exits. There is never a password prompt. |
| F3 | Incoming DBs are written over the files while the app is running. The bootstrap pull uses the "live" path (no restart). | `sync_peer.py:561`, `646-687` | The running process keeps the key from the old salt, so every query fails. |
| F4 | The sender reads `master.db` raw from disk while the DB is in WAL mode. | `sync_peer.py:746-749` | Recent commits sit in `-wal` and are missing. A page can be copied mid-write. SQLCipher reports a corrupt page with the same error as a wrong key. |
| F5 | On Windows, `os.replace` fails on a memory-mapped open file (`mmap_size` 256 MB), so the code falls back to writing over the open file in place. It also deletes `-wal`/`-shm` of an open DB. | `sync_peer.py:662-687`, `database.py:389` | Torn / corrupted database. |
| F6 | `rawPayload.db` is encrypted with the same key but never transferred. On key mismatch, `_auto_heal_raw_db` backs it up and replaces it with an empty DB, silently. | `database.py:510-551` | A PC's tracker data disappears from view after a sync that changed the salt. |
| F7 | `DOM_Parser_1/dom_parser.py` and `SDC_Parser/sdc_parser.py` derive the key themselves from `sera.key` + `sera.salt`. | those files, `get_db_hex_key` | They break whenever the key situation changes. |
| F8 | The unit of sync is the whole DB file. "Rev Score" is `clients×10000 + logs×10 + …`, which measures size, not newness. | `database.py:2697` | Concurrent edits: one PC's work is thrown away. Deletions are undone. |
| F9 | No authentication. `request_database_pull` sends DB + salt to anyone. `push_database` with `force_override: true` from anyone replaces the DB. Password stored in plaintext `sera.key`, default `admin123`. | `sync_peer.py:554-563`, `603` | Any device on the office Wi-Fi can copy or replace the client database, which contains portal credentials. |
| F10 | Discovery = one UDP broadcast to `255.255.255.255` (one adapter). Windows Firewall blocks it on "Public" networks. Peers are keyed `host:ip`. | `sync_peer.py:346-362`, `102-103` | Peers randomly missing. PCs on Wi-Fi / another subnet never see each other (the office has such PCs, see D4). |
| F11 | Peer audit logs are stored in **unencrypted** SQLite files in `peer_logs/`. | `database.py:4845+` (`PeerAuditLogManager`) | Audit data readable without the key. |
| F12 | Start-up maintenance rewrites data on every PC (`resequence_client_serial_numbers`, name clean-ups, tracker dedupe keeping the "newest **id**"). | `database.py:75-104`, `580-620`, `2439`, `3967` | Harmless today. Once changes replicate (Phase 3), PCs would fight over it, because local ids differ per PC. |
| F13 | Local integer ids leak into data: `client_id_token` falls back to `str(id)`, and `dataset_key`/identity strings use `f"CLI_{client_id}"`. | `database.py:748-751`, `1837`, `1879`, `584`, `3713` | The same client gets different tokens/keys on different PCs once ids diverge. |
| F14 | A fresh DB seeds default rows (6 staff slots, default columns, services) on every PC. | `database.py:829-833`, `876-881` | Duplicates when PCs merge. |

Evidence on the owner's PC: on 2026-08-29 there were five full-DB overwrites in 23 minutes (`master.db.pre-sync-*`), the DB size went up and down (782K → 745K → 757K), and `sera.salt` changed partway through (`af62…` → `74f4…`).

---

## 2. Decisions (owner-approved 2026-09-23)

- **D1** Offline / LAN only. No server, no internet, no relay.
- **D2** Client data merges field by field. The latest edit of each field wins. Two people editing different fields of the same client both keep their edits.
- **D3** One **admin PC** has final say over the staff roster and office membership (which PCs belong). It replaces `inv_frames`. Settings are **not** admin-PC-only: see D7. Anyone who knows the master password can move the admin role to another PC.
- **D4** Some PCs are on Wi-Fi or a different subnet. Discovery must work without broadcast: known-address book, manual "add PC by IP", and address gossip.
- **D5** Local integer ids (`clients.id`, …) stay as they are, so the UI and `database.py` queries don't change. Sync identifies rows by a global id (`gid`).
- **D6** Syncthing (or any file-copy tool) on the live database is no longer supported.
- **D7** (2026-09-23, v1.1) **Every `app_settings` key is office-wide.** There are no per-PC settings. **Any PC in admin mode (PIN) can change them** (owner decision v1.2); they replicate as normal field-level changes, the latest edit wins, and no admin-PC signature is needed. Consequence the owner accepted: theme and window mode also apply to every PC, and a PIN change (`admin_pin_hash`) made on any PC applies everywhere.
- **D8** (v1.1) **New client ID tokens get a per-PC letter prefix**: `A-1`, `A-2` … on the admin PC, `B-1` … on the next PC, and so on. Tokens can never collide without the PCs talking to each other, and staff can see which PC created the client. Existing numeric tokens stay as they are. Letters are handed out by the admin PC at pairing (P2-2) and never reused, not even after a PC is removed.
- **D9** (v1.1) **Restoring a backup on one PC becomes the new state on every PC.** It's admin-only and shows a confirmation with counts. Edits made anywhere **after** the restore still win normally (P4-3b).
- **D10** (v1.1) `SUDR/` is not shipped and is ignored.

---

## 3. Phase plan at a glance

| WP | Phase | What | Tier | Depends on | Review before merge |
|---|---|---|---|---|---|
| P0-1 | 0 Hotfix | Stop sending the whole DB after every edit | T1 | – | – |
| P0-2 | 0 | Fix `get_sync_metrics` (`is_deleted` → `is_archived`) | T1 | **P0-1** (order matters!) | – |
| P0-3 | 0 | Sender makes a consistent snapshot (`sqlcipher_export`) and streams it | T2 | – | T3 |
| P0-4 | 0 | Receiver stages the DB and swaps it at start-up, never while running | T2 | P0-3 | T3 |
| P0-5 | 0 | Start-up checks that `sera.key` opens the DB; prompts otherwise | T2 | – | – |
| P0-6 | 0 | First-run "New office / Join office" + legacy join with on-screen approval | T2 | P0-3, P0-4, P0-5 | T3 |
| P0-7 | 0 | Authenticate legacy sync messages (HMAC) | T2 | P0-6 | **T3** |
| P0-8 | 0 | `rawPayload.db` auto-heal: visible alert + audit entry | T1 | – | – |
| P0-9a | 0 | Installer firewall rules (Private + Domain) | T1 | – | – |
| P0-9b | 0 | In-app warning when the network profile is Public | T2 | – | – |
| P0-10 | 0 | Per-adapter broadcast, "Add PC by IP", peers keyed by host | T2 | – | – |
| P0-11 | 0 | Docs: `operations-sync.md` update | T1 | P0-1…P0-10 | – |
| P1-1 | 1 Office key | `sera_keys.py`: DPAPI, office.json, key id, recovery blob | **T3** | P0 released | T3 |
| P1-2 | 1 | Start-up key resolution (office mode / legacy mode) | T2 | P1-1 | – |
| P1-3 | 1 | Parsers load the key through `sera_keys` | T1 | P1-2 | – |
| P1-4 | 1 | Migration "convert this PC to an office key" | **T3** | P1-2 | T3 |
| P1-5 | 1 | Key-fingerprint gate everywhere | T2 | P1-2 | – |
| P1-6 | 1 | Recovery, recovery kit export, master-password change | T2 | P1-1 | T3 |
| P2-1 | 2 Devices | Device identity + certificates | T2 | P1-1 | **T3** |
| P2-2 | 2 | Office admin key, membership records, signatures | **T3** | P2-1 | T3 |
| P2-3 | 2 | Mutual-TLS transport + framing | **T3** | P2-1 | T3 |
| P2-4 | 2 | Pairing with 6-digit code (SPAKE2) | **T3** | P2-2, P2-3 | T3 |
| P2-5 | 2 | Discovery v3, address book, gossip | T2 | P2-3 | – |
| P2-6 | 2 | Snapshot service + joiner install | T2 | P2-3, P2-4 | T3 |
| P2-7 | 2 | UI: Join wizard, Add workstation, Members, Remove, Hand over admin | T2 | P2-4…P2-6 | – |
| P2-8 | 2 | Rejoin + salvage import for existing diverged PCs | **T3** | P2-6 | T3 |
| P3-0 | 3 Changes | Multi-node test harness + convergence tests (written first) | T2 | P2-3 | T3 |
| P3-1 | 3 | Table / setting classification registry `sync_schema.py` | T2 | – | T3 |
| P3-2 | 3 | `gid` columns, back-fill, deterministic seed gids, `_sync_*` tables | T2 | P3-1 | – |
| P3-3 | 3 | Capture triggers + sealer + hybrid logical clock | **T3** | P3-2 | T3 |
| P3-4 | 3 | Apply engine (field LWW, tombstones, FK translation, parking, admin signatures) | **T3** | P3-3 | T3 |
| P3-5 | 3 | Sync session protocol, forwarding, poke, scheduler | **T3** | P3-4, P2-5 | T3 |
| P3-6 | 3 | Make start-up maintenance admin-only / id-independent (F12, F13) | T2 | P3-2 | T3 |
| P3-7 | 3 | Shadow mode: capture check, replica convergence, digests | T2 | P3-5 | T3 |
| P3-8 | 3 | UI refresh by table + Sera Sync panel status | T2 | P3-5 | – |
| P3-9 | 3 | Go live (install converged replica, switch mode) | T2 | P3-7 criteria met | Owner decision |
| P4-1 | 4 Cleanup | Remove legacy protocol, inv_frames, Rev Score, peer_logs | T2 | P3-9 on all PCs | – |
| P4-2 | 4 | Remove `sera.key`/`sera.salt` use (keep legacy importer for restores) | T2 | P4-1 | – |
| P4-3a | 4 | Scheduled local backups (daily, keep 14) | T2 | P1-2 | – |
| P4-3b | 4 | Restore a backup → becomes the state on every PC (D9) | **T3** | P4-3a, P3-9 | T3 |
| P4-4 | 4 | Change-log compaction | **T3** | P3-9 | T3 |
| P4-5 | 4 | Docs + test clean-up | T1 | all | – |

**Release grouping**
- Phase 0 is a normal 2.x release.
- Phases 1 + 2 ship together as 3.0. The office key is useless until pairing can hand it to other PCs. P1 can be merged earlier if it is disabled by default.
- Phase 3 ships in shadow mode first (3.1), then goes live (3.2) after the go-live criteria in P3-7.

**Rough effort:** Phase 0 ≈ 1 week, Phase 1 ≈ 1 week, Phase 2 ≈ 2 weeks, Phase 3 ≈ 3–4 weeks including shadow time, Phase 4 ≈ 1 week.

---

## 4. Target architecture

### 4.1 Keys: one office key, never derived per PC

- **Office data key (DEK):** 32 random bytes, created once when the office is created (or migrated, P1-4). Every Sera database (`master.db`, `rawPayload.db`) is encrypted with it directly: `PRAGMA key = "x'<64 hex chars>'"` (the same raw-key form the code already uses).
- **On each PC** the DEK is stored in `keys/office_key.dpapi`, encrypted with Windows DPAPI (CurrentUser scope). Launch needs no password prompt, which keeps today's UX. `sera.key` goes away.
- **Recovery blob** `keys/office_key.recovery`: the DEK encrypted with a key derived from the **master password** (Argon2id → AES-256-GCM). Used when DPAPI can't decrypt (Windows profile reset, new Windows account) and for "export recovery kit". Changing the master password re-encrypts this blob only, never the database.
- **Key id** = first 32 hex chars of `HMAC-SHA256(DEK, b"sera-key-id-v1")`. Stored in `keys/office.json`, sent in every beacon/message, checked before opening or applying anything. A mismatch gives a clear message and changes nothing: no auto-heal, no crash.
- The salt (`sera.salt`) is no longer involved in the key.

### 4.2 Devices, admin and membership

- **Device identity:** each PC has an ECDSA P-256 key pair and a self-signed certificate. `device_id` = first 32 hex chars of SHA-256 over the public key (SubjectPublicKeyInfo DER). The private key is stored encrypted; its passphrase is DPAPI-protected.
- **Office admin key:** one Ed25519 key pair per office. Its public key is in `office.json`. The private key is:
  - (a) DPAPI-protected on the admin PC (`keys/admin_key.dpapi`);
  - (b) encrypted with the master password (`admin_key.recovery`) and replicated to all members, so any PC can become admin by entering the master password (D3).
- **Admin-signed records:** membership (add/revoke device, device role), the "current admin PC" record and staff-roster changes (D3). Settings are not signed (D7). Every PC verifies the signature before accepting them. Everything else only needs the sender to be a member.
- **Members list:** a signed record per device (`device_id`, name, cert PEM, role, added_at, revoked_at). Connections from devices that are not active members are dropped.

### 4.3 Transport and discovery

- **All sync traffic** runs over TLS 1.3 with **both sides presenting certificates** (Python `ssl`, standard library). The trust store is exactly the active members' certificates. After the handshake each side checks the peer's certificate fingerprint against the members list.
- **Pairing** (a PC joining) uses a 6-digit code shown on the admin PC and typed on the new PC. It uses SPAKE2 (password-authenticated key exchange, the same design as magic-wormhole), so the code can't be brute-forced from recorded traffic. Wrong code 3 times → the pairing window closes.
- **Discovery** tries these in order, for each member:
  1. The last address that worked.
  2. Other known addresses (from gossip or manual entry).
  3. Addresses seen in beacons.

  Beacons go to `255.255.255.255` **and** to each adapter's directed broadcast address. Members exchange their known addresses in every session, so a Wi-Fi PC learns a wired PC's address from anyone who knows it (D4). "Add PC by IP" is always available.

**Ports**

| Port | Proto | Use | Phase |
|---|---|---|---|
| 49152 | TCP/WS | Extension bridge (unrelated, don't touch) | – |
| 49156 | UDP | Beacons (v2 and v3 share the port; different `magic`) | 0+ |
| 49157 | TCP | Legacy v2 sync (removed in P4-1) | 0–3 |
| 49158 | TCP | Pairing listener (open only while "Add workstation" is open) | 2+ |
| 49159 | TCP | v3 sync over mutual TLS | 2+ |

The installer opens 49156–49159 for the app executable on Private and Domain profiles (P0-9a).

### 4.4 Replicating changes, not files (Phase 3)

- Every replicated table gets a `gid` (random 128-bit hex) filled by an AFTER INSERT trigger. Local ids stay local. Sync messages use gids, and foreign keys are translated gid ↔ local id on each PC.
- **Capture:** SQLite triggers record every insert/update/delete of replicated tables into `_sync_pending`. This includes writes by DOM_Parser/SDC_Parser and anything else, without editing each of the ~5,000 lines of `database.py`. After each commit a **sealer** turns pending rows into change records with a **hybrid logical clock (HLC)** timestamp and a per-origin sequence number.
- **Merge rules** (from the registry in P3-1):
  - `lww`: per column, the higher `(hlc, origin)` wins (D2).
  - `admin_lww`: like `lww`, but only accepted with a valid admin signature (D3).
  - `append`: insert-only, union by gid; can never conflict.
  - `set`: add/remove per membership pair, row-level LWW.
  - `local`: never replicated.
  - Deletes leave a **tombstone**; "delete wins", and an edit discarded by a delete is written to `_sync_conflicts` for admin review.
- **Anti-entropy:** each PC keeps a vector `{origin_stream: highest contiguous seq applied}`. Two PCs exchange vectors and send each other exactly what's missing. They also forward changes from third PCs, so A's edits reach C through B. Every batch is applied in one transaction together with the vector update. That makes it crash-safe, idempotent and resumable. A 20-second periodic sync plus a small "poke" after local edits means a lost message is repaired on the next round instead of causing damage.
- **No file replacement ever happens on a running app.** Snapshots are only installed when joining (before any DB is opened) or at start-up via a staged swap.

### 4.5 Files on disk after v3

```text
~/AmanAssociates_Sera/
|-- master.db              (encrypted with DEK; includes _sync_* tables)
|-- rawPayload.db          (encrypted with DEK; its own _sync_* tables)
|-- keys/
|   |-- office.json        (plaintext, not secret: office_id, office_name, key_id, admin_pubkey, device_id, format)
|   |-- office_key.dpapi
|   |-- office_key.recovery
|   |-- device_cert.pem
|   |-- device_key.pem     (PEM encrypted with a random passphrase)
|   |-- device_key.pass.dpapi
|   |-- admin_key.dpapi    (admin PC only)
|   `-- admin_key.recovery
|-- incoming/              (staged snapshots, pending_swap.json)
|-- shadow/                (Phase 3 shadow replicas)
|-- backups/               (scheduled snapshots, pre-migration copies)
`-- logs/sync.log
```

---

## 5. Work packages

Every WP ends with "Accept". Those are the tests/checks that must pass. New tests go in `tests/`, named as stated.

### Phase 0 — Hotfix on the current protocol

#### P0-1 Stop the whole-DB broadcast after every edit (T1)
- **Files:** `main.py` `_broadcast_live_update_to_peers` (line 1713).
- **Do:**
  - Remove the per-peer `request_pull_from` / `push_to(..., live_update=True)` block (lines 1717-1740 and the thread loop that calls it).
  - Keep the tracker-dump and audit-log telemetry broadcast, but debounce it: at most once every 60 s. If another call arrives in the window, one run happens at the end of the window. Use a `threading.Timer` guarded by a lock.
- **Don't:** touch `sync_peer.py`'s TCP server (manual pushes must still work).
- **Accept:** `tests/test_sync_hotfix.py::test_write_does_not_push_database`: with a fake `sync_service` recording calls, 10 calls to `_broadcast_live_update_to_peers` within 1 s → `push_to` and `request_pull_from` never called, telemetry broadcast called at most once per 60 s window (use an injectable clock or a short test interval).

#### P0-2 Fix `get_sync_metrics` (T1) — only after P0-1 is merged
- **Why the order:** with correct counts, the quarantine lifts and automatic whole-DB pushes would start again everywhere. P0-1 removes that path first.
- **File:** `database.py:2667-2670`. Replace `is_deleted = 0` with `is_archived = 0` and `is_deleted = 1` with `is_archived = 1`.
- Make the `except` at `2711` also `print(f"[database] get_sync_metrics failed: {e}")` so this can't hide again.
- **Accept:** `tests/test_sync_hotfix.py::test_sync_metrics_counts_clients`: a scratch DB with 2 active + 1 archived client → `client_count == 2`, `archived_count == 1`, `sync_revision > 0`.

#### P0-3 Consistent snapshot on the sending side (T2, review T3)
- **Files:** `sync_peer.py` (`push_to`), new helper `make_snapshot(db, dest_path)` in `database.py`.
- **Do:**
  - `make_snapshot` opens a connection with the current key and runs:
    ```sql
    ATTACH DATABASE '<dest>' AS snap KEY "x'<hex_key>'";
    SELECT sqlcipher_export('snap');
    DETACH DATABASE snap;
    ```
    Then it copies `PRAGMA user_version` to the snapshot. Escape the path (single quotes doubled). `dest` lives in a temp dir under `APP_DIR/incoming/out/`.
  - `push_to` sends the snapshot file, not `self.db_path`. Stream it in 1 MB chunks from the file instead of `f.read()` into memory. Delete the temp file in `finally`.
  - `_recv_exact`: replace `buf += chunk` with a `bytearray` or `io.BytesIO` (avoids quadratic copying). The receiver writes chunks straight to a file (see P0-4).
- **Accept:** `test_snapshot_includes_wal_changes`: write rows without checkpointing (WAL mode) → the snapshot opened with the same key contains them, and `PRAGMA cipher_integrity_check` returns no rows.

#### P0-4 Receiver stages, swaps at start-up (T2, review T3)
- **Files:** `sync_peer.py` `_handle_incoming_push`; `main.py` start-up (before line 188); `_on_live_sync_received`.
- **Do:**
  1. Incoming DB and salt bytes are streamed to `incoming/master.db.part` and `incoming/sera.salt.part`, then renamed to the final staging names.
  2. **Verify before accepting:** derive the key with the local `sera.key` password and the **incoming** salt, then open the staged DB and run `SELECT count(*) FROM sqlite_master` and `PRAGMA cipher_integrity_check`. If that fails, reply `{"status":"rejected","reason":"PASSWORD_MISMATCH"}` and delete the staging files. This PC's password differs from the sender's; the UI says so.
  3. On success, write `incoming/pending_swap.json` (`{"db": "...", "salt": "...", "from": host, "at": iso}`), reply `ok`, and call `on_sync_received` (the existing restart dialog, `_lock_and_force_restart`). **The live path no longer replaces files:** `live_update` pushes are treated exactly like normal ones.
  4. New function `apply_pending_swap(app_dir)` in `sync_peer.py`, called first thing in `main.py` start-up **before any `SeraDatabase` is created**. It:
     - copies the current `master.db`/`sera.salt` to `master.db.pre-sync-<ts>.db` / `sera.salt.pre-sync-<ts>`;
     - `os.replace`s the staged files in;
     - removes `master.db-wal`/`-shm` (safe now: nothing is open);
     - deletes `pending_swap.json`;
     - on any error, restores the pre-sync copies and leaves `pending_swap.json` renamed to `.failed`.
  5. Delete `safe_write_file` and the in-place fallback (`sync_peer.py:662-676`).
- **Accept:**
  - `test_push_is_staged_not_live`: after an accepted push, the live `master.db` bytes are unchanged and `pending_swap.json` exists.
  - `test_apply_pending_swap`: swaps, backups exist, WAL files removed.
  - `test_push_with_other_password_rejected`: reason `PASSWORD_MISMATCH` and no staging files left.

#### P0-5 Start-up checks the password (T2)
- **File:** `main.py` `_get_master_password` (1079-1111).
- **Do:**
  - If `sera.key` exists, **test it** (derive the key, open the DB read-only-ish with `SELECT count(*) FROM sqlite_master`).
  - If it fails, show the prompt, up to 3 tries, with the text "This PC's saved password doesn't open the office database. Enter the office master password." Save to `sera.key` only after a password opens the DB.
  - The `admin123` default is tried only when `master.db` **already exists**. Never create a DB as a side effect of the check: the current code's `SeraDatabase(self.db_path, hex_key)` creates one. Check existence first; for the test use a bare connection, not `SeraDatabase`.
  - First-run DB creation moves to P0-6.
- **Accept:** `test_wrong_saved_password_prompts` (monkeypatch the prompt): a wrong `sera.key` plus a correct prompt answer → returns the right password and rewrites `sera.key`. A missing DB → no DB file created by `_get_master_password`.

#### P0-6 First run: "New office" or "Join office" (T2, review T3)
- **Files:** `main.py` start-up, new `ui/dialogs/first_run_dialog.py`, `sync_peer.py` (new action).
- **When:** `master.db` does not exist in `APP_DIR`. Move salt generation (`main.py:189-190`) inside the "New office" branch.
- **New office:**
  - Ask for a master password (min 8 chars, typed twice; `admin123` refused), create salt + DB, write `sera.key`.
- **Join office:**
  1. The joiner's sync service isn't running yet. The dialog uses a helper that only listens for beacons for 10 s and lists PCs (host + user). It also has an "Enter IP" field.
  2. The joiner connects **out** to the chosen PC and sends `{"action":"fetch_snapshot","host":..,"username":..,"code":<6 random digits shown on the joiner's screen>}`. The joiner makes only outbound connections, so its own firewall doesn't matter.
  3. The serving PC shows a modal: "Workstation <host> (<username>) wants to join. Code on their screen: 123 456. Allow?" Waiting times out after 120 s → reject.
  4. If allowed, the server sends `ready` + the snapshot (P0-3) + salt on the **same connection**. The joiner streams it to `incoming/` and asks for the office master password. It verifies the password against the staged files (as in P0-4 step 2), then installs files and writes `sera.key`. Then start-up continues normally, with no restart needed because nothing was open.
  5. The server allows at most one pending join request at a time and ignores `fetch_snapshot` while its own modal is open.
- **Accept:**
  - `test_join_flow_end_to_end` with two services on localhost (different temp dirs and ports) and the approval callback auto-answering yes: the joiner ends with a DB that opens with the office password.
  - With the callback answering no, nothing is written on the joiner.

#### P0-7 Authenticate legacy messages (T2, review **T3**)
- **Files:** `sync_peer.py`.
- **Do:**
  - Every outgoing header gets `ts` (unix seconds) and `mac = HMAC-SHA256(auth_key, canonical_json(header without mac))`, where `auth_key = HMAC-SHA256(bytes.fromhex(hex_key), b"sera-sync-auth-v1")`. Pass the hex key into `SyncPeerService.__init__` as a new parameter.
  - The receiver rejects if the mac is missing or wrong (use `hmac.compare_digest`), or if `abs(now - ts) > 120`.
  - Exception: `fetch_snapshot` (P0-6), which is protected by the on-screen approval instead.
  - `canonical_json` = `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.
  - Also remove `request_database_pull` handling for unauthenticated senders (covered by the same check).
- **Accept:** `test_unauthenticated_push_rejected`, `test_unauthenticated_pull_rejected`, `test_stale_timestamp_rejected`, `test_authenticated_push_accepted`.

#### P0-8 `rawPayload.db` auto-heal is visible (T1)
- **File:** `database.py:510-551`.
- **Do:**
  - Keep the backup-and-recreate behaviour, but record what happened: set `self.raw_db_was_reset = backup_name`.
  - Write an audit-log entry `action="raw_db_reset"`.
  - `main.py` shows a persistent alert after start-up: "Tracker database could not be opened with this office's key and was reset. Backup: <name>".
  - Skip auto-heal entirely if `master.db` itself failed to open.
- **Accept:** `test_raw_db_reset_is_reported`.

#### P0-9a Installer firewall rules (T1)
- **File:** `build_tools/installer_setup.iss` (`[Run]` section at line 69, and add `[UninstallRun]`).
- **Add:**
  ```
  Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Amas Sera Sync"" dir=in action=allow program=""{app}\Amas_Sera.exe"" enable=yes profile=private,domain"; Flags: runhidden
  ```
  Uninstall: `delete rule name="Amas Sera Sync"`. Check the exe name against `[Setup]`/`[Files]` in the same file.
- **Accept:** manual check by the owner on a test PC. `netsh advfirewall firewall show rule name="Amas Sera Sync"` lists the rule.

#### P0-9b Warn on a "Public" network (T2)
- **Do:**
  - Use `comtypes` (already a dependency) with the NetworkListManager COM object (`{DCB00C01-570F-4A9B-8D69-199FDBA5723B}`) to read the category of connected networks (0 = Public, 1 = Private, 2 = Domain). Do this in a background thread at start-up and every 10 minutes.
  - If the active network is Public, the Sera Sync panel shows a warning with the fix: Windows Settings → Network → set to Private.
  - Fallback if COM fails: run `powershell -NoProfile -Command "Get-NetConnectionProfile | ConvertTo-Json"`.
- **Accept:** unit test with the probe function monkeypatched. Owner checks manually on a real PC.

#### P0-10 Discovery that reaches Wi-Fi and other subnets (T2)
- **Files:** `sync_peer.py`, `ui/dialogs/sera_sync_dialog.py`, `requirements.txt` (add `ifaddr`, pure Python, used lazily).
- **Do:**
  1. The beacon sender sends to `255.255.255.255` **and** to each IPv4 adapter's directed broadcast address (from `ifaddr.get_adapters()`: `ip | ~netmask`). Skip `127.*` and `169.254.*`.
  2. **"Add PC by IP"** in the Sera Sync dialog stores addresses in the setting `sync_manual_peers` (JSON list). It is office-wide like every setting (D7), so each PC skips its own addresses in the list. Every 30 s the service sends a unicast beacon to each of them, and they answer with a unicast beacon back. So PCs on routed subnets see each other without broadcast.
  3. The peer table key becomes the host name (`PeerInfo.key()` returns `self.host`), so an IP change updates the entry instead of adding a ghost.
- **Accept:** `test_manual_peer_unicast_beacon` (two services on localhost, broadcast disabled by a flag, discovery via the manual list), `test_peer_ip_change_updates_entry`.

#### P0-11 Docs (T1)
- Update `docs/operations-sync.md`:
  - Remove the Syncthing-on-live-DB advice.
  - Describe the Join flow, the Public-network warning and Add PC by IP.
  - Say that `inv_frames` stays only until v3.

### Phase 1 — Office key

#### P1-1 `sera_keys.py` (T3)
- **New module** at repo root, next to `security.py`. No PySide6. Functions:
  ```python
  KEYS_DIRNAME = "keys"
  def dpapi_protect(data: bytes, entropy: bytes) -> bytes
  def dpapi_unprotect(blob: bytes, entropy: bytes) -> bytes
  def new_dek() -> bytes                               # os.urandom(32)
  def key_id(dek: bytes) -> str                        # hmac sha256(dek, b"sera-key-id-v1").hexdigest()[:32]
  def dek_hex(dek: bytes) -> str                       # dek.hex()  -> used in PRAGMA key = "x'...'"
  def wrap_with_password(secret: bytes, password: str, aad: bytes) -> dict
  def unwrap_with_password(blob: dict, password: str, aad: bytes) -> bytes   # raises WrongPassword
  def load_office(app_dir) -> OfficeInfo | None        # reads keys/office.json
  def save_office(app_dir, info: OfficeInfo) -> None
  def load_dek(app_dir) -> bytes                       # DPAPI file; raises KeyUnavailable
  def store_dek(app_dir, dek: bytes, password: str, office_id: str) -> None  # writes .dpapi + .recovery
  def recover_dek(app_dir, password: str) -> bytes     # from .recovery, then re-stores .dpapi
  def atomic_write(path, data: bytes) -> None          # tmp file + flush + os.fsync + os.replace
  ```
- **DPAPI via `ctypes`** (pywin32 is not installed). Reference sketch; test the round trip:
  ```python
  import ctypes
  from ctypes import wintypes
  class _BLOB(ctypes.Structure):
      _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
  def _in(data):
      buf = ctypes.create_string_buffer(data, len(data))
      return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf
  def dpapi_protect(data, entropy):
      i, _k1 = _in(data); e, _k2 = _in(entropy); o = _BLOB()
      if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(i), None, ctypes.byref(e), None, None, 0x1, ctypes.byref(o)):
          raise ctypes.WinError()
      try: return ctypes.string_at(o.pbData, o.cbData)
      finally: ctypes.windll.kernel32.LocalFree(ctypes.cast(o.pbData, ctypes.c_void_p))
  # dpapi_unprotect: same with CryptUnprotectData(byref(i), None, byref(e), None, None, 0x1, byref(o))
  ```
  Entropy constants: `b"SeraOfficeKey/v1"` (DEK), `b"SeraDeviceKey/v1"` (P2-1), `b"SeraAdminKey/v1"` (P2-2).
- **Password wrap format** (JSON, base64 fields):
  `{"format":1,"kdf":"argon2id","t":3,"m_kib":65536,"p":4,"salt":<16 B>,"nonce":<12 B>,"ct":<AESGCM(kek).encrypt(nonce, secret, aad)>}`
  with `kek = argon2.low_level.hash_secret_raw(password.encode(), salt, time_cost=t, memory_cost=m_kib, parallelism=p, hash_len=32, type=argon2.low_level.Type.ID)`.
  - AAD for the DEK: `b"sera-recovery-v1|" + office_id.encode()`.
  - AAD for the admin key: `b"sera-admin-v1|" + office_id.encode()`.
  - Use `cryptography.hazmat.primitives.ciphers.aead.AESGCM`.
- `office.json`: `{"format":1,"office_id":<uuid4>,"office_name":str,"key_id":str,"admin_pubkey":<b64, P2-2>,"device_id":<P2-1>,"created_at":iso}`.
- **Don't:** log or print keys, passwords or blobs. Don't store the DEK anywhere unencrypted.
- **Accept:** `tests/test_sera_keys.py`:
  - DPAPI round trip;
  - wrong entropy fails;
  - password wrap round trip;
  - wrong password raises `WrongPassword`;
  - tampered ciphertext fails;
  - wrong AAD (other office_id) fails;
  - `key_id` stable and 32 hex;
  - `atomic_write` leaves no `.tmp` on success.

#### P1-2 Start-up key resolution (T2)
- **File:** `main.py` (before line 188).
- **Order:**
  1. `apply_pending_swap` (P0-4).
  2. If `keys/office.json` exists → **office mode**: `dek = load_dek()`. On `KeyUnavailable`, show the recovery dialog (P1-6). `hex_key = dek_hex(dek)`. Check `key_id` against `office.json`. Never read or write `sera.key` in this mode.
  3. Else → **legacy mode**: today's path with P0-5.
- Expose `self.key_mode` (`"office"`/`"legacy"`) and pass `hex_key` + `key_id` into `SyncPeerService`.
- **Accept:** `test_startup_office_mode_ignores_sera_key`, `test_startup_legacy_mode_unchanged`.

#### P1-3 Parsers use `sera_keys` (T1)
- **Files:** `DOM_Parser_1/dom_parser.py` (`get_db_hex_key`, ~line 40), `SDC_Parser/sdc_parser.py` (~line 25).
- **Do:** first try `sera_keys.load_office(app_dir)` + `load_dek` → `dek_hex`, and keep the existing legacy search as the fallback. If the import of `sera_keys` fails (standalone run), fall back as well.
- **Accept:** a test that creates an office key in a temp dir and checks both functions return `dek.hex()`.

#### P1-4 Migrate a PC to an office key (T3)
- **Where:** Admin → Sera Sync → "Convert to office key (admin PC only)". Button writes `incoming/migrate_request.json` and restarts. The migration runs at start-up, right after `apply_pending_swap`, before any DB is opened.
- **Steps (all or nothing):**
  1. Ask for the current master password and verify it with the legacy key. Ask for the office name. Ask for a new master password, or keep the current one if it isn't `admin123`.
  2. Back up `master.db`, `rawPayload.db`, `sera.salt`, `sera.key` into `backups/pre-office-key-<ts>/`. Checkpoint first: open with the legacy key, run `PRAGMA wal_checkpoint(TRUNCATE)`, close.
  3. `dek = new_dek()`. For each DB, `sqlcipher_export` into `incoming/migrate/<name>` with key `x'<dek hex>'` (as in P0-3), and copy `user_version`.
  4. Verify each new file:
     - it opens with the DEK;
     - `PRAGMA cipher_integrity_check` is empty;
     - `PRAGMA quick_check` = `ok`;
     - per-table `count(*)` equals the old file.
  5. `store_dek` (DPAPI + recovery with the master password) and `save_office` (new `office_id`, `key_id`).
  6. `os.replace` the new DB files over the old ones. Move `sera.key` and `sera.salt` into the backup folder (don't delete).
  7. Continue start-up in office mode.
- **Rollback:**
  - A failure in steps 1–5 deletes `incoming/migrate/` and `keys/` files created in this run, and leaves the legacy files untouched.
  - A failure in step 6 restores from the backup folder.
  - In all cases, show the error and continue in legacy mode.
- **Stop and ask:** if row counts differ in step 4, do not work around it.
- **Accept:**
  - `test_migrate_to_office_key` on a scratch legacy office with data in both DBs;
  - `test_migrate_rollback_on_verify_failure` (monkeypatch verification to fail → legacy files byte-identical afterwards).

#### P1-5 Key-fingerprint gate (T2)
- **Do:**
  - Add `key_id` to legacy beacons and headers. A legacy PC (no key id) and an office-mode PC never exchange databases; the dialog shows "different office key — rejoin needed".
  - `_auto_heal_raw_db`: in office mode, never auto-heal. Raise a clear error naming the file.
- **Accept:** `test_key_id_mismatch_rejected`.

#### P1-6 Recovery, recovery kit, password change (T2, review T3)
- **Recovery dialog:** shown when DPAPI can't decrypt. "This Windows account can't unlock Sera. Enter the office master password." Uses `recover_dek`, 5 tries, and offers "Restore from recovery kit file".
- **Export recovery kit:** Admin menu. Writes `office.json` + `office_key.recovery` (+ `admin_key.recovery` after P2-2) into one `.serakit` JSON file chosen by the user (USB). The master password is required to create it.
- **Change master password:** verify the old password via `unwrap_with_password`, re-wrap the DEK (and the admin key after P2-2). The database is not touched.
- **Accept:** tests for each function (not the dialogs).

### Phase 2 — Devices, pairing, secure transport

#### P2-1 Device identity (T2, review T3) — new `sync_identity.py`
- **Generate on first office-mode start:**
  - an EC P-256 key;
  - a self-signed X.509 cert (subject CN = device_id, 20-year validity, `BasicConstraints(ca=True, path_length=None)` critical, `KeyUsage(digital_signature, key_cert_sign)`).
- `device_id = sha256(public_key SPKI DER).hexdigest()[:32]`.
- **Private key PEM** encrypted with `BestAvailableEncryption(passphrase)`, where `passphrase = os.urandom(32)` is stored in `device_key.pass.dpapi`.
- Provide `load_cert_chain_args() -> (certfile, keyfile, password)` for `ssl`.
- **Accept:** generation is idempotent (the second call loads, doesn't regenerate), `device_id` is stable, and the key PEM is not loadable without the passphrase.

#### P2-2 Office admin key and signed records (T3) — new `sync_admin.py`
- Ed25519 key. Created with the office (and during P1-4 migration). Stored as `admin_key.dpapi` (admin PC) + `admin_key.recovery` (password-wrapped, AAD from P1-1). The public key goes into `office.json`.
- **Signed record** = dict with `sig` = base64 Ed25519 signature over `canonical_json(record without "sig")` (canonical_json from P0-7).
- **Record types:**
  - `member` (`device_id`, `name`, `cert_pem`, `role`: member|admin, `token_letter`, `added_at`, `revoked_at`|null, `rev`: int). `token_letter` is the D8 prefix: `A` for the office creator, then the next unused letter (`B`…`Z`, then `AA`, `AB`…). A revoked member's letter is never reused;
  - `office_admin` (`device_id`, `since`, `rev`);
  - `staff_change` (used by P3-4).
- **Stored in:**
  - Phase 2: `master.db` table `_sync_members(device_id PK, record_json, rev)`;
  - Phase 3: replicated like any admin-scoped table.
- **Rule:** a higher `rev` with a valid signature replaces a lower one.
- `claim_admin(password)`: unwrap `admin_key.recovery`, store `admin_key.dpapi` on this PC, and sign a new `office_admin` record with `rev+1`. The old admin PC sees that it is no longer named and deletes its `admin_key.dpapi`.
- **Accept:**
  - forged or modified records are rejected;
  - a lower `rev` is ignored;
  - `claim_admin` with a wrong password fails and changes nothing.

#### P2-3 Mutual-TLS transport (T3) — new `sync_transport.py`
- **Contexts:**
  - Server: `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)`, `minimum_version = TLSVersion.TLSv1_3`, `load_cert_chain(*identity)`, `verify_mode = CERT_REQUIRED`, `load_verify_locations(cadata=<PEMs of all non-revoked members, including self>)`.
  - Client: `PROTOCOL_TLS_CLIENT`, TLS 1.3 minimum, `check_hostname = False`, `verify_mode = CERT_REQUIRED`, same `cadata`, own cert chain.
  - Rebuild both contexts whenever the members list changes.
- **After the handshake (both sides):**
  - `fp = sha256(sock.getpeercert(binary_form=True))` must match a non-revoked member's cert. The client also checks it's the device it meant to reach. Otherwise close.
  - Run the handshake inside the connection's worker thread, never in the accept loop.
- **Framing:**
  - JSON frames: 4-byte big-endian length + UTF-8 JSON, max 16 MB.
  - Binary chunks: frame `{"t":"chunk","n":len}` followed by raw bytes.
  - Read timeout 30 s per frame. Whole session deadline 10 min.
  - Stream to files, never hold whole DBs in memory.
- Listens on 49159. Max 4 concurrent sessions, extra connections refused with a `busy` frame.
- **Accept:** `tests/test_sync_transport.py` on localhost:
  - two members connect;
  - a non-member cert is refused on both server and client side;
  - a revoked member is refused after a context rebuild;
  - an oversized frame is refused;
  - a slow peer times out.
- **Stop and ask** if OpenSSL rejects the self-signed CA-flag certs. Don't switch to `CERT_NONE` to make it work.

#### P2-4 Pairing (T3) — new `sync_pairing.py`
- **Dependency:** add `spake2` to `requirements.txt` (pure Python). Import lazily. It is bundled by PyInstaller, so it works offline.
- **Admin side:**
  - "Add workstation" creates `code = f"{secrets.randbelow(10**6):06d}"` and shows it as `123 456`.
  - It opens a plain TCP listener on 49158 for 5 minutes, sets beacon `pair: true`, and allows 3 failed attempts in total, then closes.
  - One joiner at a time.
- **Protocol** (each message is a JSON frame; bytes are base64):
  1. Both sides: `s = SPAKE2_Symmetric(code.encode(), idSymmetric=b"sera-pair-v1")`. Each sends `{"t":"spake","m":s.start()}` and computes `K = s.finish(peer_m)`.
  2. `k_conf = HKDF(SHA256, 32, salt=None, info=b"sera-pair-confirm").derive(K)`, `k_enc = HKDF(..., info=b"sera-pair-enc").derive(K)`.
  3. Admin sends `HMAC(k_conf, b"A" + transcript)`, joiner sends `HMAC(k_conf, b"J" + transcript)`, where `transcript = sha256(msgA || msgB)` in a fixed order (admin's spake message first). Verify with `compare_digest`. On failure: count the attempt and close.
  4. Joiner → admin: `AESGCM(k_enc)`-encrypted `{"cert_pem","device_name"}` (random 12-byte nonce, AAD `b"sera-pair-v1|J"`).
  5. Admin creates and signs a `member` record for the joiner, then sends an encrypted `{"office": office.json, "dek": b64, "office_key_recovery", "admin_key_recovery", "members": [...records], "admin_address": ip}` (AAD `b"sera-pair-v1|A"`).
  6. Joiner: `store_dek` (DPAPI only; the recovery blob is saved as received), `save_office`, store the members. Then snapshot download (P2-6).
- **Don't:** log the code, `K`, derived keys or the DEK. Don't accept a second joiner on the same code.
- **Accept:**
  - `tests/test_sync_pairing.py`: success path; wrong code fails and increments attempts; 3 failures close the window; a message modified in transit fails the key confirmation; the joiner ends with an identical `key_id`.

#### P2-5 Discovery v3, address book, gossip (T2)
- **Beacon v3:** `{"magic":"sera-sync-v3","office":<office_tag>,"dev":device_id,"name":device name,"port":49159,"pair":bool}`, where `office_tag = hmac(dek, b"sera-office-tag-v1").hexdigest()[:16]`.
  - While pairing is open, also include `"office_name"` and `"pair_port":49158`, so joiners, who don't have the DEK yet, can list it.
  - Sent every 10 s, same destinations as P0-10 plus unicast to all known member addresses.
- **Address book:** table `_local_addresses(device_id, ip, port, last_ok_at, source)` in `master.db`. `_local_*` tables are never replicated or included in snapshots. Updated on every successful session; `source` = beacon|gossip|manual.
- **Gossip:** the session HELLO (P3-5) carries `addresses: [{device_id, ip, port, last_ok_at}]` seen in the last 7 days.
- **Connect order per member:** last_ok → other known addresses (newest first) → beacon address.
- **Diagnostics:** if a member has addresses but none have worked for 10 min, the panel shows "Can't reach <name> — check that both PCs are on a Private network and that the Wi-Fi doesn't isolate devices".
- **Accept:** tests for the address ordering and for gossip merge. Localhost test: discovery with broadcast disabled.

#### P2-6 Snapshot service and joiner install (T2, review T3)
- **Server** (mTLS, request `{"t":"snapshot"}`):
  - `sqlcipher_export` both DBs into a temp dir.
  - In the copies, delete `_local_*` tables and the rows of `local`-mode tables (P3-1). All `app_settings` rows are kept (D7).
  - Build `manifest = {office_id, key_id, schema_version, vectors (Phase 3; empty before), files:[{name,size,sha256}]}` and stream it.
  - In Phase 3 the vectors must be read in the same transaction as the export: `BEGIN IMMEDIATE`, read the vectors, export, `COMMIT`. This blocks writers for under a second.
- **Joiner:**
  - Download into `incoming/join/`.
  - Verify: sha256; open with the DEK; `cipher_integrity_check` empty; `quick_check` ok; `key_id` and `office_id` match `office.json`.
  - Install with `os.replace` (no DB is open during joining). Continue start-up.
  - **The joiner never seeds default rows** (F14): the DB comes only from the snapshot.
- **Accept:** `test_join_snapshot_end_to_end` (two services on localhost: pair, snapshot, joiner opens the DB and sees the same clients); `test_corrupt_snapshot_rejected`.

#### P2-7 UI (T2)
- **First-run dialog (from P0-6), office mode:**
  - "New office" creates the DEK, admin key, office.json and device identity.
  - "Join office" lists PCs with open pairing (plus Enter IP), asks for the code, then shows download progress.
- **Sera Sync panel:**
  - members (name, online/offline, last successful sync, role, this PC marked);
  - "Add workstation" (admin, or after entering the master password);
  - "Remove" (signed revoke);
  - "Hand over admin" / "Become admin (master password)";
  - network warnings (P0-9b, P2-5);
  - the activity stream (keep the existing widget).
- Remove the `inv_frames` toggle **in P4-1**, not here.
- **Accept:** manual click-through by the owner. Widget construction smoke tests.

#### P2-8 Rejoin and salvage for existing PCs (T3)
Because of F1, office PCs have diverged. Only the admin PC migrates (P1-4). Every other PC rejoins and salvages what only it has.
- **On a legacy-mode PC:** "Rejoin office" wizard.
  1. Move `master.db`, `rawPayload.db`, `sera.salt`, `sera.key` to `legacy/<ts>/`.
  2. Pair (P2-4) and download the snapshot (P2-6).
  3. Salvage: open the legacy DB with the legacy key (read-only) and run a **dry run** first. Show counts, then apply after the user confirms:
     - **Clients:** match by the value of the internal PK column (`mcl_columns.is_internal_pk = 1`, usually PAN), comparing values normalised with upper-case and trim.
       - Unmatched legacy clients are inserted with all their `client_values`. Columns are mapped by `mcl_columns.label`; values for unknown labels are listed in the report and not imported.
       - Matched clients with different values: list them. The default keeps the office value. The user can choose per client "take this PC's values".
     - **`audit_log`:** insert rows where `(ts, actor, action, detail)` is not in the office DB.
     - **`tracker_dump`:** insert rows whose `dataset_key` (recomputed after P3-6) is not present. Timelines are matched by `session_id`.
  4. Write a salvage report to `logs/salvage-<ts>.txt`.
- **Stop and ask:** if a legacy client has no internal-PK value (can't be matched safely), list it and do not auto-insert.
- **Accept:** `test_salvage_dry_run_changes_nothing`, `test_salvage_imports_unmatched_clients`, `test_salvage_keeps_office_value_on_conflict_by_default`.

### Phase 3 — Change replication

#### P3-0 Test harness first (T2, review T3)
- `tests/sync_harness.py`: starts N in-process nodes on localhost, each with its own temp `APP_DIR`, ports, identity and office key (paired through the real P2-4 code).
- **Harness controls:** `node.write(fn)`, `partition(a, b)`, `heal()`, `run_until_quiet(timeout)`, `digest(node)` (from P3-7).
- **Convergence tests** (`tests/test_sync_convergence.py`), each ending with **all digests equal**:
  - (a) 3 nodes, random 200 operations (create/edit/archive/delete client, set values, add services, audit entries, tracker dumps), random partitions, then heal;
  - (b) the same field edited on two partitioned nodes: the higher HLC wins everywhere;
  - (c) different fields of the same client edited on two nodes: both edits survive;
  - (d) delete versus concurrent edit: delete wins and a `_sync_conflicts` row exists;
  - (e) a crash in the middle of applying a batch (injected exception) leaves no partial state, and resync completes;
  - (f) A→B→C forwarding with A and C never connected;
  - (g) a node offline for a long time (compaction, P4-4) gets `NEED_SNAPSHOT` and still converges without losing its own unsent edits.
- Tests (a)–(f) must exist and fail before P3-3 starts. They become the definition of done for P3-3…P3-5.

#### P3-1 Classification registry `sync_schema.py` (T2, review T3)
One declarative table. The implementer **verifies each row against the code** and reports any table or column not listed here, **stop and ask** for those.

| Table | DB | Mode | Row key | FK columns → table |
|---|---|---|---|---|
| clients | master | lww | gid | – |
| client_values | master | lww | (client_id, column_id) | client_id→clients, column_id→mcl_columns |
| mcl_columns | master | lww | gid | – |
| services | master | lww, natural merge on `name` | gid | userid_column_id, password_column_id→mcl_columns |
| client_services | master | set | (client_id, service_id) | client_id→clients, service_id→services |
| cell_formatting | master | lww | (client_id, column_key) | client_id→clients |
| audit_log | master | append | gid | client_id→clients, service_id→services (nullable; if the referent is unknown after parking, store NULL) |
| staff_users | master | admin_lww, natural merge on `name` | gid | – |
| app_settings | master | lww (**every key**, D7) | key | – |
| client_activity_stats | master | local | – | – |
| client_recent_activity | master | local | – | – |
| tracker_dump | raw | lww | gid | client_id→clients (cross-DB), service_id→services |
| sdc_session_timelines | raw | lww | session_id | client_id→clients |
| client_raw_containers | raw | **verify:** if fully rebuilt from tracker data by `re_resolve_all_tracker_dumps`, mark `local`; else lww on `identity_key` | identity_key | client_id→clients |

**`app_settings`: every key is office-wide (D7).** No per-PC list and no exceptions, including `theme`, `window_mode` and `sync_manual_peers`. New keys added later are office-wide automatically.
- `inv_frames` replicates like any key until P4-1 deletes it. It has no effect once the legacy protocol is off (P3-9).

**Settings screens stay exactly as they are today**: behind the admin-mode PIN, usable on any PC. Changes replicate like client edits.

**Staff roster editing on non-admin PCs** shows: "The staff list is changed from <admin PC name>. To change it here, use Become admin (master password)." If an unsigned staff change arrives anyway, it is rejected and logged.

#### P3-2 gids and sync tables (T2)
- For every non-local table whose row key is `gid`: `ALTER TABLE t ADD COLUMN gid TEXT`, then `CREATE UNIQUE INDEX IF NOT EXISTS ux_t_gid ON t(gid)`. Back-fill `UPDATE t SET gid = lower(hex(randomblob(16))) WHERE gid IS NULL`. Add a trigger:
  ```sql
  CREATE TRIGGER IF NOT EXISTS _gid_ai_<t> AFTER INSERT ON <t> WHEN NEW.gid IS NULL
  BEGIN UPDATE <t> SET gid = lower(hex(randomblob(16))) WHERE rowid = NEW.rowid; END;
  ```
- **Seeded rows get deterministic gids** (F14): `gid = uuid.uuid5(SERA_NS, f"seed:{table}:{natural_key}").hex` for default staff slots, default MCL columns and services. `SERA_NS` is a fixed UUID constant in `sync_schema.py`. Update `_seed_default_data`, `_seed_from_ini` and the staff seeding at `database.py:829-833` to set them.
- **Create in each DB file** (`master.db` and `rawPayload.db` each get their own set):
  ```sql
  _sync_meta(key TEXT PRIMARY KEY, value TEXT)            -- device_id, stream_id, next_seq, last_hlc, mode(off|shadow|live), schema_version
  _sync_flags(name TEXT PRIMARY KEY, value INTEGER)        -- 'applying'
  _sync_pending(id INTEGER PRIMARY KEY, tbl TEXT, rid INTEGER, op TEXT, key_json TEXT)
  _sync_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT, origin_seq INTEGER, hlc TEXT, tbl TEXT,
                row_key TEXT, op TEXT, data TEXT, sig TEXT, UNIQUE(origin, origin_seq))
  _sync_clock(tbl TEXT, row_key TEXT, col TEXT, hlc TEXT, origin TEXT, vhash TEXT, PRIMARY KEY(tbl, row_key, col))
  _sync_tombstones(tbl TEXT, row_key TEXT, hlc TEXT, origin TEXT, PRIMARY KEY(tbl, row_key))
  _sync_vector(origin TEXT PRIMARY KEY, max_seq INTEGER NOT NULL)
  _sync_peer_vectors(device_id TEXT, origin TEXT, max_seq INTEGER, seen_at TEXT, PRIMARY KEY(device_id, origin))
  _sync_parked(id INTEGER PRIMARY KEY, change_json TEXT, reason TEXT, first_at TEXT, tries INTEGER)
  _sync_conflicts(id INTEGER PRIMARY KEY, at TEXT, tbl TEXT, row_key TEXT, col TEXT, kept TEXT, discarded TEXT, reason TEXT)
  _sync_alias(tbl TEXT, alias_gid TEXT PRIMARY KEY, gid TEXT)
  ```
- **Origin stream id** = `f"{device_id}:m"` for `master.db` and `f"{device_id}:r"` for `rawPayload.db`.
- **Accept:** migration runs twice without error; every replicated row has a unique gid; seeded rows have identical gids on two fresh DBs.

#### P3-3 Capture triggers, sealer, HLC (T3)
- **Triggers** are generated from the registry for every non-local table. Pseudocode:
  ```sql
  CREATE TRIGGER IF NOT EXISTS _sync_ai_<t> AFTER INSERT ON <t>
    WHEN (SELECT value FROM _sync_flags WHERE name='applying') IS NOT 1
    BEGIN INSERT INTO _sync_pending(tbl, rid, op, key_json) VALUES ('<t>', NEW.rowid, 'upsert', NULL); END;
  -- AFTER UPDATE: same, op 'upsert'
  -- AFTER DELETE: op 'delete', key_json captured from OLD:
  --   gid tables:       json_array(OLD.gid)
  --   natural key:      json_array(OLD.<key>)
  --   composite FK key: json_array((SELECT gid FROM clients WHERE id = OLD.client_id), (SELECT gid FROM mcl_columns WHERE id = OLD.column_id))
  ```
  - A composite delete whose parent gid is NULL (parent deleted in the same cascade) is **skipped** by the sealer: the parent's tombstone implies it.
  - Use a real table for the flag, not a TEMP table: non-TEMP triggers can't reference temp objects. Don't use a custom SQL function either: DOM_Parser/SDC_Parser connections wouldn't have it and their writes would fail.
- **HLC:** string `f"{ms:013d}.{counter:04d}.{device_id}"`. Compare HLCs as strings. Keep `last_hlc` in `_sync_meta`.
  - `tick()`: `pt = now_ms`. If `pt > l.ms` → `(pt, 0)`, else `(l.ms, l.c + 1)`.
  - `observe(remote)` follows the standard HLC receive rule.
  - **Drift guard:** a remote `ms > now_ms + 3_600_000` → the change is parked with reason `clock_ahead` and the panel shows "PC <name>'s clock is ahead by N minutes". Never advance the local clock from such a change.
- **Sealer** `seal(db_file)`:
  - Runs after every commit that changed rows. Hook it in `SeraDatabase._connect` / `_connect_raw` after `conn.commit()` when `conn.total_changes`, and also every 5 s from a timer, which picks up writes by other processes.
  - Uses its own private connection helper that does **not** call the sealer (no recursion).
  - Runs in one transaction:
    1. Read `_sync_pending` ordered by id, grouped by `(tbl, rid)`.
    2. For upserts, read the current row by rowid; if it's gone, skip. Compute each column's `vhash = sha256(repr-normalised value)` and compare with `_sync_clock`. Changed columns get **one** new HLC per row. On the first seal of a row (no clock rows yet), **all** columns are included.
    3. Translate FK ids to gids (the registry says which columns). Cross-DB FKs (`rawPayload.db` → `clients`) read `master.db` with a separate read connection.
    4. Write `_sync_changes` (`origin` = this stream, `origin_seq = next_seq++`, `hlc`, `data` = JSON of changed columns, with FK columns holding gids). Update `_sync_clock`.
    5. Deletes write a tombstone + a change.
    6. Delete the processed pending rows.
  - Admin-scoped tables: sign the change (`sig`) with the admin key if this PC holds it. Otherwise don't seal it: log it and let the UI have prevented it.
  - Only runs when `_sync_meta.mode` is `shadow` or `live`.
- **Accept:**
  - every public write method of `SeraDatabase` produces the expected changes (parameterised test over a list of methods; `database.py` has many, so the implementer builds the list and the reviewer checks it);
  - an external raw `sqlite3` write is captured by the timer seal;
  - no pending rows are left after `seal`;
  - HLC is monotonic under a stepped-back clock (monkeypatched time).

#### P3-4 Apply engine (T3)
`apply_batch(db_file, changes)`, one transaction:
1. `UPDATE _sync_flags SET value=1 WHERE name='applying'`. It **must** be reset to 0 before commit, including on every error path (`try/finally`, and it rolls back with the transaction anyway).
2. Sort changes by `hlc`. That keeps per-origin order (HLC is monotonic per origin) and puts parents before children.
3. Per change:
   - Skip if `(origin, origin_seq)` is already in `_sync_changes` (idempotent).
   - Admin scope: verify `sig` with `admin_pubkey`. If it's bad, drop and log.
   - Resolve the row: gid → `SELECT rowid FROM t WHERE gid=?` (follow `_sync_alias`); natural/composite keys → lookup after translating parent gids. **A missing parent** → park this change and any later change for the same row. Parked changes are retried after each batch; after 7 days they surface in the panel.
   - **Tombstone:** if one exists for the row, drop the upsert. If the upsert's HLC is newer than the tombstone, write a `_sync_conflicts` row ("edit after delete discarded").
   - **lww / admin_lww:** for each column in `data`, apply only if `(hlc, origin) > (_sync_clock.hlc, _sync_clock.origin)`. Update the clock. If the row doesn't exist → INSERT with all given columns; local id auto-assigned.
   - **append:** INSERT if the gid is absent.
   - **set:** row-level LWW on add/remove.
   - **delete:** delete the local row (FK cascades happen and are not captured because `applying=1`) and write a tombstone.
   - **Natural-key merge** (`services.name`, `staff_users.name`): an insert that collides with an existing row of a different gid → the **lexicographically smaller gid wins** on every PC. If the incoming gid is smaller, rewrite the local row's gid to it. Record `_sync_alias(old → winner)`. Merge columns by LWW. This is deterministic, so all PCs converge.
   - Append the change to the local `_sync_changes` unchanged (same origin, origin_seq, hlc, sig) for forwarding. Advance `_sync_vector[origin]` only while contiguous.
   - `observe(hlc)` on the local clock.
4. Commit. Return the set of touched tables for UI refresh (P3-8).
- **Accept:** P3-0 tests (b), (c), (d), (e), plus unit tests for each bullet above.

#### P3-5 Session protocol and scheduler (T3) — new `sync_engine.py`
- **Session over P2-3:**
  1. Both sides send `HELLO {proto:3, schema_version, device_id, office_tag, vectors:{stream:max_seq}, members_rev, addresses:[...]}`.
     - Schema mismatch → `BYE{reason:"schema", mine, yours}`, and the panel shows "PC <name> needs updating".
     - A peer's vector below our compaction floor for some stream → `NEED_SNAPSHOT` (P4-4).
  2. Exchange membership records if `members_rev` differs (union by rev, verify signatures). Rebuild TLS contexts if anything changed.
  3. Initiator sends `CHANGES{stream, items:[...]}` batches: ≤ 500 changes or ≤ 1 MB, taken from `_sync_changes` where `origin_seq > peer_vector[origin]`, ordered by hlc. After each batch it waits for `ACK{vectors}`. Then the responder does the same. Then `BYE`.
  4. Store the peer's final vectors in `_sync_peer_vectors`, and update `_local_addresses.last_ok_at`.
- **Scheduler:**
  - Every 20 s ± 5 s jitter, sync with each reachable member, one at a time.
  - After a local seal, send a UDP **poke** `{"magic":"sera-sync-v3","office":tag,"dev":id,"poke":true}` to members' last known addresses. A received poke from a member schedules a session with it within 1 s (debounced 1 s).
  - At most one session per peer at a time, and 3 total.
- **Accept:** P3-0 tests (a), (f); a session interrupted mid-transfer resumes correctly next round; the poke path syncs within 3 s in the harness.

#### P3-6 Maintenance and id leaks (T2, review T3)
- **Data-rewriting maintenance runs only on the admin PC** (`sync_admin.is_admin_pc()`):
  - in `run_startup_maintenance` (`database.py:75`): `resequence_client_serial_numbers`, `_clean_ligature_noise_from_names`, `deduplicate_tracker_dumps`, `upgrade_all_placeholder_client_names`, `re_resolve_all_tracker_dumps`;
  - the dedupe/purge block in `_init_raw_schema` (`database.py:580-620`).

  Other PCs receive the results through sync. `sync_fst_reports` and `optimize_storage` stay on all PCs if they only write files/local tables (verify).
- **Tie-breakers:** anywhere "keep newest **id**" / "order by id" decides which row survives, use `(created_at, gid)` instead. `grep -n "ORDER BY id" database.py` and review each.
- **Id leaks (F13):**
  - `client_id_token` (D8): `add_client` (`database.py:1959-1961`) sets `token = f"{letter}-{n}"`. `letter` is this PC's `token_letter` from its member record. `n` = 1 + the highest number already used with that letter in `clients.client_id_token`, computed inside the same write transaction. Legacy mode, before pairing exists, keeps `str(client_id)`. Existing tokens are never rewritten. Search (`database.py:1794`) already uses `LIKE`, so `B-12` is searchable as typed. Check that nothing parses tokens as integers: `grep -n "client_id_token" -r --include=*.py .` and look for `int(`.
  - **Serial number in the ID column** (`database.py:1963-1966` writes `str(client_id)`): use `max(existing numeric serials) + 1` computed in the same transaction. Two PCs adding clients while apart can briefly produce the same serial. The admin PC's `resequence_client_serial_numbers` fixes that. Besides start-up, it also runs on the admin PC after an applied batch that inserted clients, debounced to at most once per 10 minutes. Order by `(created_at, gid)`, not by id.
  - `f"CLI_{client_id}"` in `dataset_key` / identity strings (`database.py:584`, `3713`): use the client's gid instead: `f"CLI_{gid}"`. Recompute `dataset_key`s once on the admin PC.
- **Accept:** the harness shows two nodes produce identical digests after both restart (maintenance doesn't make them diverge).

#### P3-7 Shadow mode (T2, review T3)
- **Mode `shadow`:** capture and sealing on. Sessions exchange changes, but remote changes are applied to `shadow/replica_master.db` / `shadow/replica_raw.db`, not to the live DBs.
  - The replicas start as a snapshot of the live DBs taken when shadow mode is enabled. All PCs must have joined through P2 from the same office snapshot.
  - Local changes are applied to the replica too.
- **Digest** `digest(db)` = SHA-256 over, for each replicated table sorted by row key: `row_key` + the sorted `col=value` pairs.
  - FK values are gids and local ids are excluded.
  - `app_settings` includes every key (D7).
- **Checks** (every 30 min and on demand from the panel), logged to `logs/sync_shadow.log`:
  1. **Capture check (one PC):** baseline snapshot + this PC's own changes replayed into a scratch DB → its digest must equal the live DB's digest.
  2. **Convergence check (all PCs):** after all vectors are equal (HELLO shows it), replica digests must be equal across PCs.
- **Go-live criteria** (owner decides):
  - 7 consecutive days;
  - zero capture-check mismatches;
  - replica digests equal within 2 minutes of quiet;
  - no parked changes older than 1 hour.
- **Accept:** a harness test where a deliberately missed write (trigger dropped) makes the capture check fail.

#### P3-8 UI refresh and panel (T2)
- `apply_batch`'s touched-table set becomes a Qt signal, throttled to one per second.
  - Refresh only the affected windows (reuse the logic of `main.py:1666` `_handle_live_sync_received_main_thread`, split per table).
  - No toast for every batch; update the sidebar indicator only.
- **Panel:** per member last sync time, pending outgoing change count, parked changes, conflicts list (with "keep"/"use discarded value" actions, written as a normal edit), and the shadow check results.

#### P3-9 Go live (T2, owner decision)
- On each PC, at the next start-up, a staged swap (the P0-4 mechanism) installs the **shadow replicas** (which already contain everyone's merged changes) as the live DBs. Set `mode=live`. From then on, remote changes apply to the live DBs.
- Disable legacy 49157 pushes in this release (the P4-1 removal follows).

### Phase 4 — Cleanup

- **P4-1 (T2)** Remove:
  - from `sync_peer.py`: the TCP server on 49157, `push_to`, `push_to_all`, `request_pull_from`, bootstrap quarantine, `inv_frames` (logic, setting, UI toggle, docs), Rev Score (`sync_revision` in beacons/UI), the pre-flight guard in `main.py`, `force_override`;
  - `PeerAuditLogManager` + `peer_logs/` (audit logs replicate now; move the old files to `backups/peer_logs-<ts>/`, don't delete);
  - the tracker/audit telemetry broadcasts.

  Update `tests/test_sera_sync.py` accordingly: delete tests of removed behaviour, keep anything still relevant.
- **P4-2 (T2)** No code path reads/writes `sera.key`/`sera.salt` except the restore importer ("Restore legacy backup": asks for the legacy password, opens with salt+password, and imports through the P2-8 salvage code).
- **P4-3a (T2)** Scheduled backups:
  - `sqlcipher_export` of both DBs daily at first idle after 13:00 into `backups/daily-<date>/`, keeping 14.
  - Also back up right before any restore (P4-3b), migration (P1-4) or go-live swap (P3-9).
  - Retire `master.db.pre-sync-*` rotation.
- **P4-3b (T3)** Restore becomes the office state (D9):
  - **Who:** admin PC only, or any PC after Become admin. The restore change set is signed with the admin key.
  - **Confirm:** before anything happens, show a dry run: "N clients will come back, M clients created since <backup date> will be removed, K fields will revert. All PCs will follow." The user must type `RESTORE` to continue.
  - **Mechanics:**
    1. Back up the current state (P4-3a).
    2. Install the backup through staging + start-up swap (P0-4 mechanism). Keep the current `_sync_*` tables; don't take them from the backup.
    3. **Re-assert:** for every replicated row in the restored DB, seal all its columns as fresh changes with new HLCs, so they beat everything older on every PC.
    4. For every gid that existed before the restore but isn't in the backup, write a delete (tombstone), so other PCs remove clients created after the backup.
    5. Record one `audit_log` entry `action="office_restore"` with the backup name and the counts.
  - **Known limit (write it in the confirmation text):** a PC that was offline during the restore and created **new** clients the admin PC never saw keeps those clients when it reconnects. Its edits to restored rows lose, because they're older than the restore.
  - **Accept:**
    - harness test with 3 nodes: restore on A, and B and C converge to the backup state;
    - an edit on B made after the restore survives;
    - a client created on offline C before the restore survives, and the conflict log shows no silent loss.
- **P4-4 (T3)** Compaction:
  - per stream, `floor = min(max_seq acked by each active member)`, excluding members not seen for 60 days;
  - delete `_sync_changes` with `origin_seq <= floor`; keep tombstones for 180 days;
  - a returning member below the floor gets `NEED_SNAPSHOT`: it first **sends** its own unsent changes (its own log is never compacted below what others have acked), then downloads a snapshot.
  - Harness test (g).
- **P4-5 (T1)** Rewrite `docs/operations-sync.md` for v3. Update `README.md` and `docs/project-structure.md` for the new modules. Delete this blueprint's "What is broken today" section or mark it historical.

---

## 6. Office rollout runbook (for the owner)

1. **Release Phase 0** (2.x) to all PCs. Automatic whole-DB pushing is now gone. Manual push still works, safely staged.
2. **Pick the admin PC:** the one whose database is the most complete. Make a manual copy of `~/AmanAssociates_Sera` to a USB stick.
3. **Release 3.0.** On the admin PC: Admin → Sera Sync → Convert to office key (P1-4). Export the recovery kit to USB and keep it somewhere safe.
4. **On every other PC:** Rejoin office → enter the code shown on the admin PC → review the salvage dry run → import.
5. **Release 3.1:** turn on shadow mode on all PCs. Watch the panel for 7 days (P3-7 criteria).
6. **Release 3.2:** go live (P3-9). Then 3.3: cleanup (P4).
7. **New PCs from now on:** install → Join office → enter the code. Nothing else.

---

## 7. Risks and open items

| Risk / item | Mitigation |
|---|---|
| OpenSSL rejects self-signed CA-flag certs as client certs | P2-3 test on day one. Stop and ask instead of loosening verification. |
| A write path bypasses the triggers (schema rebuild like `_migrate_tracker_dump_nullable` drops and recreates `tracker_dump`, which **drops its triggers**) | Trigger creation runs after every schema init/migration (`CREATE TRIGGER IF NOT EXISTS`). P3-7 capture check detects misses. |
| Windows password reset by an admin makes DPAPI blobs unreadable | Recovery with the master password (P1-6) and the recovery kit. |
| Master password forgotten **and** all DPAPI copies lost | No recovery by design. The runbook requires the recovery kit on USB. |
| Clock far off on one PC | HLC drift guard + panel warning (P3-3). |
| Settings are office-wide and any admin-mode PC can change them (D7) | Accepted by the owner. Two PCs changing the same setting at once: the later edit wins everywhere, and the losing value shows in the conflicts list (P3-8). If theme / window mode turn out annoying as office-wide, make those two keys `local` in the P3-1 registry (one-line change) and note it in §10. |
| Staff notice the new `B-12` token style (D8) | Mention it in the 3.0 release notes. Old tokens don't change. |
| Restore on one PC removes clients created elsewhere after the backup (D9) | Admin-only, dry-run counts, typed confirmation, pre-restore backup (P4-3b). |

---

## 8. Working with AI agents on this plan

### 8.1 Starting a session for one WP
- One WP per chat session. Always start a **fresh** session; don't continue an old one.
- **Close the xlsx in Excel first.** The tool can't save while Excel has it open.
- Open the session in the project folder `C:\Users\Nex\Downloads\Project Sera\APP`. Pick a model the agent table allows for that WP's tier (`docs/sera-sync-v3-agents.xlsx`: the **Work Packages** columns "Recommended Claude" / "Gemini allowed", or the **Models** sheet). The tool refuses a model that isn't allowed.
- Paste this prompt, changing only `<WP-ID>` and `<MODEL NAME>`:

```text
You are <MODEL NAME, exactly as on the Models sheet> implementing work package <WP-ID> of docs/sera-sync-v3-blueprint.md.
1. Read §0 (rules), §2 (decisions), the §3 row for <WP-ID>, and the full §5 entry for <WP-ID>. Also read §9 (progress log) for notes left by earlier WPs.
2. Run: venv\Scripts\python tools\sync_v3_tracker.py set <WP-ID> --status "In progress" --model "<MODEL NAME>"
   If it prints REFUSED (dependencies not Done, or model not allowed for this tier), stop and tell me.
3. Implement only <WP-ID>. Don't start other WPs, and don't change the spec in §1–§7.
4. Write the tests listed under "Accept" first, then the code. Run the full test suite.
5. When finished, add an entry to §9 (template in §8.3), then run:
   venv\Scripts\python tools\sync_v3_tracker.py set <WP-ID> --status "In review" --notes "<one line: what changed, test result>"
6. Show me: files changed, the test results, and any deviation from the spec.
7. Don't commit, push, bump versions or build unless I say so.
```

### 8.2 After a WP is finished
1. Read the agent's summary and the §9 entry.
2. **Review, if needed.** A review is needed if the WP has "Review before merge" in §3, or if the tool printed "needs a Fable 5.1 / Opus 5.5 review" (a Caution model was used). Start a **new** session with Fable 5.1 or Opus 5.5 and paste:
   ```text
   You are <Claude Fable 5.1 | Claude Opus 5.5>. Review the uncommitted changes for work package <WP-ID> against docs/sera-sync-v3-blueprint.md (§0, §2, §5 <WP-ID>). Report spec deviations, correctness and security problems. Don't edit code.
   If you find no blocking problems, run: venv\Scripts\python tools\sync_v3_tracker.py set <WP-ID> --reviewed-by "<your model name>"
   ```
3. Fix what the review finds (same implementing model, new session, paste the findings). Re-review if the fixes were substantial.
4. **Commit.** In any session, paste:
   ```text
   Commit the changes for work package <WP-ID> with message "sync v3: <WP-ID> <title>", then run:
   venv\Scripts\python tools\sync_v3_tracker.py set <WP-ID> --status Done --commit <short hash>
   If it prints REFUSED, tell me why and don't work around it.
   ```
   The tool refuses Done without a commit, or without a review when one was needed.
5. You don't edit the xlsx yourself. To see progress, open it in Excel (close it again before the next session), or ask any agent to run `venv\Scripts\python tools\sync_v3_tracker.py show`.

### 8.3 Keeping the doc in step with the code
- **Two records, two jobs.**
  - §9 holds the *story*: what changed, deviations, notes for later WPs. Agents write it in this markdown file.
  - The xlsx holds the *status*: Status, Model used, Reviewed by, Commit. Only `tools/sync_v3_tracker.py` writes it.

  Neither is copied into the other.
- **Agents only append to §9.** Every §9 entry uses this template:
  ```text
  ### <WP-ID> — <title> — <Done | Partial | Blocked> — <YYYY-MM-DD>
  - Model: <model name>   Commit: <hash or "uncommitted">
  - Tests: <n passed / n failed; names of new tests>
  - Deviations from spec: <none | what and why>
  - Notes for later WPs: <anything the next agent must know, e.g. renamed function>
  ```
- **Spec changes go through the owner.** If an agent finds the spec wrong, it records that under "Deviations" and stops. The owner (or a Fable/Opus session asked to "fold the approved deviations from §9 into §1–§7") updates the spec, bumps the doc version at the top, and adds a line to §10.
- **Line numbers drift.** §1 and §5 cite lines at baseline `4963ab8`. Agents search for the function names instead of trusting line numbers. Nobody needs to keep the line numbers updated.

### 8.4 When a whole phase is finished
1. All WPs of the phase show **Done** in the xlsx, and the review-marked ones have been reviewed.
2. Run the full test suite once more in a fresh session: "Run the full test suite and report failures only."
3. Do a hands-on check on two real PCs, or on one PC plus a spare laptop, using the phase's rows on the xlsx **Phase Checks** sheet.
   - Record each result in Excel yourself, or tell an agent, for example: "run `venv\Scripts\python tools\sync_v3_tracker.py check 3 --result Pass --notes "spare laptop"`".
   - `venv\Scripts\python tools\sync_v3_tracker.py checks` lists the check numbers.
4. Release (you, not the agent; see §0 rule 5 and the release process). Then roll out as described in §6.
5. Only then start the first WP of the next phase.

---

## 9. Progress log

(Agents append entries here using the template in §8.3. Newest at the bottom.)

### P0-1 — Stop the whole-DB broadcast after every edit — Done — 2026-09-23
- Model: Gemini 3.1 Pro   Commit: d1387cc
- Tests: 798 passed / 10 failed; new tests: test_write_does_not_push_database
- Deviations from spec: none
- Notes for later WPs: sync_sent_signal, _handle_sync_sent_main_thread, and sidebar.notify_sync_sent are now unused and can be cleaned up in a later WP.

---

## 10. Doc changelog

- **1.0** (2026-09-23): initial blueprint.
- **1.1** (2026-09-23): owner answers. SUDR ignored (D10). All settings office-wide (D7). Per-PC letter tokens (D8). Restore propagates to all PCs (D9, P4-3 split into P4-3a/b). Gemini allowed on T1/T2. Agent workflow (§8), progress log (§9), agent table xlsx.
- **1.2** (2026-09-23): any PC in admin mode can change settings; `app_settings` is plain `lww`, not admin-signed (D3, D7, P2-2, P3-1 updated). Staff roster stays admin-PC-signed.
- **1.3** (2026-09-23): agents update the xlsx only through `tools/sync_v3_tracker.py` (rule 14; §8.1–§8.4 prompts updated). Two phase checks added for the settings / staff rules.
