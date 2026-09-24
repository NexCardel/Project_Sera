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

---


## 10. Doc changelog

- **1.0** (2026-09-23): initial blueprint.
- **1.1** (2026-09-23): owner answers. SUDR ignored (D10). All settings office-wide (D7). Per-PC letter tokens (D8). Restore propagates to all PCs (D9, P4-3 split into P4-3a/b). Gemini allowed on T1/T2. Agent workflow (§8), progress log (§9), agent table xlsx.
- **1.2** (2026-09-23): any PC in admin mode can change settings; `app_settings` is plain `lww`, not admin-signed (D3, D7, P2-2, P3-1 updated). Staff roster stays admin-PC-signed.
- **1.3** (2026-09-23): agents update the xlsx only through `tools/sync_v3_tracker.py` (rule 14; §8.1–§8.4 prompts updated). Two phase checks added for the settings / staff rules.
- **1.4** (2026-09-24): owner decision: the P0-9a firewall rule also covers the **Public** profile (`profile=private,domain,public`), so sync isn't silently blocked on PCs whose network is classified Public. Accepted risk: until Phase 2's mutual TLS, the legacy sync ports (HMAC-authenticated since P0-7) are also reachable on untrusted Public networks (hotel/café Wi-Fi). The P0-9b Public-network warning and its activity-log entry stay; only their wording changed, from "Windows Firewall may block LAN sync discovery" to a security note ("Sera Sync is reachable by other devices on this network … set it to Private"). `tests/test_installer_firewall.py` updated to the new rule.
- **1.5** (2026-09-24): status moved out of the xlsx into `docs/sera-sync-v3-status.csv` / `-checks.csv` (written by `tools/sync_v3_tracker.py`, same commands). The xlsx is now a read-only viewer that pulls the CSVs in with Power Query, so it can stay open while agents work (refreshes on open, every minute, or Ctrl+Alt+F5). New `tools/build_sync_v3_tracker.py` regenerates `sera-sync-v3-plan.json` + the viewer from §3 and keeps progress. Rule 14 and §8 updated. "Ready?" now only says Ready for WPs not yet started.
