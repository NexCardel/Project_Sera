# Operations & LAN Database Synchronization

## 1. Sera Sync (Built-In Zero-Configuration LAN Sync)

Project Sera includes **Sera Sync** (`sync_peer.py`), a built-in peer-to-peer (P2P) database synchronization service that operates over your local network (LAN) without requiring external servers or third-party software.

### How Sera Sync Works:
- **Automatic Peer Discovery**: Broadcasts UDP beacons on **Port 49156** every 5 seconds. All devices on the LAN running Project Sera discover each other automatically.
- **Sera Sync Panel**: Admins can open the **Sera Sync** dialog (**Admin → Sera Sync** or profile row click in Admin mode) to see real-time online workstations, hostnames, and IP addresses.
- **One-Way Database Push**: Selecting a workstation and clicking **"Sync Database To Selected"** sends a copy of `master.db` and `sera.salt` directly over TCP (**Port 49157**).
- **Auto-Accept & App Locking**: The receiving workstation automatically accepts the database push, locks its user interface immediately, displays a top-level mandatory restart dialog, and cleanly auto-restarts (`os.execl`) into the new database.
- **No Shared Password Setup Needed**: `master.db` and `sera.salt` are transferred together as a matched pair, so teammates do not need to configure pre-matched master passwords before syncing.

---

## 2. Syncthing Support & Conflict Resolution

Project Sera stores its runtime database files in:

```text
~/AmanAssociates_Sera/
|-- master.db
`-- sera.salt
```

If your organization uses Syncthing alongside or instead of Sera Sync:
- Point Syncthing at `~/AmanAssociates_Sera/`.
- Both `master.db` and `sera.salt` must sync together.

### Conflict File Auto-Resolver:
When Syncthing generates conflict files (`master.sync-conflict-*.db` / `sera.salt.sync-conflict-*`) or `sync_peer` saves conflict backups (`master.db.conflict-*`):
- `restore_from()` in `database.py` automatically scans, matches, and decrypts candidate database and salt pairs.
- Validates SQLCipher HMAC decryption (`SELECT count(*) FROM sqlite_master;`) before applying the restore.
- Admin Mode **Restore DB** allows picking either a backup folder OR a specific conflict file directly.

---

## 3. Database Restores

Restoring a database in Admin Mode overwrites the local `master.db` and `sera.salt`. Upon a successful restore, the app displays a confirmation message and automatically restarts to re-authenticate SQLCipher and reload all services.

---

## 4. Workstation Identity & Audit Attribution

Each workstation prompts for a display name / user label on first launch (`device_identity.txt`). That label is used in the Audit Log for credential access, portal autofill events, and return submission tracking.
