# Operations & LAN Database Synchronization

## 1. Sera Sync (Built-In Zero-Configuration LAN Sync)

Project Sera includes **Sera Sync** (`sync_peer.py`), a built-in peer-to-peer (P2P) database synchronization service that operates over your local network (LAN) without requiring external servers or third-party software.

### How Sera Sync Works:
- **Automatic Peer Discovery**: Broadcasts UDP beacons on **Port 49156** every 5 seconds. All devices on the LAN running Project Sera discover each other automatically, sharing version numbers, database timestamps, and structural revision scores.
- **Dual Sync Engine (Initial & Live Sync)**:
  - **Initial Sync**: When full database and salt pair are transferred to an un-synced node, creating a pre-sync safety backup (`master.db.pre-sync-*`), locking the UI, and cleanly auto-restarting (`os.execl`) into the new database.
  - **Live Sync**: Lightweight background syncing after initial pairing that updates open UI windows (Search Table, Admin, Client Detail) in real time without app restart.
- **`inv_frames` Protocol (Invincibility Frames / Sovereign Node)**:
  - **Sovereign Master (`inv_frames = ON`)**: A node with `inv_frames` enabled **rejects all incoming data, pushes, or pulls** from any other node, but can freely push its own database to nodes across the LAN.
  - **Single Authority**: When only 1 node has `inv_frames` ON, normal nodes follow this authority and accept its database pushes. Normal nodes cannot push to each other while an authority is active.
  - **Multi-Node Freeze (>1 `inv_frames`)**: If more than one node on the LAN enables `inv_frames`, sync across the entire LAN is immediately paused (frozen) to prevent split-brain collisions and data corruption.
  - **Zero `inv_frames`**: Normal bidirectional P2P sync operates between all nodes with Sync Guard revision protection.
- **Sera Sync Panel & Live Activity Stream**:
  - Admins can open the **Sera Sync** dialog (**Admin → Sera Sync**) to toggle `inv_frames`, inspect online workstations, review revision scores (`Rev Score`), and monitor real-time sync events with colored badges (`🛡️ INV_FRAMES`, `📊 REVISION`, `🟢 BEACON`, `📥 PULL`, `📤 PUSH`).

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
