# Sera Sync v3 — Blueprint

| | |
|---|---|
| **Status** | Design approved 2026-09-23. Nothing implemented yet. Doc version **1.5** (changelog in §10). |
| **Agent table** | `docs/sera-sync-v3-agents.xlsx`: a read-only viewer showing which model may do which WP, and the status. Status lives in `docs/sera-sync-v3-status.csv` / `-checks.csv`, which agents update only through `tools/sync_v3_tracker.py` (§8). |
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
14. **Record progress only with `tools/sync_v3_tracker.py`.** Never edit the status CSVs, `docs/sera-sync-v3-plan.json` or the xlsx by hand. Commit the status CSVs with each WP. Don't commit `docs/sera-sync-v3-agents.xlsx` unless it was rebuilt with `tools/build_sync_v3_tracker.py`. If the tool says `REFUSED`, don't work around it: report the message to the owner.

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
| P0-9a | 0 | Installer firewall rules (Private + Domain + Public, v1.4) | T1 | – | – |
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

The installer opens 49156–49159 for the app executable on Private, Domain and Public profiles (P0-9a; Public added by owner decision in v1.4).

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
  Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Amas Sera Sync"" dir=in action=allow program=""{app}\Amas_Sera.exe"" enable=yes profile=private,domain,public"; Flags: runhidden
  ```
  (v1.4, owner decision 2026-09-24: `public` added so sync also works on PCs whose network Windows classifies as Public. The P0-9b warning stays.)
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

1. **Release Phase 0** (2.x) to all PCs. Automatic whole-DB pushing is now gone. Manual push still works, safely staged. **From P0-7 onward this release must reach every PC before anyone relies on push/pull again:** P0-7 makes an upgraded PC reject a push or pull request from a PC still on the pre-P0-7 build (it has no way to sign the request, since the auth code doesn't exist there yet — this is the intended effect of closing F9, not a bug). The Sera Sync panel's activity log names the rejected peer and says it may need updating. The built-in auto-updater checks every 2 hours; treat an office as fully upgraded only once the panel shows no more such rejections.
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
| A PC still on the pre-P0-7 build can't push/pull to/from one already upgraded (it can't sign the request) | By design: this is what closes F9. Mitigation is procedural (§6 step 1: ship to every PC together, watch the activity log for "may need updating" until none remain) rather than a protocol fallback — accepting an unsigned request from "maybe just an old peer" would reopen F9 for an attacker who simply omits the mac. Phase 3's session protocol (P3-5) already gives real, safe cross-version handling for the long term: a `schema_version` mismatch in `HELLO` produces "PC \<name\> needs updating" instead of a silent drop, without weakening authentication. |
| Restore on one PC removes clients created elsewhere after the backup (D9) | Admin-only, dry-run counts, typed confirmation, pre-restore backup (P4-3b). |

---

## 8. Working with AI agents on this plan

### 8.1 Starting a session for one WP
- One WP per chat session. Always start a **fresh** session; don't continue an old one.
- The xlsx can stay open in Excel. Agents write the status CSVs, never the workbook. Press **Data → Refresh All** (Ctrl+Alt+F5) to see new status; it also refreshes on open and every minute.
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
5. You don't edit anything yourself. To see progress, refresh the xlsx in Excel, or ask any agent to run `venv\Scripts\python tools\sync_v3_tracker.py show`.

### 8.3 Keeping the doc in step with the code
- **Two records, two jobs.**
  - §9 holds the *story*: what changed, deviations, notes for later WPs. Agents write it in this markdown file.
  - `docs/sera-sync-v3-status.csv` holds the *status*: Status, Model used, Reviewed by, Commit. Only `tools/sync_v3_tracker.py` writes it; the xlsx just displays it.
  - If the §3 table changes (a WP added, split, or its dependencies changed), whoever folds the change in also runs `venv\Scripts\python tools\build_sync_v3_tracker.py` with the xlsx closed. That regenerates `sera-sync-v3-plan.json` and the viewer, and keeps all recorded progress.

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
   - Record each result by telling an agent (typing into Excel doesn't stick: the next refresh overwrites it), for example: "run `venv\Scripts\python tools\sync_v3_tracker.py check 3 --result Pass --notes "spare laptop"`".
   - `venv\Scripts\python tools\sync_v3_tracker.py checks` lists the check numbers.
4. Release (you, not the agent; see §0 rule 5 and the release process). Then roll out as described in §6.
5. Only then start the first WP of the next phase.

---

## 9. Progress log

(Agents append entries here using the template in §8.3. Newest at the bottom.)

### P0-1 — Stop the whole-DB broadcast after every edit — Done — 2026-09-23
- Model: Gemini 3.1 Pro   Commit: df7a850
- Tests: 798 passed / 10 failed; new tests: test_write_does_not_push_database
- Deviations from spec: none
- Notes for later WPs: sync_sent_signal, _handle_sync_sent_main_thread, and sidebar.notify_sync_sent are now unused and can be cleaned up in a later WP.

### P0-2 — Fix get_sync_metrics — Done — 2026-09-23
- Model: Gemini 3.8 Flash   Commit: 302e32c
- Tests: 799 passed / 10 failed; new tests: test_sync_metrics_counts_clients
- Deviations from spec: none
- Notes for later WPs: none

### P0-3 — Consistent snapshot on the sending side — Done — 2026-09-23
- Model: Gemini 3.8 Flash   Commit: c51ac91
- Tests: 804 passed / 10 failed; new tests: test_snapshot_includes_wal_changes, test_push_to_streams_snapshot_and_cleans_up, test_recv_exact_uses_bytearray, test_make_snapshot_raises_if_dest_exists, test_prune_outgoing_snapshots
- Deviations from spec: `push_to` falls back to sending `self.db_path` when `self.db` is `None` (preserves compatibility with existing tests until P4-1); `make_snapshot` accepts either a `SeraDatabase` instance or a `(db_path, hex_key)` tuple.
- Notes for later WPs:
  - Fixed #2: `make_snapshot(db, dest_path)` raises `FileExistsError` if `dest_path` already exists (prevents silent file overwrite during future backup/migration use).
  - Fixed #4: added `prune_outgoing_snapshots` in `sync_peer.py`, called on `SyncPeerService` startup (0s age) and in `push_to` (300s age). P0-4's `apply_pending_swap(app_dir)` can also call `prune_outgoing_snapshots(app_dir / "incoming" / "out", max_age_seconds=0.0)`.
  - For P0-4 (#6): When P0-4 changes the receiver to stage incoming DBs and write `pending_swap.json` instead of writing over open files in place, update receiver assertions in `test_push_to_streams_snapshot_and_cleans_up` accordingly.

### P0-4 — Receiver stages, swaps at start-up — In review (Reviewed by Claude Opus 5.5) — 2026-09-24
- Model: Gemini 3.8 Flash   Commit: 89c9aa3
- Tests: 814 passed / 10 failed; new tests: test_push_is_staged_not_live, test_apply_pending_swap, test_push_with_other_password_rejected, test_apply_pending_swap_backup_failure_preserves_live_db, test_apply_pending_swap_strict_incoming_and_no_wal_deletion, test_apply_pending_swap_db_replace_failure_restores_wal_and_shm, test_push_rejected_on_zero_byte_or_zero_table, test_push_rejected_when_busy, test_push_rejected_when_restart_pending
- Deviations from spec:
  - Addressed review findings (Claude Opus 5.5):
    1. Blocking 1: `apply_pending_swap` rollback strictly tracks `had_live_db`/`swapped_db` and NEVER deletes/unlinks a pre-existing live DB or salt file if pre-sync backup fails (satisfies §0 rule 3).
    2. Blocking 2: `apply_pending_swap` strictly resolves staged files within `incoming/` only, rejects path traversal (`..`, `/`, `\`), and if staged files are missing, fails safely without touching `app_path` or live WAL/SHM sidecars.
    3. Blocking 3: `_handle_incoming_push` validates `db_size > 0` and `salt_size in (16, 32)` up-front, and SQLite verification requires `SELECT count(*) FROM sqlite_master` > 0.
    4. Concurrency: `SyncPeerService` uses a non-blocking `_staging_lock` returning `{"status": "rejected", "reason": "BUSY"}` when another incoming transfer is in progress.
    5. Windows restart locks: `_retry_file_op` wraps all file replacement, backup, and cleanup operations for up to 5 seconds to gracefully handle temporary file locks during process restart race; failed startup swaps are surfaced via `self.shell.show_alert` in `main.py`.
    6. Backup of existing `master.db-wal` and `master.db-shm` added during pre-sync backups (and restored on rollback).
    7. Worth-fixing 1: Rejection with `RESTART_PENDING` before sending `ready` and re-checked inside `_staging_lock` if `pending_swap.json` already exists in `incoming/`, preventing any rejected incoming push from cancelling or corrupting an already-accepted staged swap.
    8. Worth-fixing 2: Removal of existing live `-wal`/`-shm`/`-journal` sidecars moved BEFORE `os.replace` (with retry); fails and aborts before live DB is swapped if sidecars cannot be unlinked.
    9. Worth-fixing 3: `pending_swap.json.failed` is unlinked after displaying the warning alert on startup so it does not repeat indefinitely.
    10. Must-fix (post-review): `apply_pending_swap` rollback restores `live_wal` and `live_shm` from pre-sync backup whenever they existed and were backed up, regardless of `swapped_db`, preventing lost un-checkpointed WAL data if `os.replace` on the live database fails (tested via `test_apply_pending_swap_db_replace_failure_restores_wal_and_shm`).
- Notes for later WPs:
  - Staged pushes write `incoming/pending_swap.json` and stage strictly to `incoming/<db_name>` and `incoming/<salt_name>`.
  - `apply_pending_swap(app_dir)` is called at `main.py` startup before database initialization. It rotates `pre-sync-<ts>` backups (keeping 5), atomically replaces live files with retry, removes SQLite WAL/SHM sidecars, and prunes leftover outgoing snapshots (`incoming/out/`).
  - In-place file replacement (`safe_write_file`) has been deleted.
  - `_on_live_sync_received` in `main.py` routes directly to `_on_sync_received()` requiring an application restart.
  - `test_push_to_streams_snapshot_and_cleans_up` and `tests/test_sera_sync.py` were updated to initialize valid SQLCipher test databases and verify staging + `apply_pending_swap`.

### P0-8 — `rawPayload.db` auto-heal is visible — Reviewed — 2026-09-24
- Model: Claude Haiku 4.5; review fixes by Claude Opus 5.5 (reviewer)   Commit: see git log ("P0-8")
- Tests: 819 passed / 10 failed (the same 10 fail on HEAD without this change: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher); new tests: test_raw_db_reset_is_reported, test_raw_db_reset_without_backup, test_raw_db_reset_backs_up_sidecars, test_raw_db_left_alone_when_backup_impossible
- Deviations / additions:
  - `raw_db_was_reset` is the backup path, or the string `"reset_without_backup"` when the main file was empty (0 bytes: nothing to keep).
  - `-wal`/`-shm`/`-journal` with content are backed up next to the main backup (`<backup>-wal` …), not just deleted.
  - Copy fails → the files are moved to the `.bak` names instead. Both fail → nothing is removed, nothing is reported or audited as reset, and `_init_raw_schema` raises `RuntimeError` naming the file (start-up stops rather than losing data, §0 rule 3).
  - "Skip auto-heal if master.db failed" is effectively guaranteed by `_init_schema` re-raising; the `_master_db_failed` checks are defensive.
  - Toast: `duration <= 0` means the alert stays until dismissed (`ui/components/toast.py`).
  - Start-up alerts (tracker reset, failed sync swap) are combined into one toast so a later one no longer replaces the persistent reset alert.
  - The first part of the `main.py` alert landed in the P0-5 commit `487d6bf`; this WP's commit reworks it.
- Notes for later WPs:
  - P1-5 ("in office mode, never auto-heal") supersedes this path for migrated PCs; legacy-mode PCs keep the behaviour above until they migrate.

### P0-9a — Installer firewall rules (Private + Domain) — In review — 2026-09-24
- Model: Gemini 3.8 Flash   Commit: uncommitted
- Tests: test_installer_firewall_rules passed; full suite: 808 passed / 12 failed (11 pre-existing, 1 WIP P0-5)
- Deviations from spec: Added pre-emptive `delete rule` in `[Run]` prior to `add rule` to prevent duplicate firewall rules on application upgrades; added `RunOnceId: "DelAmasSeraSyncRule"` to `[UninstallRun]` to avoid compiler warnings and preserve idempotency.
- Notes for later WPs:
  - The rule allows inbound traffic for the program executable `{app}\Amas_Sera.exe` on Private and Domain profiles as specified in §5 (whole-program scope rather than restricted to ports 49156–49159 mentioned in §4.3). If port restriction is desired by the owner, §4.3 and the rule can be aligned with specific TCP/UDP port parameters.


### P0-5 — Start-up checks that sera.key opens the DB; prompts otherwise — Done — 2026-09-24
- Model: Gemini 3.8 Flash   Commit: 487d6bf
- Tests: 815 passed / 10 failed; new tests: test_wrong_saved_password_prompts
- Deviations / additions:
  - Error diagnostics & visual alert (Issue #2): `_verify_master_password()` and `_get_master_password()` track failure causes (`MISSING_SALT`, `DATABASE_LOCKED`, `WRONG_PASSWORD`, `CANCELLED`). `main.py` shows a user-facing `QMessageBox.critical` alert before `sys.exit(0)` when start-up cannot unlock an existing database ("wrong password, or the database file is damaged", missing salt, or database locked), eliminating silent application exits.
  - Salt guard on existing database (Issue #3): In `main.py`, salt generation is guarded: if `master.db` exists but `sera.salt` is missing, a new random salt is never generated, avoiding overwriting/mismatching the salt on an existing database.
- Notes for later WPs:
  - `_verify_master_password(password)` verifies using a bare SQLCipher connection without instantiating `SeraDatabase` (`SELECT count(*) FROM sqlite_master`).
  - When `master.db` is missing, `_get_master_password()` returns `""` immediately without touching `sera.key` or creating a database file; first-run creation moves to P0-6.
  - `_prompt_master_password` accepts `prompt_text` parameter and defaults to `"Enter Master Password:"`.
  - For P0-6: move salt generation strictly inside the "New office" branch; if `master.db` exists and `sera.salt` is missing, do not generate a salt and show an error.

### P0-9b — Warn on a "Public" network — In review — 2026-09-24
- Model: Claude Sonnet 5   Commit: uncommitted
- Tests: 833 passed / 11 failed (10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5; 1 flaky in the full-suite run only, `test_join_flow_end_to_end`, passes standalone and alongside the rest of `test_sync_hotfix.py` — looks like cross-test timing noise, not a regression from this WP); new tests: `tests/test_network_category_probe.py` (10 tests: comtypes/PowerShell probe paths, fallback, both-fail case, `NetworkCategoryMonitor`), plus `test_get_network_category_reflects_monitor` and `test_network_monitor_started_and_stopped_with_service` in `tests/test_sera_sync.py`.
- Deviations from spec:
  - New module is `sync_network_probe.py` (not named in §5, but required by rule 7: `sync_peer.py` must not import PySide6, so the probe/monitor had to live outside it). No PySide6 import in it either.
  - `NetworkCategoryMonitor` (background thread, polls at start + every 10 min, matches `SyncPeerService._start_peer_reaper`'s threading pattern) lives inside `SyncPeerService` (`self._network_monitor`, started/stopped with the service, `get_network_category()` accessor) rather than as a standalone thread the panel starts itself, so the probe runs from app start-up regardless of whether the Sera Sync dialog is ever opened, matching "Do this in a background thread at start-up" in the spec.
  - Found (and fixed) a real bug while wiring this up: calling `comtypes.CoUninitialize()` after creating the NetworkListManager COM object crashed the interpreter with an access-violation the moment the local COM object references were garbage-collected post-uninitialize (their `__del__` calls `Release()` into a torn-down apartment). Fixed by never uninitializing on the monitor's dedicated thread — same convention already used in `core/vsdc/vsdc_uia_text.py` for its UIA worker thread (COM apartment lives for the thread's lifetime; the daemon thread dies with the process).
  - `probe_network_category()` fails "quiet" (`is_public: False`) rather than "warn" when both the comtypes and PowerShell probes fail, since a false Public-network alarm was judged worse than a missed one; `error` still carries both failure messages for diagnosis.
- Notes for later WPs:
  - `SyncPeerService.get_network_category()` returns `{"is_public": bool, "categories": [...], "method": "comtypes"|"powershell"|"unknown", "error": str|None}`.
  - The warning banner in `ui/dialogs/sera_sync_dialog.py` (`self.network_warning_banner`) refreshes on the dialog's existing 3s `_refresh_timer`, so it can lag up to 10 minutes behind an actual network change (the monitor's own poll interval) plus up to 3s of dialog refresh — acceptable per spec, no explicit latency requirement given.
  - Real network switch and the real comtypes/PowerShell paths were not exercised on this dev machine beyond confirming they don't crash at import/monkeypatch time — §5 says "Owner checks manually on a real PC," unchanged here.

### P0-9b — review fixes — 2026-09-24
- Model: Claude Sonnet 5 (review by Claude Opus 5.5)   Commit: uncommitted
- Fixed blocking #2 (real bug, confirmed on the reviewer's PC on a Public hotspot): Windows PowerShell 5.1's `Get-NetConnectionProfile | ConvertTo-Json` renders `NetworkCategory` as the bare enum number (`0`/`1`/`2`), not the string name (`"Public"`/…), so `_query_categories_powershell` always returned `"unknown"`. `_POWERSHELL_CATEGORY_NAMES` now maps both the strings and `"0"`/`"1"`/`"2"`; new tests `test_powershell_json_parsing_numeric_category` and `test_powershell_json_parsing_numeric_category_list`. Verified live on this machine (Public hotspot): both the comtypes path and the PowerShell-only path now correctly return `public`.
- Could not reproduce blocking #1 ("the warning never switches on... `enable_network_monitor` defaults to `False`"): no `enable_network_monitor` flag exists anywhere in `sync_peer.py`, `main.py`, or `sync_network_probe.py` (confirmed by `git diff` against origin and a repo-wide grep) — `SyncPeerService.__init__` creates `self._network_monitor` unconditionally and `start()`/`stop()` call it unconditionally. `main.py:301` (`self.sync_service.start()`) therefore does start it. Flagging this back to the owner/reviewer rather than adding a flag that doesn't correspond to anything in the current working tree; possible the review ran against a stale or differently-edited copy in this shared, multi-agent working tree.
- Also applied the non-blocking hardening notes: `CREATE_NO_WINDOW` on the PowerShell subprocess (no console flash), full path to `powershell.exe` under `%SystemRoot%`, both-probes-failed now prints to console (`[Sera Sync] network category probe failed: ...`) instead of only being visible via `error` in the returned dict, `_last_known_public` is now set in `__init__` instead of read via `getattr`.
- Not addressed (logged, not fixed): possible false positives from virtual adapters (VirtualBox/Hyper-V "Unidentified network" often reports as Public) — `is_public` is still "any connected network is Public", per spec's wording ("the active network" isn't well-defined when several are connected). Left as a known limitation for the owner to weigh in on rather than guessing at adapter-filtering heuristics.
- Tests: full suite re-run: 839 passed / 10 failed, 2 skipped (270s) — all 10 failures match the pre-existing list (dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5). `test_sync_hotfix.py::test_join_flow_end_to_end`, which failed once in the earlier full-suite run, passed this time — confirms it was flaky/order-dependent, not a regression from this WP.

### P0-6 — First-run "New office / Join office" + legacy join with on-screen approval — Done — 2026-09-24
- Model: Gemini 3.8 Flash   Commit: 874a685
- Tests: 844 passed / 10 failed (10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5); new tests (33 total in test_sync_hotfix.py): original 7 P0-6 tests + 5 blocking-fix tests: test_create_new_office_refuses_if_db_exists, test_create_new_office_backs_up_existing_salt_and_key, test_complete_join_office_backs_up_and_rolls_back_on_failure, test_create_new_office_password_strip_consistent, test_complete_join_office_password_strip_consistent.
- Deviations from spec:
  - §5 P0-6 step 5 ("ignore fetch_snapshot while the modal is open"): implemented as a lock-and-flag guard (_join_lock / _join_in_progress) rather than filtering TCP actions at the server loop level. Functionally equivalent: concurrent requests get BUSY rejection. 
  - actor_alias passed to FirstRunDialog is always "Admin" at first-run dispatch time (actor_alias resolved later in __init__). Joiner sends OS hostname as the workstation name via socket.gethostname(), which is sufficient for approval dialog context.
- Blocking fixes applied (post-initial-review):
  1. Backup-before-overwrite: create_new_office now refuses if master.db exists; backs up existing sera.salt / sera.key to .bak-<ts> before overwriting. complete_join_office backs up all three target files before any install step.
  2. Rollback on partial install: complete_join_office tracks which files were installed; on exception after any step, restores backed-up originals and removes any partially-installed file. Pattern mirrors the existing apply_pending_swap flow.
  3. Password strip consistency: FirstRunDialog._handle_create_new_office now stores pwd.strip() (matching what create_new_office writes to sera.key). _handle_verify_and_install strips before both passing to complete_join_office and storing in self.master_password.
- Notes for later WPs:
  - When `master.db` does not exist in `APP_DIR`, `FirstRunDialog` provides mode selection ("New Office" and "Join Office").
  - New office validation enforces >= 8 characters, password confirmation match, and refuses "admin123"; creates salt, initializes DB, and writes `sera.key`.
  - Joining an office uses `discover_lan_peers` for 10s beacon listening, sends `fetch_snapshot` outbound with a 6-digit random code, waits for serving PC on-screen approval (up to 120s auto-reject), stages snapshot and salt to `incoming/`, verifies office password via cipher integrity and table count before installing, and writes `sera.key` without requiring app restart.
  - Serving PC shows `JoinApprovalDialog` via `on_join_approval_requested` callback, enforcing at most one pending join request and rejecting concurrent requests with `BUSY`.

### P0-7 — Authenticate legacy sync messages (HMAC) — In review — 2026-09-24
- Model: Claude Sonnet 5   Commit: uncommitted
- Tests: 848 passed / 10 failed / 2 skipped (10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5 — same as HEAD before this WP); new tests in `tests/test_sync_hotfix.py`: `test_authenticated_push_accepted`, `test_unauthenticated_push_rejected` (missing mac, and a mac forged with the wrong key), `test_unauthenticated_pull_rejected` (also asserts no reverse push is triggered), `test_stale_timestamp_rejected`.
- Deviations from spec:
  - `hex_key` on `SyncPeerService.__init__` is optional (`None` by default). When an instance has no `hex_key` configured, it neither signs its own outgoing headers nor enforces the mac check on incoming ones — this is what lets ~30 pre-existing tests across `tests/test_sync_hotfix.py` / `tests/test_sera_sync.py` (P0-1, P0-3, P0-4, P0-6, P0-9b, etc.) keep constructing `SyncPeerService` without a key and still pass, unchanged. `main.py` is the only production call site and it always passes `hex_key` (the same value already derived at start-up to open `master.db`), so real running instances always enforce authentication; only a test/legacy construction can opt out. Flagging this for the T3 reviewer: please confirm this is an acceptable scope boundary rather than a hole, since the spec's wording ("every outgoing header") assumed the key is always present.
  - `canonical_json`, `SERA_SYNC_AUTH_INFO`, `AUTH_TS_TOLERANCE_SEC` (120s, per spec) and `AUTH_EXEMPT_ACTIONS` (`{"fetch_snapshot"}`) are module-level in `sync_peer.py`.
  - Only the request/action headers that peers send to initiate something (`push_database`, `request_database_pull`, `push_audit_log`, `push_tracker_dump`) are authenticated. The short status replies (`{"status": "ok"|"rejected"|"ready"}`) sent back over the same already-open connection are not separately signed — the spec's Accept tests only exercise request-side rejection, and the reply is bound to a connection the requester itself opened.
  - Fixed in passing: `sync_peer.py` used `Optional[Any]` in `SyncPeerService.__init__`'s signature without importing `Any` from `typing`. This was silently broken (Python 3.14 defers annotation evaluation, so `import sync_peer` never triggered it) but would raise `NameError` on any `inspect.signature()`/`get_type_hints()` call. Added `Any` to the `typing` import.
- Notes for later WPs:
  - `auth_key = HMAC-SHA256(hex_key_bytes, SERA_SYNC_AUTH_INFO)`; `mac = HMAC-SHA256(auth_key, canonical_json(header_without_mac))`. `SyncPeerService._sign_header(header)` / `._verify_header(header)` are the single choke points — any new outgoing action header should go through `_sign_header` before `_send_framed`.
  - `main.py` passes `hex_key=hex_key` (the value already derived from the master password + salt right before `SeraDatabase(...)` is opened) into `SyncPeerService(...)`.
  - P1-2/P1-5 (office-mode key resolution, key-fingerprint gate) will replace this password-derived `hex_key` with the office DEK's hex form; `_sign_header`/`_verify_header` don't need to change, only what's passed as `hex_key` at construction.

### P0-7 — rollout-compatibility follow-up — 2026-09-24
- Model: Claude Sonnet 5   Commit: uncommitted
- Reported: during a staggered rollout (not every PC upgraded at once), a PC still on a pre-P0-7 build gets a bare `{"status":"rejected","reason":"UNAUTHENTICATED"}` when it pushes/pulls to/from an already-upgraded PC, with nothing in the message pointing at "this peer needs updating." Confirmed this is inherent, not a bug: an old build has no code to compute a mac, so it can never authenticate, and *accepting* a request just because it "looks like" it might be from an old build would let an attacker claim the same and reopen F9 — so the fix is not to loosen `_verify_header`.
- What changed instead (no weakening of authentication; `_verify_header`'s `reason` values and the four P0-7 acceptance tests are unchanged):
  - `_handle_incoming_push`: when a rejection is `UNAUTHENTICATED` **and** the header has no `mac` at all (as opposed to a `mac` that's present but wrong), that's the specific signature of a pre-P0-7 sender. The activity-log detail and console print now say so ("`<host>` may still be on a build from before this release and cannot authenticate — upgrade it to the current version."), and the rejection reply carries an additional, non-authoritative `hint` field alongside the unchanged `reason`.
  - `push_to`: if the peer's rejection carries a `hint`, it's folded into the activity-log entry and the returned message, so the *pushing* PC's own operator sees the same explanation.
  - §6 (rollout runbook) step 1 and §7 (risks) now say explicitly that this release must reach every PC before push/pull is relied on again, and why a version-agnostic fallback isn't the fix (Phase 3's HELLO/schema_version check in P3-5 is the real, safe answer for the long term).
- Tests: full suite re-run after this change: 848 passed / 10 failed / 2 skipped, same 10 pre-existing failures as before. `test_unauthenticated_push_rejected` / `test_unauthenticated_pull_rejected` still pass unmodified (they only assert on `reason`, not `hint`).
- Notes for later WPs: `hint` is advisory text for logs/toasts only — never branch protocol logic on its presence, only on `reason` (P3-5's own version-mismatch handling is unrelated code, not an extension of this field).

### P0-10 — Discovery that reaches Wi-Fi and other subnets — Done (Reviewed by Claude Opus 5.5) — 2026-09-24
- Model: Gemini 3.8 Flash   Commit: 66feefe
- Tests: 859 passed / 10 failed / 2 skipped (10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5); new tests: test_peer_ip_change_updates_entry, test_manual_peer_unicast_beacon, test_directed_broadcast_addresses_skips_loopback_and_link_local, test_sync_manual_peers_setting_and_skips_own_address, test_sera_sync_dialog_add_pc_by_ip_validates_and_stores, test_manual_beacon_bad_port_isolation, test_malformed_beacon_does_not_crash_listener, test_unicast_beacon_reply_rate_limit, test_sync_peer_service_remove_manual_peer, test_sera_sync_dialog_remove_pc_by_ip, test_sera_sync_dialog_table_context_menu_remove
- Deviations from spec:
  - `MANUAL_PEER_INTERVAL_SEC` set to 10s (instead of 30s) to prevent manual peers from flapping/timing out against `PEER_TIMEOUT_SEC = 30s` (addresses review finding #1).
  - Dialog input and `_parse_peer_address` accept an optional `:port` (1–65535) alongside IP addresses to support testing and non-standard port environments.
  - Added per-IP rate-limiting (5s cooldown) and port validation (1-65535) on unicast beacon replies to prevent amplification/reflection abuse.
- Review fixes applied:
  1. Blocking 1 (peer flapping): Shortened manual beacon poll interval to 10s so peers are refreshed well ahead of the 30s reaper timeout.
  2. Blocking 2 (port error isolation): Validated port range (1–65535) in both dialog and `_parse_peer_address`; wrapped send loop in per-peer exception handling so invalid ports/addresses never abort sending to subsequent peers.
  3. Non-blocking (listener crash): Protected UDP listener loop and numeric conversions against malformed beacon payloads.
  4. Non-blocking (unicast reflection): Added rate limit (5s per IP) and port validation on reply path.
  5. Non-blocking (performance): Cached `_get_own_ips()` for 15s to eliminate redundant DNS/adapter queries on every peer send cycle.
- Notes for later WPs:
  - `PeerInfo.key()` now returns `self.host` (host name), so peer IP updates modify the existing entry without creating duplicate/ghost records. Cloned Windows images with identical hostnames will collapse into one row switching between IPs (note for P0-11 and P2-5).
  - Beacon sender broadcasts to `255.255.255.255` and directed broadcasts derived lazily via `ifaddr` (skipping loopback and link-local).
  - Manual peer discovery sends unicast beacons every 10s to addresses configured in office-wide setting `sync_manual_peers` (skipping own address), and recipients answer with a unicast beacon reply.
  - SeraSyncDialog includes "Add PC by IP" button with Google Material icon (`mdi.plus-network`) which validates IPv4 addresses and stores them in `sync_manual_peers`.
  - Added pure-Python `ifaddr>=0.2.0` dependency to `requirements.txt`.
- Addition beyond the spec (reviewed by Claude Opus 5.5, 2026-09-24): **removing manual addresses.** A "Remove PC by IP" button and a right-click "Remove … from Manual Peers" on peer rows. `SyncPeerService.remove_manual_peer` removes only the exact entry (removing `ip:port` leaves a separate plain `ip` entry, and vice versa), drops peers with that IP from the table and notifies the panel. Tests: `test_sync_peer_service_remove_manual_peer`, `test_sera_sync_dialog_remove_pc_by_ip`, `test_sera_sync_dialog_table_context_menu_remove`.
  - **For P0-11 (operations doc):** in Phase 0 `sync_manual_peers` only reaches other PCs through a whole-DB push, so removing an address affects this PC only; other PCs keep contacting it, and a later push from a PC that still has it puts it back. A removed PC can also stay visible if it's found by broadcast or still lists this PC (this PC still answers its beacons).

### P0-11 — Docs: operations-sync.md update — Done — 2026-09-24
- Model: Gemini 3.7 Flash   Commit: df695f8
- Tests: full suite: 859 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5)
- Deviations from spec: none
- Notes for later WPs:
  - `docs/operations-sync.md` is updated to describe all Phase 0 architectural changes: multi-adapter broadcast, hostname-keyed peers, "Add PC by IP" manual discovery (`sync_manual_peers`), Public-network security warnings and firewall scope, the first-run Join flow with 6-digit verification code and on-screen approval, WAL snapshotting with `sqlcipher_export`, startup database staging/swapping (`apply_pending_swap`), HMAC authentication, prohibition of Syncthing/external file synchronizers on live active databases (with legacy conflict file recovery preserved in Admin Restore), and explicit notation that `inv_frames` whole-DB authority is transitional and stays only until Sera Sync v3.
  - Completes Phase 0 work packages (P0-1 through P0-11).

### P1-1 — `sera_keys.py`: DPAPI, office.json, key id, recovery blob — Done — 2026-09-24
- Model: Claude Opus 5.5   Commit: uncommitted
- Tests: full suite 883 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5). New `tests/test_sera_keys.py`, 24 tests: all 8 Accept items (DPAPI round trip, wrong entropy, password wrap round trip, `WrongPassword`, tampered ciphertext, wrong AAD, `key_id` 32 hex, `atomic_write` no `.tmp`) plus tampered DPAPI blob, tampered KDF params, malformed blob, fresh salt/nonce, office.json round trip/format/backup, store/load/recover DEK, key-id mismatch on recover, `atomic_write` failure path, no PySide6 import.
- Deviations from spec (all additions; the listed signatures are unchanged):
  - **Key files are never overwritten without a backup.** `store_dek`, `save_office` and `recover_dek` copy an existing file that differs to `<name>.bak-<YYYYmmdd_HHMMSS>` before `atomic_write` (§0 rule 3). Identical content is not rewritten.
  - Extra exception `KeyFileInvalid` (subclass of `SeraKeysError` and `ValueError`) for malformed/unknown-format `office.json` or recovery data, and for a recovered DEK whose `key_id` doesn't match `office.json`. `WrongPassword` and `KeyUnavailable` are as specified; all three derive from `SeraKeysError`.
  - `unwrap_with_password` validates the blob before running Argon2: `format`/`kdf`, `t` 1–16, `m_kib` 8 MiB–1 GiB, `p` 1–16, salt 16 B, nonce 12 B. A tampered blob can't make a PC allocate gigabytes. Tampering with in-range params still fails at the GCM tag (they change the KEK).
  - `recover_dek` checks the unwrapped DEK's `key_id` against `office.json` before re-storing `.dpapi`.
  - `store_dek` computes both the recovery blob and the DPAPI blob before writing either, so a DPAPI failure writes nothing.
  - `atomic_write` uses a unique temp name (`mkstemp` in the same folder), retries `os.replace` for up to 2 s on `PermissionError` (Windows AV/indexer locks), and removes the temp file on any failure.
  - DPAPI: `ctypes.WinDLL(..., use_last_error=True)` with explicit `argtypes`/`restype` (the sketch's bare `windll` calls truncate pointers on 64-bit); decrypted output buffer is zeroed before `LocalFree`. On non-Windows, `dpapi_*` raise `OSError`.
- Notes for later WPs:
  - Public helpers besides the §5 list: `ENTROPY_OFFICE_KEY` / `ENTROPY_DEVICE_KEY` / `ENTROPY_ADMIN_KEY`, `recovery_aad(office_id)`, `admin_aad(office_id)` (P2-2), `keys_dir(app_dir)`, `OfficeInfo.new_office_id()` (uuid4). File-name constants: `OFFICE_FILE`, `DEK_DPAPI_FILE`, `DEK_RECOVERY_FILE`.
  - `OfficeInfo` is a dataclass: `office_id, office_name, key_id, admin_pubkey=None, device_id=None, created_at=<now UTC iso>, format=1`. `load_office` returns `None` only when the file is missing, and raises `KeyFileInvalid` if it's damaged. P1-2 should treat that as "stop and show the error", not as legacy mode.
  - `load_dek` raises `KeyUnavailable` for a missing file, an unreadable file, or DPAPI failure. It doesn't check `key_id`; P1-2 does that against `office.json`, as specified.
  - `argon2` and `cryptography` are imported lazily inside the functions, so `import sera_keys` stays cheap for P1-2's start-up path. One unlock (Argon2id t=3, 64 MiB, p=4) takes about 0.1–0.3 s.
  - **For P1-6 (T3 review):** because of the backup rule, a master-password change leaves the old `office_key.recovery` as `office_key.recovery.bak-<ts>`, and the old password can still unwrap it. The DEK doesn't change, so a leaked old password keeps working until that backup is removed. The owner should decide whether P1-6 deletes that specific backup after a successful re-wrap (an exception to rule 3) or accepts it.
    - **Owner decision (2026-09-24): risk accepted.** P1-6 keeps the old `office_key.recovery.bak-<ts>` like any other key-file backup; don't delete it. (Not yet folded into §5/§7; the owner or a Fable/Opus "fold approved deviations" session does that.)

### P1-3 — Parsers use `sera_keys` — Done — 2026-09-24
- Model: Claude Sonnet 5   Commit: uncommitted
- Tests: full suite 891 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5). New `tests/test_parser_sera_keys.py`, 2 tests (Windows-only, DPAPI): `test_dom_parser_get_db_hex_key_uses_office_dek`, `test_sdc_parser_get_db_hex_key_uses_office_dek` — each builds an office key with `store_dek`/`save_office` under a temp `AmanAssociates_Sera` dir and asserts the parser's `get_db_hex_key()` returns `sera_keys.dek_hex(dek)`.
- Deviations from spec: none. `get_db_hex_key` in both files now tries `sera_keys.load_office(key_dir)` + `sera_keys.load_dek(key_dir)` → `sera_keys.dek_hex(dek)` first, over two candidate dirs in order: the real data dir (`~/AmanAssociates_Sera`, where `main.py`'s `APP_DIR` and `keys/office.json` actually live) and the code dir (`.../DOM_Parser_1/..`), then falls back to the existing legacy sera.key/salt search unchanged. Any exception (import failure when run standalone, `KeyUnavailable`, `KeyFileInvalid`, no `keys/office.json`) falls through silently to the legacy path, same as today's `except Exception: pass` style already used in these functions.
- Notes for later WPs: none.

### P1-2 — Start-up key resolution (office mode / legacy mode) — In review — 2026-09-24
- Model: Gemini 3.8 Flash   Commit: uncommitted
- Tests: 889 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5); new tests in `tests/test_startup_key_resolution.py`: `test_startup_office_mode_ignores_sera_key` (with file-open interception strictly verifying `sera.key` is never accessed), `test_startup_office_mode_without_sera_key`, `test_startup_legacy_mode_unchanged`, `test_startup_office_mode_with_key_unavailable_recovers_dek`, `test_startup_office_mode_key_id_mismatch_aborts`, `test_startup_office_mode_corrupt_office_json_aborts`.
- Deviations from spec:
  - Temporary recovery prompt: P1-2 implements `_recover_office_dek()` using `QInputDialog` with the specified recovery text and 5 attempts calling `sera_keys.recover_dek()`. P1-6 will replace this with the full recovery dialog including the "Restore from recovery kit file" option.
- Notes for later WPs:
  - `main.py` introduces `_resolve_encryption_key()` called at startup right after `apply_pending_swap()`.
  - In office mode (`keys/office.json` exists), loads the DEK via `sera_keys.load_dek(APP_DIR)` and validates that `sera_keys.key_id(dek)` matches `office.json`. Never reads or writes `sera.key`. If DPAPI decryption fails (`KeyUnavailable`), `_recover_office_dek()` prompts for the master password up to 5 times and restores `keys/office_key.dpapi` via `sera_keys.recover_dek()`.
  - In legacy mode (`keys/office.json` does not exist), continues today's path with P0-5/P0-6 (`FirstRunDialog` if no DB, else `_get_master_password()` with PBKDF2 derivation).
  - Exposes `self.key_mode` (`"office"` or `"legacy"`) and `self.key_id` (`str` in office mode, `None` in legacy mode) on `SeraApp`, and passes both `hex_key` and `key_id` into `SyncPeerService`.
  - `SyncPeerService.__init__` accepts `key_id: Optional[str] = None` and stores `self.key_id = key_id`.
  - **For P1-5 (Sync in office mode):** Sync still reads `sera.key` in office mode. While P1-5 adds `key_id` to beacons and headers to prevent exchange between legacy and office-mode PCs, P0-4's staged receiver verification (`_handle_incoming_push` in `sync_peer.py`) still derives and checks incoming DBs using local `sera.key` password + incoming salt. P1-5 must update this verification path so that office-mode peers verify incoming snapshots against the office DEK/`hex_key` instead of reading `sera.key`.
  - **For P1-6 (Missing DB in office mode):** Missing `master.db` in office mode quietly creates an empty database if `SeraDatabase` is opened without checking DB existence. P1-6 should check whether `master.db` exists in office mode; if missing, it should prevent silent empty DB initialization and instead prompt for recovery / restore from backup or recovery kit.
  - **For P1-6 (DPAPI rewrite failure on recovery):** If `keys/office_key.dpapi` cannot be rewritten during recovery (e.g. DPAPI failure or permissions error), `recover_dek` raises an exception and start-up currently aborts even though the DEK was successfully unwrapped from the recovery password. P1-6 should handle DPAPI re-protection failure gracefully (e.g. keep decrypted DEK in memory for the current session with a warning to the user, rather than aborting start-up).

### P1-4 — Migrate a PC to an office key — In review — 2026-09-24
- Model: Claude Opus 5.5   Commit: uncommitted
- Tests: full suite 912 passed / 10 failed / 2 skipped (the same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5). New `tests/test_office_key_migration.py`, 21 tests: both Accept tests (`test_migrate_to_office_key`, `test_migrate_rollback_on_verify_failure`), plus keep-current-password, input validation (admin123 / short / empty office name), wrong legacy password changes nothing, row-count mismatch stops and rolls back, step-6 failure restores from backup, a stray pre-existing key file survives rollback, crash after office.json is rolled forward at next start, crash before office.json is rolled back at next start, request-file round trip, refuses when already in office mode, `check_legacy_password`, no PySide6 import, main.py wiring (requested migration → office mode; cancel → stays legacy and the request is consumed), and UI smoke tests (button only in legacy mode, button writes the request and restarts, cancel writes nothing, dialog validation).
- Files: new `sync_migrate.py`, new `ui/dialogs/office_key_migration_dialog.py`; `main.py` (`_run_pending_office_key_migration`, `_ask_office_key_migration_details`, `_show_office_key_migration_message`, called right after `apply_pending_swap` and before `_resolve_encryption_key`); `ui/dialogs/sera_sync_dialog.py` ("Convert to office key (admin PC only)" button).
- Deviations from spec / additions (flagging them for the T3 review):
  - **Crash recovery (addition).** Before step 5, a marker `incoming/migrate_state.json` is written (key_id, backup folder, DB names, which key files already existed). At every start-up `resume_interrupted_migration` runs first: if `office.json` exists with the marker's key_id, it **rolls forward** (finishes step 6, which is idempotent). The DEK is stored and the new files were verified before `office.json` was written. If `office.json` doesn't exist, it **rolls back** (the legacy files were never touched). Without this, a power cut between step 5 and the end of step 6 would leave `office.json` pointing at legacy-keyed DBs, so the next start would fail with "Database Error".
  - **`office.json` is written last in step 5** (after `store_dek`), because its existence is what switches the PC to office mode.
  - **Step-6 rollback also removes the key files from this run.** The spec only says "restores from the backup folder". If `office.json` stayed, the next start would try the DEK on the restored legacy DBs. The key files are removed only after the restored files are checked (sha256 equals the backup, and master.db opens with the legacy key). If the restore can't be checked, the keys are **kept** (they may be the only way to open what's live), the marker is set to `rollback_failed`, and start-up stops with a message naming the backup folder (`MigrationRollbackFailed`).
  - **§0 rule 3:** key files that were already in `keys/` before the run (a stray `office_key.dpapi`/`.recovery` without `office.json`) are moved to `<backup>/keys-preexisting/` and put back on rollback, not deleted. Only files created by this run are deleted, as the spec says. Live `-wal`/`-shm`/`-journal` files are moved to `<backup>/replaced-sidecars/` before the swap, not deleted. `sera.key`/`sera.salt` are moved with `os.replace` onto their already-verified copies in the backup folder. A leftover `incoming/migrate/` from an earlier crash with no marker is renamed to `migrate.stale-<ts>`.
  - **Verification (step 4)** also compares the set of table names and `user_version`. A row-count difference raises `RowCountMismatch`, which rolls back and shows a critical "report this to the owner" message. Nothing works around it (the "stop and ask" rule).
  - **Step 1:** the dialog checks the current password against master.db (5 tries). `migrate_to_office_key` checks it again and also requires `rawPayload.db`, if it exists, to open with the legacy key; otherwise it aborts rather than migrating half the data. If `rawPayload.db` doesn't exist, only master.db is migrated. A new password must be ≥ 8 characters and not `admin123`, the same rules as P0-6, and is stripped like P0-6. "Keep current password" is refused if the current one is `admin123`.
  - The checkpoint in step 2 folds any WAL content into master.db before the backup. After a rollback the legacy files are logically unchanged, but they are byte-identical only if the WAL was already empty, which is the normal case after a clean exit.
  - The request file is consumed before the dialog opens, so cancelling or a failure never makes it reappear on every start. Cancelling means the PC stays in legacy mode.
  - If `resume_interrupted_migration` can't decide what to do (`rollback_failed`, or `office.json` with a different key_id), start-up shows the error and exits instead of guessing.
- Notes for later WPs:
  - **P2-2:** §4.2/P2-2 say the admin key is "created ... during P1-4 migration". P2-2 doesn't exist yet, so P1-4 doesn't create it and `office.json.admin_pubkey` stays `null` on migrated PCs. P2-2 must create the admin key for an office whose `admin_pubkey` is `null` (the admin PC after P1-4). It needs the master password to write `admin_key.recovery`.
  - **P1-6:** the success message doesn't offer "Export recovery kit" yet (P1-6 adds it). §6 step 3 expects it right after the conversion.
  - **P1-5:** until P1-5, a converted PC still runs the legacy sync, whose staged-push verification reads `sera.key` (see the P1-2 notes). That file is now in the backup folder, so legacy push/pull to/from the converted PC fails. This is expected: legacy and office PCs must not exchange DBs.
  - Public API: `write_migrate_request/read_migrate_request/clear_migrate_request`, `check_legacy_password`, `validate_new_password`, `migrate_to_office_key(app_dir, legacy_password, office_name, new_password=None) -> MigrationResult(office_id, key_id, backup_dir)`, `resume_interrupted_migration(app_dir) -> None|"completed"|"rolled_back"`, and the exceptions `MigrationError` ⊃ `RowCountMismatch`, `MigrationRollbackFailed`.
  - Not exercised: a real click-through (button → restart → dialog → office-mode start) on the owner's PC. Only the automated tests above were run.

### P1-4 — review fixes — 2026-09-24
- Model: Claude Opus 5.5   Commit: uncommitted
- Fixed all three review findings:
  1. **Roll-forward onto legacy databases.** `_finish_swap` took a missing `incoming/migrate/<name>` as "already replaced". A crash inside `_rollback_prepare` (after the new files were deleted, before the keys) left `office.json` + marker, and the next start then moved `sera.key`/`sera.salt` away with legacy-keyed DBs still live. Now:
     - `_rollback_prepare` removes the key files **before** `incoming/migrate/`;
     - `_finish_swap` checks that every live DB opens with the office key (DEK passed in the normal run; loaded from `keys/` and matched to the marker's key_id on resume) **before** moving `sera.key`/`sera.salt` or deleting the marker. If one doesn't open, it raises `MigrationError`, leaves everything and keeps the marker; start-up shows the error and exits.
  2. **Stale `incoming/migrate/` deleted on a step-2 failure.** It is now renamed to `migrate.stale-<ts>` before the backup folder is created, so no rollback can remove it.
  3. **Locked DB counted as a wrong password.** `check_legacy_password` returns False only for `WrongPassword` and raises `MigrationError` otherwise; the dialog shows "Can't check the password right now: …" without using up an attempt.
- Tests (this round): 5 new in `tests/test_office_key_migration.py` (`test_rollforward_refuses_when_new_files_are_missing`, `test_rollback_removes_keys_before_new_files`, `test_step2_failure_keeps_stale_migrate_folder`, `test_check_legacy_password_raises_when_it_cannot_check`, `test_migration_dialog_locked_db_does_not_use_an_attempt`); file total 26. Full suite: 932 passed / 10 failed / 2 skipped (same 10 pre-existing).
- Deviations from spec: none beyond the P1-4 entry above.

### P1-4 — review fixes, round 2 — 2026-09-24
- Model: Claude Opus 5.5   Commit: uncommitted
- Fixed both leftovers from the second review:
  1. **The live-DB check passed on a missing file.** Connecting to a missing path creates a 0-byte file, and an empty file opens with any key. `_check_live_dbs_open` now raises `MigrationError` for a missing or empty file before connecting, so no empty DB is created.
  2. **A crash during a step-6 rollback could be rolled forward.** Office.json still existed, so the next start swapped the office-key copy back in, which left the two DBs on different keys and needed a manual restore. `_rollback_swap` now saves the marker with `phase: "rolling_back"` before restoring anything, and `resume_interrupted_migration` finishes that phase as a rollback (files already restored match their backup and are skipped; keys are removed only after every file matches its backup).
- Tests: 2 new (`test_rollforward_refuses_when_a_live_db_is_missing`, `test_crash_during_swap_rollback_is_finished_as_rollback`); file total 28. Full suite: 936 passed / 10 failed / 2 skipped (same 10 pre-existing).
- Deviations from spec: none beyond the P1-4 entry above.

### P1-5 — Key-fingerprint gate everywhere — Done (Reviewed by Claude Opus 5.5) — 2026-09-24
- Model: Gemini 3.8 Flash   Commit: 1960b19
- Tests: 937 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5); 8 tests in `tests/test_key_fingerprint.py`: `test_key_id_mismatch_rejected`, `test_auto_heal_disabled_in_office_mode`, `test_key_id_in_beacons_and_headers`, `test_bootstrap_autopull_skips_mismatched_key_id`, `test_dialog_shows_different_office_key`, `test_office_refuses_legacy_fetch_snapshot`, `test_office_push_sends_zero_salt_and_swap_does_not_install_salt`, `test_apply_pending_swap_preserves_stray_salt_as_stale_backup`.
- Deviations / additions from spec:
  - **Zero-salt in office mode**: in office mode (`self.key_id` set), `push_to` sends `salt_size = 0` and sends no salt bytes; `_handle_incoming_push` verifies `salt_size == 0` and writes `pending_swap.json` with no salt key. `apply_pending_swap` was updated so that when `salt` is absent, it swaps only `master.db` and leaves salt untouched (never creates or installs a fake `sera.salt`). Per §0 rule 3, any stray staged salt files in `incoming/` are preserved by renaming to timestamped `.stale-<ts>` copies rather than being unlinked.
  - **Office mode rejects `fetch_snapshot`**: an office-mode PC (`self.key_id is not None`) immediately rejects legacy join `fetch_snapshot` requests with `{"status": "rejected", "reason": "KEY_ID_MISMATCH", "hint": "different office key — rejoin needed"}` rather than falling through to on-screen approval.
  - **`SeraDatabase` auto-mode fallback**: `SeraDatabase.__init__` accepts `key_mode` or auto-detects via `keys/office.json`. If `office.json` is damaged or unreadable, it defaults to `"office"` mode as the safe choice.
  - **Key check before signature check**: `_handle_incoming_push` verifies `key_id` match prior to `_verify_header` so callers receive the explicit `KEY_ID_MISMATCH` reason rather than generic `UNAUTHENTICATED`.
  - **"Sync All" filtering**: `SeraSyncDialog._on_sync_all_clicked` filters peers to only those sharing the local `key_id`, blocking accidental bulk overwrites to legacy or foreign office PCs.
- Notes for later WPs:
  - Legacy UDP beacons and signed push/pull message headers include `key_id: self.key_id` when running in office mode.
  - Push and pull requests are rejected with `{"status": "rejected", "reason": "KEY_ID_MISMATCH", "hint": "different office key — rejoin needed"}` if the sender and receiver key_id do not match (including legacy vs office, office vs legacy, or different office keys).
  - In office mode, `make_snapshot` encrypts with the office DEK; receiver verification in `_handle_incoming_push` uses `self.hex_key` directly without reading `sera.key`.
  - `SeraDatabase` checks `_is_office_mode()` in `_auto_heal_raw_db` and raises `RuntimeError` naming the file instead of wiping or recreating `rawPayload.db`, including when `rawPayload.db` is 0 bytes.
  - Empty bootstrapping node checks `peer.key_id == self.key_id` before initiating auto-pull.
  - `SeraSyncDialog` displays `"different office key — rejoin needed"` in Column 7 (warning color) and disables/warns on push attempts when `peer.key_id != local.key_id`.

### P1-6 — Recovery, recovery kit, password change — Done — 2026-09-24
- Model: Gemini 3.8 Flash   Commit: 79149ab
- Tests: full suite: 980 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5). 33 tests in `tests/test_office_recovery.py`: validate_master_password, change_master_password (success, with admin key, wrong old password, invalid new password, preserves old recovery as backup, missing office), export_recovery_kit (success, with admin key, wrong password, missing office), inspect_recovery_kit, restore_recovery_kit (success to fresh dir, wrong password, rejects foreign office kit, backs up existing files on same office), recover_dek DPAPI failure handling, AST check verifying no PySide6 import in sera_keys.py, plus UI dialog and main startup tests (OfficeRecoveryDialog unlock/attempts/restore-kit, ChangeMasterPasswordDialog, export_recovery_kit_flow, missing office DB prompt, missing office DB restore with sqlcipher3 + sidecars, missing office DB wrong key rejection, missing office DB unencrypted rejection, missing office DB cancel, exact password whitespace preservation, lenient stripped password fallback, and UnifiedSettingsDialog Backup page in office vs legacy mode).
- Deviations from spec / additions & review resolutions:
  - Addressed all review findings:
    1. **Missing office DB restore encryption verification (Blocker 1):** `main.py`'s `_handle_missing_office_db` now tests the backup inside a `tempfile.TemporaryDirectory` copy to avoid modifying/checkpointing live user backups. It connects via `sqlcipher3` with `PRAGMA key = "x'{hex_key}'";`, verifies table count and `PRAGMA quick_check; == 'ok'`, restores `-wal` and `-shm` sidecars, renames leftover live sidecars to `.bak-<timestamp>` per §0 rule 3 instead of unlinking, rejects unencrypted or wrong-key files, and shows an alert on cancel before exiting.
    2. **Foreign recovery kit rejection (Blocker 2):** `sera_keys.restore_recovery_kit` verifies against `load_office(app_dir)` if `keys/office.json` already exists on the PC; if `office_id` or `key_id` differ from the kit, it raises `KeyFileInvalid` before touching or creating any files.
    3. **Removed test class name checks in production code (Blocker 3):** Removed all `type(self).__name__` checks (`Stub` and `StubSeraApp`) from `main.py` per §0 rule 4. `_recover_office_dek` directly invokes `OfficeRecoveryDialog`, and `_run_pending_office_key_migration` directly calls `_offer_export_recovery_kit`.
    4. **Settings backup page crash / NameError (Re-review Blocker A):** Restored `app_dir = Path(db_path).parent if db_path else None` before `load_office(app_dir)` in `unified_settings_dialog.py`, narrowed error handling to `except sera_keys.SeraKeysError:`, and added automated tests verifying page rendering and button visibility in both office and legacy modes.
    5. **Password whitespace handling (Re-review Blocker B):** To avoid locking out converted offices where the legacy password contained leading/trailing spaces, passwords are now stripped only when setting/validating new passwords. When unlocking (`recover_dek`, `export_recovery_kit`, `restore_recovery_kit`, and `old_password` in `change_master_password`), the exact typed password is attempted first; on `WrongPassword`, a lenient fallback to `password.strip()` is attempted if different.
    6. **DPAPI persistence notice:** `OfficeRecoveryDialog` alerts the user if Windows DPAPI cannot persist the key for automatic unlocking, informing them that the password will be needed on next start.
- Notes for later WPs:
  - `sera_keys.py` exports: `validate_master_password(pwd)`, `change_master_password(app_dir, old_pwd, new_pwd)`, `export_recovery_kit(app_dir, pwd, dest_path)`, `inspect_recovery_kit(kit_path)`, `restore_recovery_kit(app_dir, kit_path, pwd)`.
  - Constants added: `ADMIN_KEY_DPAPI_FILE = "admin_key.dpapi"`, `ADMIN_KEY_RECOVERY_FILE = "admin_key.recovery"`, `RECOVERY_KIT_FORMAT = "sera-recovery-kit-v1"`, `MIN_MASTER_PASSWORD_LEN = 8`, `DEFAULT_MASTER_PASSWORD = "admin123"`.
  - UI components added: `OfficeRecoveryDialog` (`ui/dialogs/office_recovery_dialog.py`), `ChangeMasterPasswordDialog` and `export_recovery_kit_flow` (`ui/dialogs/change_master_password_dialog.py`).
  - `main.py`'s `_recover_office_dek` uses `OfficeRecoveryDialog` (5 tries, show/hide password toggle, and "Restore from recovery kit file...").
  - `SeraSyncDialog` exposes "Export recovery kit" button when in office mode (`self.key_id is not None`).
  - `UnifiedSettingsDialog` Backup page includes "Office Recovery Kit & Security" section (Export Recovery Kit, Change Master Password) when in office mode.
  - For P2-2: When `admin_key.recovery` exists, `change_master_password`, `export_recovery_kit`, and `restore_recovery_kit` automatically re-wrap, bundle, and restore `admin_key.recovery` alongside `office_key.recovery`.


### P2-1 — Device identity + certificates — In review — 2026-09-24
- Model: Claude Sonnet 5   Commit: uncommitted
- Tests: full suite 977 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5). New `tests/test_sync_identity.py`, 10 tests covering both Accept items (idempotent generation, stable `device_id`) plus: file creation, `load_device_identity` returning `None` when nothing exists yet, the key PEM rejecting no/wrong passphrase and accepting the right one, certificate fields (CN = device_id, critical `BasicConstraints(ca=True, path_length=None)`, critical `KeyUsage(digital_signature, key_cert_sign)`, ~20-year validity), `load_cert_chain_args` working with a real `ssl.SSLContext.load_cert_chain`, `DeviceIdentityUnavailable` on a missing identity and on a tampered DPAPI blob, and an AST check for no PySide6 import.
- Files: new `sync_identity.py`.
- Deviations from spec: none. `device_id = sha256(public_key SPKI DER).hexdigest()[:32]` as specified; the private key is generated first (EC P-256), then `device_id` is derived from its public key before the self-signed cert is built (CN = device_id), since the cert itself embeds that public key.
- Notes for later WPs:
  - Public API: `ensure_device_identity(app_dir) -> DeviceIdentity` (generates on first call, loads on later calls), `load_device_identity(app_dir) -> DeviceIdentity | None` (`None` only when no identity file exists yet; raises `DeviceIdentityUnavailable` if files exist but can't be read/decrypted), `load_cert_chain_args(app_dir) -> (certfile, keyfile, password)` for `ssl.SSLContext.load_cert_chain` (`password` is returned as raw `bytes`, not a `str` — the `ssl` module UTF-8-encodes a `str` password, which would not round-trip the random passphrase).
  - Files live under `sera_keys.keys_dir(app_dir)`: `device_cert.pem` (plaintext, not secret), `device_key.pem` (PKCS8, `BestAvailableEncryption` with a random 32-byte passphrase — never the master password), `device_key.pass.dpapi` (that passphrase, DPAPI-protected with `sera_keys.ENTROPY_DEVICE_KEY`, same CurrentUser scope as the office DEK).
  - Reuses `sera_keys.keys_dir`, `sera_keys.atomic_write`, `sera_keys.dpapi_protect/unprotect`, and `sera_keys.ENTROPY_DEVICE_KEY` rather than duplicating them; does not touch `office.json` (P1-1 already reserved an optional `device_id` field there for a later WP to fill in once membership exists).
  - Generation isn't wired into start-up yet (§5 says "generate on first office-mode start") — no caller exists until P2-2 (membership) / P2-3 (mutual TLS) need an identity, so `main.py` is untouched by this WP.
  - `_write_new` computes the DPAPI blob before writing any file, so a DPAPI failure (e.g. non-Windows, or a Windows profile that can't protect data) leaves nothing behind rather than an unusable half-written identity — same pattern as `sera_keys.store_dek`. This module does not yet apply §0 rule 3's backup-before-overwrite to a *pre-existing, different* identity file, because nothing in this WP ever overwrites one (`ensure_device_identity` only writes when `load_device_identity` returns `None`); a future WP that replaces an identity (e.g. re-issuing a device cert) should back up the old files first, the way `sera_keys._replace_key_file` does.

### P2-1 — review fixes — 2026-09-24
- Model: Claude Sonnet 5 (review by Claude Opus 5.5, two rounds)   Commit: uncommitted
- Round 1 fixed all four findings from the first review:
  1. **Crash during first-time setup.** `_write_new` now writes `device_key.pem` and `device_key.pass.dpapi` first and `device_cert.pem` last, so the cert's presence is the "identity fully generated" marker. A previous crash between those writes leaves the key/passphrase without a cert; the module treats that as "no identity yet" rather than a permanent `DeviceIdentityUnavailable`.
  2. **Trusting the cert's CN blindly.** `load_device_identity` now recomputes `sha256(cert public key SPKI)[:32]` and compares it against the CN, and separately compares the loaded private key's public key (SPKI) against the certificate's, raising `DeviceIdentityUnavailable` if either check fails. This matters once P2-2/P2-3 rely on `device_id` for membership and fingerprint checks.
  3. **Two processes generating at once.** `ensure_device_identity` takes a new best-effort file lock (`keys/device_identity.lock`, `_IdentityLock`) before generating, re-checking `load_device_identity` after acquiring it.
  4. **Minor test issues.** `test_no_pyside6_import` now resolves `sync_identity.py` via `__file__` instead of a cwd-relative path; the unused `ExtendedKeyUsageOID` import was removed.
  - Tests (round 1): full suite 984 passed / 10 failed / 2 skipped (same 10 pre-existing failures).
- Round 2 fixed one blocking regression the round-1 fix introduced, found by the second review pass:
  - **New race:** `load_device_identity` was moving the stray key/passphrase files aside (as `.bak-<ts>`) whenever the cert was missing -- including when called with *no lock held*, from `ensure_device_identity`'s first check and from `load_cert_chain_args`. If process A had written the key and passphrase but not yet the cert, a concurrent `load_device_identity` call (e.g. from a P2-3 transport thread calling `load_cert_chain_args`) would rename A's in-progress files out from under it, and A would then write a cert with no matching key on disk -- reproduced in a scratch folder by the reviewer.
  - **Fix:** `load_device_identity` is now strictly read-only -- with no cert it just returns `None` and touches nothing. The stray-file cleanup moved into a new `_clear_incomplete_identity`, called only from inside `ensure_device_identity` while holding `_IdentityLock`, immediately before generating.
  - Also fixed the two "not blocking" items from the same review: `_IdentityLock` previously used one constant for both "how long this caller waits" and "how old before a lock is stale", so on a self-inflicted timeout it returned as if it owned the lock and would then delete a lock it never acquired. Split into `_LOCK_ACQUIRE_TIMEOUT_SECONDS` (10s, how long a caller waits) and `_LOCK_STALE_SECONDS` (30s, how old by mtime before an abandoned lock is stolen); on its own timeout, `__enter__` now raises `TimeoutError` and `__exit__` only unlinks the lock file if this instance actually created it. `_backup_stray` now raises on a failed rename instead of silently swallowing it (so `_write_new` can't overwrite a stray file with no backup).
  - New tests: `test_concurrent_load_during_generation_does_not_disturb_writer` (reproduces the reviewer's race directly: writes key+passphrase without a cert, asserts `load_device_identity` returns `None` without touching either file), `test_identity_lock_timeout_raises_and_does_not_steal_live_lock`; `test_crash_before_cert_written_is_recovered_on_next_call` updated to check that `load_device_identity` alone no longer performs the backup (only `ensure_device_identity` does).
  - Tests (round 2, final): `tests/test_sync_identity.py` now 16 tests, all passing. Full suite: 986 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5).
- Deviations from spec: none beyond the P2-1 entry above.

### P2-2 — Office admin key and signed records — Done — 2026-09-24
- Model: Claude Opus 5.5   Commit: uncommitted
- Tests: full suite 1040 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5). New `tests/test_sync_admin.py`, 54 tests. Accept: `test_forged_record_rejected`, `test_modified_record_rejected` (6 fields incl. un-revoke and type swap), `test_lower_rev_ignored`, `test_claim_admin_wrong_password_changes_nothing` (key files and `_sync_members` rows byte-identical). Also: added field / missing / garbage signature, a signed member record whose `cert_pem` isn't its `device_id`'s, malformed-but-signed records, bad admin pubkey, staff_change sign/verify, higher rev replaces, same record twice is a no-op, equal-rev conflict converges in either order, office_admin rev rule, tampered DB row not returned when re-verified, token letter sequence (A…Z, AA…ZZ, AAA), `create_admin_key` (success, wrong password changes nothing, refused if one exists), `load_admin_key` rejects a key not matching office.json, P1-6 `change_master_password` re-wraps the real admin key, membership init/add/revoke, revoked letters never reused, hand-over via `claim_admin` + old admin dropping its key, non-member claim refused, claim on current admin restores a lost DPAPI copy, P1-4 migration creates the admin key and its rollback removes it, no PySide6 import. Upsert/rowcount behaviour also checked by hand on `sqlcipher3`.
- Files: new `sync_admin.py`, new `tests/test_sync_admin.py`; `sync_migrate.py` (step 5 creates the admin key before office.json; `KEY_FILES` includes the two admin key files so both rollback paths remove/restore them).
- Deviations from spec / decisions for the reviewer:
  1. **Where `office_admin` is stored.** §5 names only `_sync_members(device_id PK, record_json, rev)`. The single office_admin record is stored there under the reserved key `device_id = 'office_admin'` (real ids are 32 hex chars, so no collision). `list_members` / `get_member` skip it.
  2. **Equal `rev`.** §5 says only "higher rev replaces lower". For two *different* validly signed records with the same rev (possible if two PCs hold the admin key during a hand-over), the one whose canonical JSON sorts higher wins. This makes every PC keep the same record whatever the arrival order. Done in one `INSERT … ON CONFLICT … WHERE` statement.
  3. **Deleting `admin_key.dpapi` vs §0 rule 3.** §5 says the old admin PC "deletes its admin_key.dpapi". `reconcile_admin_key` does delete it, instead of renaming it to `.bak`, because a `.bak` would still let that Windows account sign admin records without the master password. It only deletes when a verified office_admin record names another PC **and** `admin_key.recovery` is present, so the key is never lost from the PC. **Owner confirmed 2026-09-24: delete, as §5 says (no `.bak`).**
  4. **`claim_admin` also updates member roles.** Besides the new office_admin record (rev+1), it re-signs the new admin's member record as `role: admin` and the old admin's as `role: member` (each rev+1) so the Members list (P2-7) isn't stale. The office_admin record is authoritative; `role` is for display.
  5. **Strict record shape.** member/office_admin records must have exactly the §5 fields (+ `type`, `sig`). `sign_record` refuses malformed records, and `verify_record` checks that a member's `cert_pem` hashes to its `device_id`. `staff_change` records only need `type` + a valid `sig` here; P3-4 defines and checks their payload.
  6. **Signing needs the named admin PC.** `add_member` / `revoke_member` refuse (`NotAdmin`) unless the current office_admin record names the calling device *and* it holds the key. A revoked device can't be re-added with the same identity (`MembershipError`), so its letter stays retired. The admin PC can't revoke itself.
- Notes for later WPs:
  - Public API: `create_admin_key(app_dir, password)` (office with `admin_pubkey` null, i.e. the admin PC after an earlier P1-4 run, or "New office" in P2-7; checks the password against `office_key.recovery`), `write_admin_key_files`, `generate_admin_key`, `load_admin_key` / `has_admin_key`, `public_key_b64`, `device_id_from_cert_pem`, `sign_record`, `verify_record`, `ensure_members_table`, `store_record(conn, record, admin_pubkey) -> bool`, `get_member` / `list_members(include_revoked=)` / `get_office_admin` (pass `admin_pubkey=` to re-verify rows), `next_token_letter`, `token_letter_for_index`, `init_office_membership(app_dir, conn, cert_pem, device_name)` (creator = letter A, role admin, office_admin rev 1), `add_member(app_dir, conn, admin_device_id, cert_pem, name)`, `revoke_member(app_dir, conn, admin_device_id, device_id)`, `claim_admin(app_dir, conn, password, device_id)`, `reconcile_admin_key(app_dir, conn, device_id)`. Errors: `SyncAdminError` > `InvalidRecord`, `AdminKeyUnavailable`, `NotAdmin`, `MembershipError`; password errors are `sera_keys.WrongPassword`.
  - `admin_pubkey` format = base64 of the raw 32-byte Ed25519 public key; `admin_key.dpapi` / `.recovery` hold the raw 32-byte private key (DPAPI entropy `ENTROPY_ADMIN_KEY`, AAD `admin_aad(office_id)`).
  - Not wired into start-up or UI yet: nothing in `main.py` calls `init_office_membership` or `reconcile_admin_key`, and `_sync_members` is created on first use. P2-4 (pairing, `add_member` + sending records), P2-7 (New office / Hand over admin / Become admin / Remove) and whoever receives records (P2-4/P3) should call `reconcile_admin_key` after storing a new office_admin record. An office migrated before this WP has `admin_pubkey: null` and needs `create_admin_key` (master password) before it can pair PCs.
  - All functions take a DB-API connection. If the caller already has a transaction open they join it (the caller commits); otherwise they use `BEGIN IMMEDIATE` … `COMMIT`.
  - `office.json.device_id` is still not filled in (P2-1 note). P2-4 sends office.json to joiners, so whoever fills it in must not send the admin's own value.

### P2-3 — Mutual-TLS transport + framing — Done — 2026-09-24
- Model: Claude Opus 5.5   Commit: uncommitted
- Tests: full suite 1075 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5). New `tests/test_sync_transport.py`, 26 tests, all on 127.0.0.1 with real P2-1 identities. Accept: `test_two_members_connect`, `test_non_member_refused_by_server`, `test_non_member_refused_by_client`, `test_revoked_member_refused_after_context_rebuild`, `test_oversized_frame_refused_by_receiver`, `test_slow_peer_times_out`. Also: context settings, client refuses a member it didn't mean to reach (`WrongPeer`), a cert **issued by a member's key** is refused on both sides, client refuses a revoked server, `MemberSet.from_db` skips revoked and tampered rows, sender-side oversize (nothing sent, session still usable), oversized chunk header, malformed frames (x4), dripping peer (per-frame deadline), session deadline, a silent TCP client doesn't block a member and frees its slot, 5th session gets `busy`, 3 MB file streamed in chunks with matching sha256, more file bytes than announced refused, no PySide6 / heavy top-level imports.
- **OpenSSL check (§7 risk): passed.** Python 3.14.6 / OpenSSL 3.5.7 accepts the P2-1 self-signed CA-flag certs as both server and client certs with `CERT_REQUIRED` + `cadata`. No verification was loosened.
- Files: new `sync_transport.py`, new `tests/test_sync_transport.py`.
- Deviations from spec / additions (for the T3 review):
  1. **The fingerprint check is load-bearing, not a formality.** Member certs are CA certs, so a leaf signed by a member's key verifies in TLS. Tested: only the fingerprint check refuses it. Keep that check in any refactor.
  2. **Own cert** goes into `cadata` (as §5 says) but is accepted as a peer only if it is also in the active member list.
  3. **No TLS 1.3 session tickets** on the server (`num_tickets = 0`). A resumed session skips the certificate check, so a revoked device could otherwise resume.
  4. **`update_members` also closes sessions that are already open** with devices no longer in the set. Membership is re-checked when a session is registered, so a revoke that lands during a handshake still wins.
  5. **`busy`**: the frame is sent only after the TLS handshake and member check, in a worker thread (never in the accept loop), so non-members learn nothing. Busy-refusers are capped at `max_sessions`; beyond that the connection is closed without a frame.
  6. **Timeouts:** "30 s per frame" means the whole frame must arrive within 30 s (a deadline, so a peer dripping one byte every 29 s still times out), not 30 s between reads. A chunk's raw bytes get their own 30 s. `send_file` uses 1 MB chunks so this holds on slow Wi-Fi. The TLS handshake also has 30 s. Everything is capped by the 10-min session deadline.
  7. **Frame rules:** a frame must be a JSON object with a string `"t"`. `chunk` and `busy` are reserved (`send` refuses them). A chunk's `n` is also capped at 16 MB. Oversize is refused before anything is sent.
  8. **Port binding:** `SO_EXCLUSIVEADDRUSE` on Windows, so another process can't bind 49159 alongside Sera.
  9. **`recv_file`** creates the file with `"xb"` (never overwrites, §0 rule 3) and removes only its own partial file on failure.
- Notes for later WPs:
  - Public API: `MemberSet(members=[(device_id, cert_pem)], own_cert_pem)` / `.from_records(records, own)` / `.from_db(conn, admin_pubkey, own)` (uses `sync_admin.list_members(include_revoked=False)`); `SyncTransport(sync_identity.load_cert_chain_args(app_dir), members, frame_timeout=30, session_deadline=600)` with `.connect(host, port, expected_device_id) -> Session`, `.serve(handler, host="0.0.0.0", port=49159, max_sessions=4, on_reject=None) -> SyncServer` (`.address`, `.active_sessions`, `.stop()`), `.update_members(members)`, `.contexts`. `Session`: `send(dict)`, `recv() -> dict`, `send_chunk(bytes)`, `read_chunk(sink)` (after `recv` returned a chunk header), `send_file(path) -> (size, sha256)`, `recv_file(path, size) -> sha256`, `close()`, `peer_device_id`, `peer_address`. Errors: `TransportError` ⊃ `NotAMember`, `WrongPeer`, `FrameTooLarge`, `FrameTimeout`, `SessionDeadline`, `ProtocolError`, `ConnectionClosed`, `Busy`.
  - **TLS 1.3 detail:** when a *server* refuses a client's cert, the client's `connect()` usually succeeds and the failure shows up on the first `recv()`/`send()` as `ConnectionClosed`. Callers (P2-5 diagnostics, P3-5) should treat "closed right after connect" as a possible refusal.
  - `handler(session)` runs in a worker thread and the session is closed when it returns. Any `TransportError` from `recv` has already closed the session.
  - Whoever stores new member records (P2-4, P3) must call `transport.update_members(MemberSet.from_db(...))` afterwards.
  - Not wired into `main.py`/`sync_peer.py` yet: nothing listens on 49159 until P2-5/P2-6/P3-5 start a server.

### P2-3 — review fixes — 2026-09-24
- Model: Claude Opus 5.5 (review by Claude Opus 5.5)   Commit: uncommitted
- Fixed all findings from the review:
  1. **Slot exhaustion by devices that never finish the TLS handshake (should-fix).** A connection used to take one of the 4 session slots when it was accepted. Now it only takes one after `_verify_peer` succeeds, so a device without a member certificate can no longer hold a slot. Connections that haven't finished the handshake are capped separately: `MAX_PENDING_HANDSHAKES = 16` in total, `MAX_PENDING_PER_ADDRESS = 4` per source IP. Connections over either cap are closed straight away. They also get a shorter timeout (`HANDSHAKE_TIMEOUT_SECONDS = 10`, new `handshake_timeout` argument). The old separate `busy` refusers are gone. A verified member that finds all 4 slots full gets `busy` from its own worker thread. `SyncServer.active_sessions` now counts only verified sessions; the new `pending_handshakes` counts connections still in the handshake. **Residual risk:** an attacker using 4 or more LAN addresses can still fill the 16 pending slots for up to 10 s at a time, but members' session slots stay free.
  2. **Deeply nested JSON.** `recv()` now also catches `RecursionError` → `ProtocolError`, and closes the session.
  3. **Sink write failure.** `read_chunk` closes the session if `sink.write` raises (the rest of the chunk is unread, so the stream would be out of step), then re-raises the original error. `recv_file` still removes its partial file.
  4. **Time spent waiting for a reply.** New `recv(wait=seconds)` sets how long to wait for a frame to *start*. Once it starts, the whole frame must still arrive within the frame timeout, and everything stays capped by the session deadline. **P2-6:** the joiner should use `recv(wait=...)` for the manifest, because the export can take more than 30 s.
  5. **Small gaps.**
     - `connect()` raises `ValueError` if `expected_device_id` is missing or empty.
     - `recv_file` refuses zero-length chunks (`ProtocolError`), so a member can't stall it with them.
  6. **Heading "Done" vs tracker "In review".** I kept the heading because the §8.3 template allows only Done/Partial/Blocked there (it records the work, not the review). The tracker is the source of truth for review status.
- Tests: 6 new or changed in `tests/test_sync_transport.py` (now 32):
  - `test_idle_tcp_flood_does_not_take_session_slots`: 30 raw TCP connections from 127.0.0.2; all 4 member slots still work and the 5th member gets `busy`.
  - `test_recv_wait_allows_a_slow_start_but_not_a_slow_frame`.
  - `test_connect_requires_expected_device_id`.
  - `test_sink_failure_closes_session`.
  - `test_recv_file_refuses_empty_chunks`.
  - A `deeply-nested` case added to the malformed-frame test.
  - `test_silent_tcp_client…` now uses `handshake_timeout` and also checks `pending_handshakes`.

  Full suite: 1081 passed / 10 failed / 2 skipped (same 10 pre-existing).
- Deviations from spec: the per-address and total pending-handshake caps and the 10 s handshake timeout are additions. §5 only sets the 4-session limit.

### P2-2 — review fixes — 2026-09-24
- Model: Claude Opus 5.5 (review by Claude Opus 5.5)   Commit: uncommitted
- Fixed from the review:
  1. **A removed PC could stay in the office (should-fix 1).** With the equal-rev tie-break, an active record (`"revoked_at":null`) sorts above a revoke (`"revoked_at":"2026-…"`). So if the admin revoked PC X while X was taking over admin at the same rev, every PC kept X active. `store_record` now decides in Python inside `BEGIN IMMEDIATE`, using `_replaces`: for member records a revoke is final (it replaces an active record at any rev, and an active record never replaces a revoked one), and a record that changes `token_letter` or `cert_pem` is ignored. The rest of the rule is unchanged: higher rev wins, and equal rev goes to the higher canonical JSON.
  2. **Unchecked reads by default (should-fix 2).** `get_member`, `list_members` and `get_office_admin` now require `admin_pubkey` (no default) and always re-verify. Rows that fail are left out or returned as `None`. `next_token_letter` still reads every row unverified on purpose: a letter in a damaged row is skipped, never handed out again.
  3. **`claim_admin` (minor 1).** The membership check (`me`) now runs inside the transaction. `claim_admin` refuses to run with a caller-held transaction open (`SyncAdminError`), so `admin_key.dpapi` can't be written for records the caller later rolls back.
- Not changed: **minor 2.** If two PCs claim admin at the same moment, the one that loses the tie keeps `role: admin` on its member record. This only affects what the Members screen shows; the office_admin record decides who is admin. P2-7 should show the admin from `get_office_admin`, not from `role`.
- **Plan gap for the owner (not fixed here, spec change needed).** A PC only gets the member records when it pairs (P2-4 step 5). If it later claims admin, it doesn't know about PCs that joined after it, so it could hand out a letter already in use (breaks D8) or be unable to revoke those PCs. Before Phase 3 replicates `_sync_members`, Phase 2 needs member records exchanged between members (for example in the P2-5 gossip / session HELLO), or `claim_admin` must first fetch the current list from a reachable member. `claim_admin` itself can't detect this.
- Tests: `tests/test_sync_admin.py` now has 63 tests, all passing. New tests: `test_revoke_wins_equal_rev_race_in_either_order` (x2), `test_active_record_never_replaces_revoked_at_any_rev`, `test_revoke_replaces_active_even_at_lower_rev`, `test_token_letter_or_cert_change_refused`, `test_readers_require_admin_pubkey`, `test_next_token_letter_counts_unverifiable_rows`, `test_claim_admin_refuses_open_transaction`, `test_claim_admin_by_revoked_member_refused`. Full suite: 1049 passed / 10 failed / 2 skipped (same 10 pre-existing).
- API change for later WPs: the readers' `admin_pubkey` is now a required argument (`get_member(conn, device_id, admin_pubkey)`, `list_members(conn, admin_pubkey, include_revoked=True)`, `get_office_admin(conn, admin_pubkey)`).

### P2-4 — Pairing with 6-digit code (SPAKE2) — Done — 2026-09-24
- Model: Claude Opus 5.5   Commit: uncommitted
- Tests: full suite 1134 passed / 10 failed / 2 skipped (same 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5). New `tests/test_sync_pairing.py`, 33 tests, all on 127.0.0.1 with real P1-1/P2-1/P2-2 keys; passed 4 runs in a row. Accept: `test_pairing_success` (joiner ends with an identical `key_id`, in office.json and in the stored DEK), `test_wrong_code_fails_and_counts_attempt`, `test_three_failures_close_window`, `test_modified_spake_message_fails_key_confirmation` (a relay swaps in another valid SPAKE message). Also: a flipped bit in each of the 6 messages fails (x6); records stored in a given connection; code format / `123 456` accepted; bad codes rejected before connecting (x6); a guesser who reads the admin's confirmation and hangs up uses up an attempt; connections that never send a SPAKE message aren't counted; a garbage SPAKE point is counted; second joiner on the same code refused; one joiner at a time (`busy`); window expiry; cancel; start refused without the admin key or on a non-admin PC; admin key lost while open → refused, not counted; removed device refused; join refused on a PC that already has office.json; joiner-side validation of the welcome (x12); code/DEK never logged; no PySide6 / no top-level spake2/cryptography import; spake2 in requirements.
- Files: new `sync_pairing.py`, new `tests/test_sync_pairing.py`; `requirements.txt` (`spake2>=0.9`); `build_tools/Amas_Sera.spec` (`'spake2'` hidden import; that file is gitignored, so this change is local to the owner's PC — see the review-fixes entry). `spake2 0.9` installed in `venv` (it depends only on `cryptography`).
- Deviations from spec / decisions (for the T3 review):
  1. **Order of the confirmations.** The admin sends its MAC first. The joiner checks it before it sends its own, so a fake "admin" on 49158 never gets a confirmation it could test a guess against.
  2. **What counts as a failed attempt.** A connection counts once the admin has received the joiner's SPAKE message, because that is when a code guess has been made. From then on, anything short of a completed join counts: wrong MAC, bad point, hang-up after reading the admin's MAC, tampered encrypted request. Connections that close or stall before sending a SPAKE message don't count. A LAN device can still close the window with 3 guesses (a DoS the spec accepts), but it can't learn the code.
  3. **One joiner at a time.** A second connection gets a `{"t":"busy"}` frame and a graceful close. On Windows, a plain close with the joiner's first message still unread resets the connection before `busy` can be read. At most 4 such refusals run at once; any beyond that are closed straight away. A session gets 10 s for the first message, 30 s per frame and 60 s in total, and is capped by the window's end. Frames are limited to 4 KB until the joiner has proved the code, then 1 MB. The framing is local to `sync_pairing` (same wire format as P2-3) because P2-3's `Session` allows 16 MB frames from an unauthenticated peer.
  4. **The window closes after any use of the code.** Close reasons: `joined`, `too_many_attempts`, `expired`, `cancelled`, `refused` (a removed PC tried to rejoin: `MembershipError`, sent to the joiner as `{"t":"refused"}`), `failed` (the record was signed but the welcome couldn't be sent, or the PC can no longer add members because admin was handed over or the admin key can't be read; not counted as an attempt). Pairing the same PC again returns its existing record (P2-2 `add_member`).
  5. **Welcome contents.** `office` is office.json with `device_id: null`; the joiner fills in its own (P2-2 note). `members` = every member record, including revoked ones so their letters stay retired, **plus** the `office_admin` record, so the joiner knows which PC is admin. `admin_address` = the admin's local address on that connection; the joiner falls back to the address it dialled if the value is missing or unusable.
  6. **Joiner checks before writing anything:** office fields, DEK length, `key_id(DEK)` = `office.key_id`, recovery blobs are argon2id wrap blobs, every record verifies with `admin_pubkey`, exactly one `office_admin` naming an active member, and its own active record with its own cert. It refuses to start if `keys/office.json` already exists.
  7. **"Store the members" before any DB exists.** A joiner has no master.db until P2-6. The verified records, `admin_device_id` and `admin_address` are written to `incoming/join/pairing.json`, and also into `conn` (`_sync_members`) if one is passed. Write order: members → `office_key.recovery` + `admin_key.recovery` (as received) → `office_key.dpapi` → `pairing.json` → **office.json last**. The DPAPI blob is computed before any write. Existing files are backed up (`_replace_key_file`, §0 rule 3). There is no `admin_key.dpapi` on the joiner.
  8. **Beacon `pair: true`.** The v3 beacon doesn't exist until P2-5. `PairingWindow` exposes `is_open`, `office_name` and `port` for P2-5 to advertise, and `on_closed` to stop advertising.
- Notes for later WPs:
  - Public API: `PairingWindow(app_dir, open_db, admin_device_id, *, host="0.0.0.0", port=49158, window_seconds=300, max_failures=3, first_message_timeout=10, on_joined=None, on_closed=None)`.
    - `.start()` raises `NotAdmin` / `AdminKeyUnavailable` before opening anything.
    - Also `.code`, `.display_code`, `.is_open`, `.busy`, `.failed_attempts`, `.close_reason`, `.port`, `.office_name`, `.close()`.
    - `open_db()` must return a **new** connection, because it is used from the worker thread.
  - `join_office(app_dir, host, code, device_name, *, port=49158, timeout=10, conn=None) -> JoinResult(office_id, office_name, key_id, device_id, token_letter, admin_device_id, admin_address, records)`. `normalize_code(code)`. Errors: `PairingError` ⊃ `WrongCode`, `PairingBusy`, `PairingProtocolError`, `JoinRefused`.
  - **P2-7 / whoever wires it:** in `on_joined`, call `transport.update_members(MemberSet.from_db(...))` (P2-3 note). Nothing in `main.py` uses this module yet. The sync_* modules aren't in the PyInstaller hidden imports; P2-7 wiring makes `main.py` import them.
  - **P2-6:** start the snapshot download from `incoming/join/pairing.json` (admin device id + address + records for `MemberSet.from_records`). A join interrupted after pairing leaves office.json without master.db, and today's start-up (P1-6 `_handle_missing_office_db`) would offer a backup restore. P2-6 must detect `pairing.json` and resume the download instead.
  - Not exercised: two real PCs, and Windows Firewall on 49158 (P0-9a opens 49156–49159).

### P2-5 — Discovery v3, address book, gossip — Done — 2026-09-24
- Model: Gemini 3.8 Flash (review by Claude Opus 5.5)   Commit: uncommitted
- Tests: 28 tests in `tests/test_sync_discovery.py`, all passing (0.7s). Full suite: 1136 passed / 10 failed (the 10 pre-existing: dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5) / 2 skipped.
  - address ordering: `test_address_ordering_last_ok_first`, `test_address_ordering_local_ok_beats_newer_gossip`, `test_address_ordering_newest_first_and_nulls`, `test_address_ordering_with_beacon_address`, `test_address_ordering_beacon_only`
  - gossip merge & IP filtering: `test_gossip_export_filters_7_days`, `test_gossip_merge_adds_new_addresses`, `test_gossip_merge_never_overwrites_local_ok`, `test_gossip_merge_rejects_future_timestamps`, `test_gossip_merge_skips_own_device_and_non_members`, `test_gossip_merge_malformed_ignored`, `test_gossip_ip_validation` (rejects 0.0.0.0, 255.255.255.255, loopback, link-local, multicast)
  - diagnostics: `test_diagnostics_unreachable_member_checks_local_ok` (asserts exact text "Can't reach <name> — check that both PCs are on a Private network and that the Wi-Fi doesn't isolate devices" when local PC has not reached peer in 10m, regardless of gossip), `test_diagnostics_recent_local_ok_no_warning`, `test_diagnostics_no_addresses_no_warning`
  - beacon sightings & DB safety: `test_beacons_never_write_to_database` (verifies incoming beacons never write to master.db, kept strictly in-memory), `test_discovery_with_broadcast_disabled_localhost` (exercises bidirectional unicast discovery and in-memory sightings), `test_beacon_sightings_capping_and_pruning` (strictly caps in-memory sightings at 500 entries)
  - beacon & tag: `test_office_tag_computation` (HMAC-SHA256 16 hex chars), `test_beacon_v3_format_normal`, `test_beacon_v3_format_pairing`, `test_parse_beacon_invalid` (length caps, hex validation)
  - shared port & service: `test_sync_peer_dispatches_v3_beacon` (verifies port 49156 shared dispatch to `on_v3_beacon`, uses `tmp_path`), `test_discovery_service_listen_false_and_ephemeral_reply` (verifies sender-only `listen=False` mode and ephemeral socket unicast replies), `test_worker_db_factory_and_raw_conn_warning` (verifies `open_db` factory usage and raw connection warning), `test_service_idempotent_start_stop`, `test_record_successful_session`, `test_no_pyside6_import`.
- Files: new `sync_discovery.py`, new `tests/test_sync_discovery.py`, updated `sync_peer.py` (`on_v3_beacon` hook).
- Review fixes & refinements applied:
  1. **Beacons never write to master.db (Review Blocker 1):** Incoming beacons update in-memory sightings (`_beacon_sightings`) only, preventing forged beacon storms from growing the database or turning the PC into an amplification reflector. Database rows in `_local_addresses` are written only on successful sessions (`record_successful_session`) or valid gossip merge.
  2. **Separate local_ok_at vs last_ok_at (Review Blocker 2):** Added `local_ok_at` (when THIS PC had a successful connection) alongside `last_ok_at` (network-wide gossip). Gossip merge updates `last_ok_at` only if newer, never touches `local_ok_at`. Future timestamps (`> now + 60s`) are rejected. `get_connect_order` prioritizes `local_ok_at`, and `check_member_unreachable` evaluates `local_ok_at` so Wi-Fi isolation (D4) correctly triggers the warning even if other PCs reached the member.
  3. **Shared port 49156 dispatch & sender-only mode (Refinement A):** `sync_peer.py` dispatches `magic == "sera-sync-v3"` datagrams directly to `on_v3_beacon`. `DiscoveryService` supports `listen=False` sender-only mode to prevent opening a duplicate listening socket, avoiding Windows Winsock unicast packet stealing on shared `SO_REUSEADDR` ports. Unicast replies via `handle_datagram(sock=None)` gracefully use ephemeral UDP sockets.
  4. **Sightings capping & eviction (Refinement B):** In-memory beacon sightings are strictly capped at 500 entries with automatic pruning of stale entries (`> 60s`) and LRU eviction under unauthenticated beacon floods.
  5. **Worker thread DB connections & warning (Refinement C):** `DiscoveryService` accepts `open_db` factory so worker threads use dedicated connections without colliding with open caller transactions; logs a warning if only a raw `db_conn` is passed.
  6. **Gossip IP filtering (Minor Refinement):** `_is_valid_gossip_ip` rejects special/invalid unicast IPs in gossip (`0.0.0.0`, `255.255.255.255`, loopback, link-local, multicast) on both import and export.
  7. **Clean test files (Minor Refinement):** `test_sync_peer_dispatches_v3_beacon` uses pytest `tmp_path` fixture to leave no temporary artifacts in workspace.
- Notes for later WPs:
  - Public API in `sync_discovery.py`:
    - `compute_office_tag(dek: bytes) -> str`
    - `make_beacon_payload(...) -> dict`, `encode_beacon(payload) -> bytes`, `parse_beacon(data, sender_ip=None) -> dict | None`
    - `ensure_address_book_table(conn)` (creates `_local_addresses(device_id, ip, port, last_ok_at, local_ok_at, source)` in `master.db`)
    - `record_successful_session(conn, device_id, ip, port, ok_at=None, source=None)`
    - `upsert_address(conn, device_id, ip, port, source="beacon", last_ok_at=None, local_ok_at=None)`
    - `remove_address(conn, device_id, ip, port)`
    - `get_known_addresses(conn, device_id=None) -> list[dict]`
    - `get_all_destinations(conn) -> set[tuple[str, int]]`
    - `get_connect_order(conn, device_id, beacon_addr=None) -> list[tuple[str, int]]`
    - `export_gossip_addresses(conn, max_age_days=7, now=None) -> list[dict]`
    - `merge_gossip_addresses(conn, addresses, own_device_id=None, is_member=None, max_age_days=7, now=None) -> int`
    - `check_member_unreachable(conn, device_id, member_name, threshold_seconds=600, now=None) -> str | None`
    - `get_unreachable_member_warnings(conn, members, threshold_seconds=600, now=None) -> dict[str, str]`
    - `DiscoveryService(db_conn=None, open_db=None, office_tag=None, device_id="", device_name="", bind_host="0.0.0.0", beacon_port=49156, sync_port=49159, pair_port=49158, enable_broadcast=True, manual_destinations=None, is_member=None, on_peer_discovered=None, on_pairing_beacon=None, listen=True)` with `.start()`, `.stop()`, `.send_beacons_now()`, `.set_pairing(...)`, `.handle_datagram(data, sender_ip, sock=None)`, `.get_beacon_sighting(device_id)`.
  - For P2-6: Snapshot export drops `_local_*` tables (`_local_addresses` dropped automatically).
  - For P2-7: Network warnings in panel can call `get_unreachable_member_warnings(conn, members)`. When integrating with legacy sync, set `listen=False` on `DiscoveryService` and hook `sync_peer.on_v3_beacon = discovery_svc.handle_datagram`.
  - For P3-5: The session HELLO payload includes `addresses = export_gossip_addresses(conn, max_age_days=7)`, receiving HELLO calls `merge_gossip_addresses(conn, peer_addresses, own_device_id=own_dev, is_member=...)`. Outgoing session connect loop calls `get_connect_order(conn, device_id, beacon_addr=discovery.get_beacon_sighting(device_id))` to determine attempt order.

### P2-4 — review fixes — 2026-09-24
- Model: Claude Opus 5.5 (review by Claude Opus 5.5)   Commit: uncommitted
- Fixed from the review:
  1. **Blocking: a second PC could join on the same code.** When a join finished, `_handle` freed the joiner slot before `close(outcome)` marked the window closed. A connection waiting in that gap was admitted and could pair fully (letter C, the DEK, office.json), or make an extra wrong-code guess that wasn't counted. The same gap existed after `refused` / `failed`. Now `_handle` calls `close(outcome)` **before** freeing the slot. The accept loop admits a connection only while the slot is free *and* the window isn't closed, so nothing gets in. `close()` now ends a running session whatever the reason, not only on `cancelled` / `expired`. A connection accepted after closing is dropped without a `busy` reply.
  2. **The build-spec change isn't in git.** `.gitignore` has `*.spec`, so the `'spake2'` hidden import in `build_tools/Amas_Sera.spec` exists **only on the owner's PC**. It's still needed there for the installer build. The owner decides whether to start tracking that file.
  3. **A LAN device could keep the single joiner slot busy without warning.** Connections that end before trying a code still don't count as attempts, because no guess was made. They are now counted per source address (`PairingWindow.stalled_connections`). From `STALL_WARNING_THRESHOLD = 3` on, each one logs a warning naming the address ("… may be blocking pairing"). P2-7 can show this in the Add workstation dialog. The DoS itself isn't prevented, the same as with 3 guesses (spec).
  4. **`_install` wrote to the database before the key files.** New order: recovery blobs → `office_key.dpapi` → `pairing.json` → records into `conn` (if given) → office.json. Inside that transaction the joiner now checks that `conn` ends up with its own **active** member record, and refuses otherwise (`JoinRefused`, no office.json). `store_record` returning False isn't an error; it only means an equal or newer record was already there.
  5. **Noted, not changed: the joiner doesn't prove it holds its certificate's private key.** Only someone who knows the code can send the join request, and they get the DEK anyway, so there's no extra risk. A PC that sends someone else's cert just can't open a mutual-TLS session afterwards.
- Also: `test_one_joiner_at_a_time` failed once during P2-5's full-suite run. The likely cause: under load, the 5 s first-message timeout of the connection holding the slot ran out before the joiner connected, so the joiner paired instead of getting `busy`. The holder is closed explicitly, so the test now uses a 60 s timeout. `test_close_ends_a_running_session_whatever_the_reason` does the same.
- Tests: `tests/test_sync_pairing.py` now has 38 tests (5 new). `test_no_second_session_between_join_and_close[right]` and `test_close_ends_a_running_session_whatever_the_reason` both **failed on the unfixed code** (the second PC paired, getting letter C), and pass now. Also new: `[wrong]` variant, `test_repeated_stalls_from_one_address_are_warned_about`, `test_install_writes_database_after_key_files_and_checks_it`; `test_connection_without_a_guess_is_not_counted` also checks `stalled_connections`. The file passed 3 runs in a row. Full suite: 1143 passed / 10 failed / 2 skipped (same 10 pre-existing).
- Deviations from spec: none beyond the P2-4 entry above.

### P2-6 — Snapshot service and joiner install — Done — 2026-09-24
- Model: Gemini 3.8 Flash (review by Claude Opus 5.5)   Commit: e15c420
- Review fixes (B1 & should-fix):
  1. **B1: Target database and sidecar backups (§0 rule 3):** `_install_downloaded_files` now backs up existing target DBs and their `-wal`, `-shm`, `-journal` sidecars to `*.bak-<YYYYmmdd_HHMMSS>` prior to `os.replace` rather than silently overwriting/unlinking.
  2. **Orphan local rawPayload.db set aside:** If local `rawPayload.db` exists but the snapshot does not carry one (e.g. admin has no rawPayload.db), local `rawPayload.db` and sidecars are backed up and set aside as `*.bak-<ts>` so office mode startup does not fail with key mismatch.
  3. **Install order:** `master.db` is installed last so that any crash mid-install leaves `has_pending_join()` True, allowing clean resumption.
  4. **Freelists purged:** `_clean_exported_db` executes `VACUUM` after dropping `_local_*` tables and deleting rows from local-mode tables, ensuring no IP or activity rows linger in SQLite freelist pages.
  5. **Startup resume UI in main.py:** Startup now presents a "Resume Office Join / Cancel" dialog when `has_pending_join()` is detected, avoiding UI freezes or misleading "select backup" dialogs.
  6. **Per-chunk progress:** `download_snapshot` invokes `on_progress(fname, received, size)` on every chunk received.
  7. **Record re-verification:** `resume_join_snapshot` re-verifies member records with `sync_admin.verify_record(r, office.admin_pubkey)`.
  8. **Server ownership note:** `handle_snapshot_session` runs on the admin PC. In tests, it runs on localhost `SyncServer`. In production, P2-7 wires it into the pairing / join flow and P3-5 wires it into the permanent sync server listening on 49159.
- Tests: 11 passed in `tests/test_sync_snapshot.py` (acceptance tests `test_join_snapshot_end_to_end`, `test_corrupt_snapshot_rejected`, plus B1 backup tests, orphan rawPayload.db backup, per-chunk progress, resume, and path traversal checks).
- Deviations from spec: none.
- Notes for later WPs (open items from the review, none blocking P2-6):
  - **P2-7 (must do): nothing serves snapshots yet.** No code in `main.py` listens on 49159 or calls `handle_snapshot_session`, so on real PCs every join fails at the download step and the start-up resume always fails. Before the Join wizard can work, the admin PC must run a `SyncTransport.serve(...)` that routes `{"t":"snapshot"}` to `handle_snapshot_session`: at least while "Add workstation" is open and until the joiner has downloaded, or permanently if P3-5's server lands first. Rebuild its `MemberSet` after `add_member` so the new joiner's cert is accepted.
  - **P2-7:** the start-up resume in `main.py` (`resume_join_snapshot`) runs on the UI thread with no progress shown (up to about 70 s: 10 s connect + 60 s manifest wait). Reuse the Join wizard's progress bar (`on_progress`) and run the download off the UI thread.
  - **P2-8:** `_backup_db_and_sidecars` ignores a failed `os.replace` of a `-wal`/`-shm`/`-journal` sidecar. An old WAL could then sit next to the new `master.db`. Fresh joiners have no sidecars; P2-8 (PCs that already have a DB) must make this raise and abort the install.
  - **P2-7 or P2-8 (whoever next edits `download_snapshot`):** on a failed download the partial file is unlinked while it is still open, which fails silently on Windows. That's harmless, because the next attempt deletes it before writing, but it should be closed before the unlink.
  - **P3-5:** `download_snapshot` has its own copy of the `Session.recv_file` loop so it can report progress. Give `recv_file` an `on_progress` hook and use it from both places, so the chunk rules live in `sync_transport.py` only.

### P3-0 — Multi-node test harness + convergence tests — In review — 2026-09-24
- Model: Gemini 3.8 Flash   Commit: uncommitted
- Tests: `tests/test_sync_harness.py` 7 passed; `tests/test_sync_convergence.py` 6 failed, 1 skipped (tests (a)-(f) fail as expected before P3-3 starts, serving as definition of done for P3-3..P3-5). Full test suite run completed with no regressions against baseline (10 pre-existing documented failures).
- Files: new `tests/sync_harness.py`, `tests/test_sync_harness.py`, `tests/test_sync_convergence.py`.
- Deviations from spec: none.
- Notes for later WPs:
  - `tests/sync_harness.py` exports `SyncHarness`, `HarnessNode`, and `digest(node_or_db)`.
  - Bootstraps admin Node 0 and pairs joiner nodes 1..N-1 through real `PairingWindow` and `join_office` (P2-4).
  - Schema details observed: `clients` does not have a `pan` column (PAN is stored via EAV in `client_values` with `column_id=1`); `client_values` has PK `(client_id, column_id)` without `updated_at`; `app_settings` has PK `key` without `updated_at`.
  - `SyncHarness.digest(node_or_db)` computes a deterministic SHA-256 hash over replicated tables in `master.db` and `rawPayload.db`, excluding local `id` primary keys and translating foreign keys to `gid` when present (per P3-7).
  - Tests (a)–(f) in `tests/test_sync_convergence.py` will pass once P3-3..P3-5 sync and replication loops are implemented.

### P3-0 — Multi-node test harness + convergence tests — Done — 2026-09-24
- Model: Gemini 3.8 Flash (review by Claude Opus 5.5)   Commit: be84d98
- Fixed from the review:
  1. **`write()` passes through `SeraDatabase._connect` (Blocking 1):** `write()` previously used raw SQLite connections bypassing `SeraDatabase._connect()`, which would skip the sealer's after-commit step added in P3-3. Fixed: `HarnessNode.open_db()` and `open_raw_db()` now delegate to `self.db._connect()` and `self.db._connect_raw()`, ensuring `write()` executes within `SeraDatabase`'s full connection lifecycle and triggers after-commit hooks.
  2. **Test (b) clock control with dynamic time (Blocking 2):** Replaced hardcoded past timestamp with `_time.time()` dynamically, advancing node 1's clock to $+1000$s so that node 1's edit deterministically wins under HLC physical time.
  3. **Test (d) conflict row assertion on node 0 (Blocking 3):** Test (d) previously asserted conflict rows on both nodes. Fixed: per the P3-4 spec, an edit discarded against a tombstone is written to `_sync_conflicts` only on node 0 where the tombstone rejected the upsert.
  4. **Test (e) single batch partition & vector non-advancement (Blocking 4):** Nodes are partitioned before node 0's 5 writes so changes accumulate into a single batch. Upon healing, the injected trigger aborts mid-batch; test asserts 0 rows landed on node 1 AND verifies `_sync_vector` for node 0 did not advance. Trigger is then dropped and resync succeeds cleanly.
  5. **Digest stringified FK translation (Should-fix 1):** Added `_translate_fk` to handle stringified integer IDs such as `cell_formatting.column_key` referencing `mcl_columns.id`, while passing through non-numeric literals like `'services'`.
  6. **Member propagation error handling & deviation (Should-fix 2):** `_propagate_all_members` raises `RuntimeError` immediately if reading `_sync_members` from admin or writing to any joiner fails. Documented in docstring and deviations that member rows are copied directly from admin without individual `store_record` signature verification.
  7. **Digest error handling on FK lookups & missing row keys (Should-fix 3):** All FK lookup queries in `_compute_digest_from_conns` raise `RuntimeError` on failure rather than swallowing with `pass`. Missing or NULL row-key columns raise `RuntimeError` (with pre-P3-2 fallback to `id`).
  8. **Digest delimiter collision (Should-fix 4):** `_encode_value` backslash-escapes `:` (composite key delimiter) along with `|`, `,`, `=`, and `\`.
  9. **Dead code removed (Should-fix 5):** Removed unused `_gid_map` helper inside `_compute_digest_from_conns`.
- Tests after fixes: `tests/test_sync_harness.py` 7 passed; `tests/test_sync_convergence.py` 6 failed as expected (tests (b)-(d) fail on setup assertion awaiting sync; (a), (e), (f) fail on digest convergence assertion; (g) skipped). Full suite: **1185 passed / 16 failed / 3 skipped** in 8m19s (10 documented pre-existing failures + 6 expected P3-0 convergence failures). Zero new regressions.
- Deviations from spec: Joiners receive a direct filesystem DB copy instead of the P2-6 snapshot service; membership rows copied directly from admin without individual store_record signature verification (documented in sync_harness.py).

### P3-1 — Classification registry sync_schema.py — In review — 2026-09-24
- Model: Claude Sonnet 5   Commit: uncommitted
- Tests: 22 passed in `tests/test_sync_schema.py` (registry coverage, mode/FK/natural-merge checks against §5, `TableSpec` validation and immutability, `pending_creation` tracking, `verify_against_live_schema` checking table names AND that every registered row_key/fk column exists in a real scratch `SeraDatabase`'s `master.db`/`rawPayload.db`, and synthetic-DB checks for an unclassified table and a missing column). Plus 2 tests in `tests/test_raw_payload_db_and_srpf.py` for the `re_resolve_all_tracker_dumps()` notes-preservation fix below. Full suite: 1185 passed / 16 failed / 3 skipped — same as baseline (10 documented pre-existing + 6 P3-0 convergence tests expected to fail until P3-3..P3-5 land, per that WP's own entry above); nothing new broke, including `tests/sync_harness.py`'s digest function (P3-0), which reads this registry directly.
- Deviations from spec, recorded honestly across two review rounds — neither caught by self-review, both caught by a Claude Opus 5.5 review:
  - **Round 1:**
    1. **Blocking, fixed then revised in round 2:** `client_raw_containers` was classified `local`, but its `notes`/`screenshot_path` (typed in by staff, verified against `ui/windows/tracker_dump_window.py:1371`) have no source in `tracker_dump`, so it didn't meet §5's condition for `local`. Should have been a stop-and-ask.
    2. **Blocking, fixed and still correct:** `cell_formatting.column_key` (part of its row_key) holds `str(mcl_columns.id)`, or the literal `"services"` for the services column — verified against `ui/windows/search_window.py:618,663`, undocumented. **Owner decision 2026-09-24: document the dual encoding now, translate in P3-2/P3-4.** Registry now has `fk={"client_id": "clients", "column_key": "mcl_columns"}` with a `notes` callout that the apply engine must pass through the literal `"services"` unchanged and only translate `column_key` to a gid when it parses as an integer.
    3–5. (still standing) The notes-wipe fix's log wording was corrected; `verify_against_live_schema` now also checks that `row_key`/`fk` columns exist live (`TableSpec.expected_live_columns`, excluding the not-yet-created `"gid"` placeholder); `TableSpec.fk` and `REGISTRY` are now `MappingProxyType` (immutable).
  - **Round 1's fix for item 1 was itself wrong, caught in round 2:**
    1. **Blocking, now fixed for real:** classifying `client_raw_containers` `lww` on `identity_key` (round 1's fix) created certain, permanent data loss. `re_resolve_all_tracker_dumps()` (database.py ~4443) does `DELETE FROM client_raw_containers` then re-inserts every row — and it runs on **every** PC today (start-up, legacy sync, the Tracker window, database.py ~3394/~4226), **not** admin-PC-only; that's only what P3-6 proposes, and P3-6 hasn't been implemented. Under P3-3's capture triggers + P3-4's tombstone rule ("if a tombstone exists for the row, drop the upsert"), that DELETE would tombstone every container's `identity_key` and the following INSERT would be dropped everywhere else — every PC's containers, notes included, gone permanently after one rebuild. Not a race, certain loss. **Owner decision 2026-09-24 (final): split instead of syncing the whole row.** `client_raw_containers` goes back to `local` (accurate now that notes/screenshot_path move out — it's an honest derived cache of `tracker_dump`). A new `client_container_notes` TableSpec (`lww` on `identity_key`, `pending_creation=True`) carries only the hand-typed fields; `re_resolve_all_tracker_dumps()`'s rebuild never touches it, so no tombstone risk. `client_container_notes` **does not exist in the schema yet** — this WP only registers the intent (§5 says P3-1 is a registry WP, building the table is schema work); see its `TableSpec.notes` for exactly what creating it involves and who should do it (P3-2, or a short dedicated WP before it). `screenshot_path` replicating a path string still won't resolve on another PC until Phase 3 has real file/blob sync — unrelated to which table holds it, accepted either way. `identity_key` still sometimes embeds a local `client_id` (`CLI-NNNNN`, an id-leak like F13) on whichever table holds it.
  - Both rounds: confirmed to the reviewer that the "owner decision" dates in this log are real — made live in chat via `AskUserQuestion`, not inferred from the code.
  - Also worth flagging for whoever commits this: because P2-6/P3-0 are (partly) uncommitted in the same working tree, a plain `git diff`/`git status` mixes their files in with this WP's, and this session twice lost its `database.py`/`tests/test_raw_payload_db_and_srpf.py` edits to what looked like cleanup elsewhere in the tree (reapplied both times). This WP's own files: `sync_schema.py`, `tests/test_sync_schema.py`, the `re_resolve_all_tracker_dumps()` notes-preservation fix + its two tests in `database.py`/`tests/test_raw_payload_db_and_srpf.py`, and this §9 entry.
- Notes for later WPs:
  - `sync_schema.py` (repo root, no PySide6 import) exports: `REGISTRY` (`MappingProxyType[str, TableSpec]`), `MASTER_DB`/`RAW_DB`, mode constants `LWW`/`ADMIN_LWW`/`APPEND`/`SET`/`LOCAL`, `TableSpec(name, db, mode, row_key=(...), fk={...}, natural_merge_on=None, notes="", pending_creation=False)` (with `.key_columns`/`.expected_live_columns` properties), `tables_for(db)`, `replicated_tables_for(db)` (excludes `LOCAL`), `pending_tables_for(db)` (tables registered but not yet created), `get(name)`, `EXCLUDED_TABLES`, and `verify_against_live_schema(conn, db)`.
  - For P3-2 (or a short WP before it): **must create `client_container_notes`** before P3-2's gid/trigger work can apply to it — see its `TableSpec.notes` for the exact `CREATE TABLE`, the one-time data migration out of `client_raw_containers.notes`/`.screenshot_path`, and the two functions (`save_srpf_container_media`/`get_srpf_container_media`, database.py ~4791-4807) that need to point at the new table.
  - For P3-2 generally: `replicated_tables_for(db)` is the list that needs a `gid` column + backfill + trigger, except where `row_key` is already a natural/composite key (`client_values`, `client_services`, `cell_formatting`, `app_settings`, `sdc_session_timelines`, `client_container_notes`) — those don't get a `gid` column per §5's row-key column. `pending_tables_for(db)` flags which registered tables don't exist yet and need creating first.
  - For P3-3/P3-4: `TableSpec.fk` gives the column→table map for FK gid-translation, including cross-DB ones (`tracker_dump.client_id`/`service_id`, `sdc_session_timelines.client_id`, `client_container_notes.client_id`, all rawPayload.db → master.db). Two columns need translation logic beyond the standard per-`fk`-column rule: `cell_formatting.column_key` (numeric-string-or-literal-`"services"`) and `client_container_notes.identity_key`/`client_raw_containers.identity_key` (sometimes embeds a local `client_id` as `CLI-NNNNN`) — see the relevant `TableSpec.notes`. `TableSpec.natural_merge_on` flags `services`/`staff_users` for the lexicographically-smaller-gid-wins merge rule.
  - Before enabling sync on ANY table that has a local full-rebuild/DELETE-based maintenance function (this WP found one — `re_resolve_all_tracker_dumps()` — there may be others per F12), check whether that function's writes would tombstone rows under P3-4's rules. This is exactly the mistake round 1's fix made.

### P2-8 — Rejoin and salvage for existing PCs — Done — 2026-09-24
- Model: Claude Opus 5.5   Commit: uncommitted
- Tests: new `tests/test_sync_rejoin.py`, 35 tests, all passing. Accept: `test_salvage_dry_run_changes_nothing` (office and legacy file hashes and every row unchanged), `test_salvage_imports_unmatched_clients`, `test_salvage_keeps_office_value_on_conflict_by_default`. Also: take-this-PC's-values per client; taking values never blanks an office value; client without a PK value is listed and not inserted (and its tracker row isn't guessed onto another client); duplicate PK → ambiguous, not imported; salvage is idempotent; legacy files never written; a colliding legacy token gets a D8 letter token (`B-5`), a free one is kept; apply needs `token_letter`; different PK column on the two sides stops (`SalvageError`); wrong legacy key refused; report file lists PANs/tokens/labels but no values; container notes counted; move/restore of legacy files incl. sidecars; move failure puts everything back; restore refuses to overwrite a live DB; resume states; saved `sera.key` / typed password; request file round trip; no PySide6; **`test_rejoin_end_to_end`** (a legacy PC with real `SeraDatabase` data rejoins a real admin PC on localhost: wrong code → legacy files back byte-identical; right code → pair, snapshot, dry run, apply, report, legacy files still intact); P2-6 sidecar abort; main.py hook no-op/consume-request; start-up flow undo + import with a ticked conflict + "Not now" keeps state; Rejoin button only in legacy mode, writes the request and restarts; wizard and salvage dialog validation.
  Full suite (run while P2-7 was editing the same tree): 1214 passed / 26 failed / 3 skipped. The failures: the 10 pre-existing (dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5), the 6 expected P3-0 convergence tests, 9 `SeraSyncDialog` tests that failed on P2-7's then-unfinished dialog (`_refresh_members` / `_on_add_workstation` not defined yet), and `test_validate_icons` (my invalid `mdi.account-sync-outline`, fixed; P2-7's `mdi.laptop-account` remains). After P2-7 finished its dialog, `test_sync_rejoin`, `test_office_key_migration`, `test_sync_hotfix`, `test_key_fingerprint`, `test_sync_snapshot`: 130 passed.
- Files: new `sync_rejoin.py`, new `ui/dialogs/rejoin_office_dialog.py`, new `tests/test_sync_rejoin.py`; `main.py` (`_run_pending_rejoin`, called right after `_run_pending_office_key_migration`, before `_resolve_encryption_key`); `ui/dialogs/sera_sync_dialog.py` ("Rejoin office" button, legacy mode only, like "Convert to office key": writes `incoming/rejoin_request.json` and restarts); `sync_snapshot.py` (`_backup_db_and_sidecars`, P2-6 note).
- Deviations from spec / decisions (for the T3 review):
  1. **Where it runs.** The wizard runs at start-up before any DB is opened (a running app can't move its own open DB on Windows), requested from the Sera Sync dialog like P1-4. Steps are resumable through `incoming/rejoin_state.json`: no office.json → the legacy files go back ("rolled_back"); office.json without master.db → offer to finish the download (`resume_download` → P2-6 `resume_join_snapshot`); both present → offer the salvage. The P2-6 start-up resume in main.py therefore never sees a rejoining PC.
  2. **Pairing failure undoes step 1.** Spec order is kept (move first, then pair). If pairing fails (wrong code, admin unreachable, refused), the legacy files are moved back and the PC stays legacy; the wizard lets the user try another code. The code format and device name are checked before anything is moved.
  3. **Read-only legacy DB** = the legacy files are copied to a temp dir and the copies opened with `query_only`, so not even a WAL checkpoint touches `legacy/<ts>/`.
  4. **Serial-number column** (`field_type='id'` or labels like "No.") is neither compared nor imported: it is per-PC (F12), so every matched client would otherwise show as a conflict. The PK column must have the same label on both sides and exactly one PK column must exist on each, otherwise `SalvageError` (stop and ask). Labels are matched trim + case-insensitive; a label that appears twice on either side is treated as unknown.
  5. **"Take this PC's values"** copies the legacy value for each differing column **only where the legacy value isn't empty**, so it never erases office data. Notes are compared too (label "Notes").
  6. **Ambiguous PKs** (the same PAN on two clients on one side) are listed and not imported, the same as missing PKs.
  7. **Additions beyond the spec's list:** inserted clients also get their `client_services` (mapped by service name; unknown services are counted). Their token: the legacy token if the office doesn't use it, otherwise a D8 letter token (`<letter>-<n+1>`). The letter comes from pairing (stored in the state file) or, after a crash, from this PC's member record.
  8. **Client-id translation.** Audit, tracker and timeline rows get the office's id for their client (matched or inserted). Tracker/timeline rows whose client wasn't imported (no PK / ambiguous) are **skipped and counted**. Audit rows are still inserted, with `client_id` NULL, and counted. `service_id` is mapped by service name, else NULL.
  9. **dataset_key "recomputed after P3-6":** P3-6 isn't done, so it's recomputed with today's rule (database.py start-up recompute) using the *office* client id for the `CLI_<id>` fallback. The office side is compared against both its stored and freshly recomputed keys. When P3-6 changes the rule, `_dataset_key` in `sync_rejoin.py` must follow.
  10. **Report privacy (§0 rule 12):** the report and dialog show PAN (PK value), tokens, column labels and counts. Values for unknown labels and conflicting values are **not** printed (the spec says values for unknown labels are "listed"). Differing columns are listed by label only.
  11. **Not imported, only counted:** `client_raw_containers` notes/screenshot paths (P3-1: `client_container_notes` doesn't exist yet), `cell_formatting`. They remain in `legacy/<ts>/`.
  12. **Crash safety of the salvage:** one transaction for master.db (clients, values, services, audit), then one for rawPayload.db. A crash between them is repaired by running it again, because the salvage is idempotent (tested). The state file is removed only after a successful apply or "Don't import"; "Not now" asks again at the next start.
  13. **P2-6 note done:** `_backup_db_and_sidecars` now moves the sidecars first. If one can't be moved, the ones already moved go back and `SnapshotError` is raised before the DB is touched. (The close-before-unlink note in `download_snapshot` was fixed by the P2-7 session in the same tree.)
- Notes for later WPs:
  - Public API (`sync_rejoin`): `write/read/clear_rejoin_request`, `state_path`, `read_state`, `move_legacy_files`, `resume_interrupted_rejoin -> None|"rolled_back"|"pending_join"|"salvage_pending"`, `rejoin_office(app_dir, host, code, device_name, *, pairing_port=None, sync_port=None, on_progress=None) -> JoinResult`, `resume_download`, `legacy_hex_key`, `saved_legacy_hex_key`, `plan_salvage` / `apply_salvage(legacy_dir, legacy_hex, office_dir, office_hex, *, take_legacy=(), token_letter)` → `SalvageReport`, `salvage_after_rejoin(app_dir, *, dry_run, password=None, take_legacy=())`, `skip_salvage`, `finish_rejoin`, `write_salvage_report`. Errors: `RejoinError` ⊃ `SalvageError`, `LegacyPasswordNeeded`.
  - **P2-7:** the wizard asks for the admin PC's IP. It doesn't list PCs with open pairing (P2-7's Join wizard does). It works on real PCs only once the admin PC serves snapshots (P2-7's `AddWorkstationSession`). Pairing and download run on the UI thread with a wait cursor, the same limitation P2-6 noted.
  - **P3-6:** see deviation 9 (`_dataset_key`).
  - **P3-2 / whoever creates `client_container_notes`:** extend the salvage to import those notes (deviation 11).
  - Not exercised: two real PCs, a real click-through, Windows Firewall.

### P2-7 — UI: Join wizard, Add workstation, Members, Remove, Hand over admin — Done — 2026-09-25
- Model: Claude Sonnet 5 (review by Claude Opus 5.5)   Commit: d8af4c1
- Tests: new `tests/test_sync_office.py` (12) and `tests/test_sync_office_ui.py` (8, including two that emit signals from a real background thread and pump the Qt event loop to prove cross-thread delivery, not just call the slot directly), plus 5 new `hand_over_admin` tests in `tests/test_sync_admin.py`. Full suite: 1279 passed / 16 failed / 3 skipped (the 10 documented pre-existing failures + the 6 P3-0 convergence tests expected to fail until P3-3..P3-5 land); nothing else.
- New module `sync_office.py` (no PySide6 import): `create_new_office`, `validate_new_office`, `join_office` (P2-4 pairing + P2-6 snapshot download in one call) and `AddWorkstationSession` (runs the admin-side `PairingWindow` *and* a mutual-TLS snapshot server -- P2-6 shipped with nothing serving snapshots, see its own notes). Added `sync_admin.hand_over_admin` (no-password admin transfer between two PCs that already hold the admin key material, mirroring `claim_admin`'s existing role-swap logic).
- First-run dialog (P0-6) gained two new pages: office-mode "New Office" and "Join Office" (pairing code), wired as the choice page's default buttons; the legacy no-office-key pages are kept as a de-emphasised "Advanced" fallback (their own P0-6 tests still pass unchanged). Sera Sync panel gained an "Office Members" section (table with Name/Online/Role/Last successful sync/This PC, Add workstation, Remove, Hand over admin, Become admin, unreachable-member warnings from P2-5's `get_unreachable_member_warnings`), visible only once `key_id` is set (office mode). Wired a v3 `DiscoveryService` into `main.py` for office-mode PCs (`listen=False`, shared beacon socket via `sync_peer.on_v3_beacon`, per P2-5's own design note for this WP), threaded through to the panel so "Add workstation" can advertise its pairing window.
- Two review rounds found real bugs, both fixed and covered by tests before this entry:
  1. **Blocking (round 1): start-up never read `office_mode`.** After "New Office"/"Join Office" (pairing) closed the dialog, `main.py`'s legacy branch still ran `first_run_dlg.master_password or self._get_master_password()`, which derives a key from `sera.salt` -- useless for an office-key database. Fixed: `_resolve_encryption_key` now checks `first_run_dlg.office_mode` and, if set, calls itself again so it takes the office-mode branch it just wrote the keys for.
  2. **Blocking (round 1): `QTimer.singleShot(0, fn)` called from a non-GUI thread never fires.** `PairingWindow`'s `on_joined`/`on_closed` (sync_pairing.py) and the join-wizard's scan/progress/done callbacks all run on background worker threads; `QTimer.singleShot(0, fn)` creates the timer on *that* thread, which has no Qt event loop, so the callback is silently dropped (confirmed by the reviewer actually running it). Fixed by replacing every such call with a Qt `Signal` defined on the dialog and connected in `__init__` (`workstation_joined_signal`/`workstation_closed_signal` on `SeraSyncDialog`; `office_peer_found_signal`/`office_scan_finished_signal`/`office_join_progress_signal`/`office_join_done_signal` and, since the *same* bug already existed in the P0-6 legacy join page sharing this file, `legacy_peer_found_signal`/`legacy_scan_finished_signal`/`legacy_join_status_signal`/`legacy_fetch_done_signal` on `FirstRunDialog`) -- Qt queues a signal emitted from a different thread than the receiver's onto the receiver's own event loop automatically. New tests emit from a real `threading.Thread` and pump `QApplication.processEvents()` until delivery, rather than just calling the slot directly (which would pass even with the old broken code).
  3. **Round 2 (self-caught before the reviewer's next pass): a copy/paste slip left the "Online" table cell computation followed by a stray, unreachable copy of `_refresh_members`'s tail (the network-warning label and the four button `setEnabled` calls) sitting *after* `_online_status`'s own `return` statements** -- valid Python, but that tail never ran, so warnings and button-enabled state silently stopped updating. Moved back into `_refresh_members`, where `test_members_panel_visible_in_office_mode_and_populates` now covers it. The same round also found a test that could hang an unattended run: `office_join_done_signal.emit(False, ...)` invokes the real (also-connected) `_on_office_join_done`, which pops an actual `QMessageBox.warning` -- fixed by patching `QMessageBox.warning` out for that test.
- Deviations from spec:
  1. **An interrupted "New office" leaves the PC in office mode with no database.** `sync_admin.init_office_membership` needs `office.json`'s `admin_pubkey` to sign its own first records (it calls `load_admin_key`, which reads `office.json`), so `office.json` has to be written *before* `master.db` is created here -- the opposite order from `sync_pairing._install` / `sync_migrate.migrate_to_office_key`, which write it last because everything they name is already verified by then. If database creation then fails, `keys/office.json` exists with no `master.db` and no `pairing.json`, so `sync_snapshot.has_pending_join()` won't recognise it and offer the usual resume; the owner would need to remove `keys/office.json` (or restore a backup) and retry. Expected to be rare (both writes hit the same local disk moments apart). Documented in `sync_office.create_new_office`'s docstring.
  2. **"Hand over admin" really means "give up admin until Phase 3."** `hand_over_admin` only signs a new `office_admin` record in *this PC's own* local database; there is no sync engine yet (P3-5) to push that record to the PC being named, so nobody can add or remove workstations until that PC's owner runs "Become admin" there with the office master password. The old admin PC's `_check_admin`/`_require_named_admin` checks (P2-2, already-tested behaviour) refuse it the instant the record is signed, regardless of whether `reconcile_admin_key` has run. The confirmation dialog now says this plainly instead of implying the hand-over is immediate.
- Notes for later WPs:
  - Public API in `sync_office.py`: `create_new_office(app_dir, office_name, password, device_name) -> sera_keys.OfficeInfo`, `validate_new_office(...) -> str | None`, `join_office(app_dir, host, code, device_name, *, port=PAIRING_PORT, sync_port=SYNC_PORT, on_progress=None) -> sync_pairing.JoinResult`, `AddWorkstationSession(app_dir, open_db, admin_device_id, own_cert_pem, *, on_joined=None, on_closed=None, sync_host="0.0.0.0", sync_port=SYNC_PORT, **pairing_kwargs)` with `.start()`, `.close(reason)`, `.sync_port` (the bound port, useful when `sync_port=0`). `AddWorkstationSession` keeps its snapshot server up for `JOIN_DOWNLOAD_GRACE_SECONDS` (120s) after a successful pairing close, since pairing closes the window the instant the joiner is admitted but the joiner's snapshot download is a separate step that starts right after.
  - **P3-5:** once real sync sessions exist, "Hand over admin" should notify the new admin PC directly (or that PC's own start-up/sync-session code should notice an `office_admin` record naming it and surface "You are now the office admin" / auto-run the DPAPI side of claiming, still gated on the master password per D3).
  - **Members panel "Online" column** is beacon-sighting recency only (`DiscoveryService.get_beacon_sighting`, 30s = 3x the 10s beacon interval), not a live session check -- P3-5 should replace it with real connectivity once sessions exist.
  - Not exercised: two real PCs, a real click-through (the spec's own Accept line for this WP), Windows Firewall on 49158/49159 from a genuinely fresh install.

### P2-8 — review fixes — 2026-09-24
- Model: Claude Opus 5.5 (review by Claude Opus 5.5)   Commit: uncommitted
- Fixed from the review:
  1. **Blocking B1: a kept numeric token could be created again by `add_client`.** `add_client` makes each token from the row id (`str(id)`), so a salvaged client keeping `57` in an office with ids 1–50 would share its token with row 57 later. `_token` now keeps a numeric old token only if it is at or below the office's highest client id ever handed out (`max(sqlite_sequence.seq, max(id))`) and unused. Otherwise the client gets this PC's letter token. Non-numeric tokens are kept if unused, except letter-shaped ones (`^[A-Z]+-\d+$`): those belong to another PC's D8 range, so they are replaced too.
  2. **Should-fix 1: archived duplicates.** The app enforces a unique PK only among active clients. When several clients share a PK and exactly one of them is active, the salvage now uses that one: on the office side it's the match; on this PC's side it's imported or compared, and the archived copies are listed as "archived duplicate" and not imported. Two or more active clients with one PK are still ambiguous.
  3. **Should-fix 2:** `sync_snapshot._backup_db_and_sidecars` now puts the DB move in the same rollback as its sidecars. If the DB itself can't be moved, the sidecars go back.
  4. **Should-fix 3: a PC stuck at "pending_join".** New `sync_rejoin.undo_rejoin(app_dir)`, allowed only while master.db is missing. It renames `keys/office.json` and `incoming/join/pairing.json` to `*.bak-<ts>` (office.json first, so a crash mid-undo is finished as "rolled_back" at the next start), then puts the legacy files back. The other key files stay; a later join backs them up. The start-up prompt now offers "Finish now" / "Undo rejoin" / "Close Sera", and a failed download, both in the wizard and at start-up, asks whether to undo instead of just closing.
- Tests: `tests/test_sync_rejoin.py` now 50 (was 35). `test_free_legacy_token_is_kept` (token `977`, which B1 says must not be kept) was replaced by `test_free_numeric_token_at_or_below_office_ids_is_kept` and `test_numeric_token_above_office_ids_gets_a_letter_token`. New: `test_add_client_after_salvage_never_duplicates_a_token[3|5|977]` (salvage, then 8× `add_client` past the old token; all tokens distinct), `test_non_numeric_token_is_kept_but_not_another_pcs_letter_token`, `test_letter_shaped_legacy_token_is_replaced`, `test_office_archived_duplicate_matches_the_active_client`, `test_legacy_archived_duplicate_imports_only_the_active_client`, `test_two_active_office_clients_with_one_pan_stay_ambiguous`, `test_undo_rejoin_restores_legacy_files_and_keeps_office_json_as_backup`, `test_undo_rejoin_refused_once_the_office_db_is_installed`, `test_crash_during_undo_is_finished_at_next_start`, `test_pending_join_dialog_offers_undo`, `test_failed_download_at_start_can_be_undone`, `test_snapshot_install_puts_sidecars_back_when_the_db_cannot_be_moved`. `test_sync_rejoin.py` + `test_sync_snapshot.py`: 61 passed. Full suite (after this round): 1273 passed / 16 failed / 3 skipped — the 10 pre-existing (dom_page_replace, gst_dom_tracker, raw_payload_db_and_srpf, updater, vsdc_beeper, vsdc_gemini_enricher x5) and the 6 expected P3-0 convergence tests; nothing else. (P2-7's dialog work had settled by then, so the earlier SeraSyncDialog and icon failures are gone.)
- Deviations from spec: `undo_rejoin` is an addition. Item 1 means some salvaged clients get a new letter token where the P2-8 entry said the old token is kept.
- **For the owner (design, outside P2-8; from the review):** each PC salvages on its own in Phase 2, before anything replicates. If two legacy PCs both have a client (same PAN) that the admin PC doesn't, each inserts its own copy. P3-2 gives the copies different gids, and after Phase 3 the office has two copies of that client (D2/P3-4 merge on a natural key only for `services`/`staff_users`). Old numeric tokens can collide between PCs the same way. Decide before P3-2: either merge clients on the PK column during apply, or salvage only on the admin PC (e.g. by sending each legacy DB to it).

### P2-8 — review fixes, round 2 — 2026-09-24
- Model: Claude Opus 5.5 (re-review by Claude Opus 5.5: no blocking problems; reviewer recorded `--reviewed-by`)   Commit: uncommitted
- Fixed from the re-review:
  1. **Undo after a partial snapshot install.** The snapshot installs rawPayload.db before master.db. If master.db failed, `undo_rejoin` found rawPayload.db both live and in `legacy/<ts>/`, and `_restore_legacy_files` refused on every start. `undo_rejoin` now first renames any office files left in the app folder (rawPayload.db and sidecars, master.db sidecars, anything named in the state's `moved` list) to `*.bak-<ts>`, then office.json / pairing.json, then restores.
  2. The `sqlite_sequence` expression that had been joined onto one line is split again.
  3. `_token` uses `isascii() and isdigit()`, so a token like `²` is treated as non-numeric instead of making `int()` fail and undoing the whole import.
- Tests: 2 new (`test_undo_after_a_partial_snapshot_install`, `test_non_ascii_digit_token_does_not_abort_the_import`); `tests/test_sync_rejoin.py` now 52. `test_sync_rejoin.py` + `test_sync_snapshot.py`: 63 passed. The full suite was not re-run after this round (last full run above, before these three small changes).
- Deviations from spec: none beyond the entries above.
- **Owner decision (2026-09-24): merge clients on the internal PK column (PAN) in Phase 3**, not "salvage only on the admin PC". Reason: two PCs can also add the same new client while offline once Phase 3 is live, so a PK merge is needed anyway; sending each legacy DB to the admin PC would move portal credentials over the network and still not cover that case. Not yet folded into §5/§7 (a Fable/Opus "fold approved deviations" session does that). What it means for later WPs:
  - **P3-1 / P3-2:** `clients` gets a natural-merge rule on the internal-PK value (normalised upper + trim), like `services`/`staff_users` (`natural_merge_on`), with the lexicographically smaller gid winning.
  - **P3-4:** when an incoming client insert has the same PK value as an existing *active* client with another gid, merge the two: keep the winning gid, repoint `client_values` / `client_services` / `cell_formatting` / tracker / timelines / audit references from the losing gid, merge field values by the normal LWW rule, and tombstone the losing gid as "merged into <gid>", not as a delete, so its later edits are redirected instead of written to `_sync_conflicts`. Archived clients never take part in a merge (the app allows an archived and an active client with one PAN).
  - **Tokens (D8):** after a merge the winning row keeps its token and the losing token is recorded in the report / audit so staff can still find it. P2-8 already avoids new collisions (numeric tokens only at or below the office's max id, otherwise a letter token).

---



## 10. Doc changelog

- **1.0** (2026-09-23): initial blueprint.
- **1.1** (2026-09-23): owner answers. SUDR ignored (D10). All settings office-wide (D7). Per-PC letter tokens (D8). Restore propagates to all PCs (D9, P4-3 split into P4-3a/b). Gemini allowed on T1/T2. Agent workflow (§8), progress log (§9), agent table xlsx.
- **1.2** (2026-09-23): any PC in admin mode can change settings; `app_settings` is plain `lww`, not admin-signed (D3, D7, P2-2, P3-1 updated). Staff roster stays admin-PC-signed.
- **1.3** (2026-09-23): agents update the xlsx only through `tools/sync_v3_tracker.py` (rule 14; §8.1–§8.4 prompts updated). Two phase checks added for the settings / staff rules.
- **1.4** (2026-09-24): owner decision: the P0-9a firewall rule also covers the **Public** profile (`profile=private,domain,public`), so sync isn't silently blocked on PCs whose network is classified Public. Accepted risk: until Phase 2's mutual TLS, the legacy sync ports (HMAC-authenticated since P0-7) are also reachable on untrusted Public networks (hotel/café Wi-Fi). The P0-9b Public-network warning and its activity-log entry stay; only their wording changed, from "Windows Firewall may block LAN sync discovery" to a security note ("Sera Sync is reachable by other devices on this network … set it to Private"). `tests/test_installer_firewall.py` updated to the new rule.
- **1.5** (2026-09-24): status moved out of the xlsx into `docs/sera-sync-v3-status.csv` / `-checks.csv` (written by `tools/sync_v3_tracker.py`, same commands). The xlsx is now a read-only viewer that pulls the CSVs in with Power Query, so it can stay open while agents work (refreshes on open, every minute, or Ctrl+Alt+F5). New `tools/build_sync_v3_tracker.py` regenerates `sera-sync-v3-plan.json` + the viewer from §3 and keeps progress. Rule 14 and §8 updated. "Ready?" now only says Ready for WPs not yet started.
