# Operations & LAN Database Synchronization

## 1. Sera Sync (Built-In Zero-Configuration LAN Sync)

Project Sera includes **Sera Sync** (`sync_peer.py`), a built-in peer-to-peer (P2P) database synchronization service that operates entirely offline over your local area network (LAN) without requiring external servers, third-party software, or cloud relays.

### 1.1 Architecture & Multi-Adapter Discovery
- **Automatic Multi-Adapter Discovery**: Broadcasts UDP beacons on **Port 49156** every 5 seconds to `255.255.255.255` as well as to directed broadcast addresses across all active network adapters (skipping loopback and link-local interfaces).
- **Hostname-Keyed Peer Tracking**: Workstations are identified by host name. When dynamic DHCP assigns a new IP address to a workstation, Sera Sync seamlessly updates the existing workstation entry without creating duplicate or ghost records.
- **Cross-Subnet & Wi-Fi Discovery (Add PC by IP)**:
  - In office environments spanning multiple subnets, VLANs, or Wi-Fi routers where UDP broadcast packets are filtered or blocked, administrators can use **Add PC by IP** in the Sera Sync panel (**Admin → Sera Sync**).
  - Configured manual peer addresses are stored in office-wide settings (`sync_manual_peers`).
  - Sera Sync periodically sends unicast beacons (every 10 seconds) to each manual peer (skipping the workstation's own addresses), and receiving peers respond with unicast beacons back.
  - Manual peer entries can be removed at any time via the **Remove PC by IP** button or by right-clicking a workstation row in the Sera Sync peer table.
- **Network Security & Public Profile Warning**:
  - The Project Sera Windows installer registers firewall rules for `Amas_Sera.exe` covering Private, Domain, and Public network profiles.
  - However, running on an untrusted **Public** network (e.g. coffee shop, hotel Wi-Fi) is insecure and exposes LAN sync endpoints.
  - Sera Sync continuously monitors the active network category in the background (via Windows Network List Manager / PowerShell).
  - If any connected network profile is classified as **Public**, the Sera Sync panel displays a prominent warning banner advising the administrator to switch the network profile to **Private** via Windows Settings (**Network & Internet → Properties → Private network**).

### 1.2 First-Run Office Setup & Join Flow
When Project Sera is launched on a new computer without an existing database:
- **New Office**: Prompts for a secure office master password (minimum 8 characters; default/trivial passwords like `admin123` are rejected) and generates a fresh encryption salt and initialized database.
- **Join Existing Office**:
  1. The new workstation listens for LAN beacons for 10 seconds to discover active workstations, or allows entering a specific IP address.
  2. The joiner initiates an outbound connection to the chosen workstation, displaying a random **6-digit verification code** on its screen.
  3. The serving workstation displays an on-screen approval modal: *"Workstation `<host>` (`<username>`) wants to join. Code on their screen: `XXX XXX`. Allow?"* (times out automatically after 120 seconds if unapproved).
  4. Upon approval, the serving workstation exports a consistent snapshot and streams the encrypted database and salt to the joiner's `incoming/` staging folder.
  5. The joiner prompts for the office master password, validates cryptographic integrity and table counts, and installs the database cleanly without requiring an application restart.

### 1.3 Database Snapshotting, Staging & Startup Swap
To guarantee database consistency and prevent corruption on SQLite/SQLCipher Write-Ahead Logging (WAL) databases:
- **WAL-Safe Snapshot Export**: The sending workstation never reads active database files directly from disk. Instead, it creates an encrypted point-in-time snapshot using `sqlcipher_export` into temporary staging and streams the snapshot in 1 MB chunks.
- **Pre-Validation & Staging**: The receiving workstation streams incoming database files into `incoming/` staging files and verifies cryptographic decryption and table integrity using the local master password before accepting.
- **Atomic Startup Swap**: Active database files are **never overwritten while the application is running**. Accepted transfers write a pending swap manifest (`incoming/pending_swap.json`). On the next application launch, `apply_pending_swap()` creates pre-sync safety backups (`master.db.pre-sync-<timestamp>.db`), clears SQLite WAL/SHM sidecars, and atomically swaps the staged database into place before opening.
- **Message Authentication (HMAC)**: All sync requests over TCP (Port 49157) are signed and verified using HMAC-SHA256 derived from the office encryption key, enforcing timestamp freshness (within 120 seconds) and rejecting unauthenticated or tampered traffic.
- **Tracker Database Auto-Heal Alert**: If the secondary tracker database (`rawPayload.db`) cannot be opened due to key mismatch, it is safely backed up and reinitialized, generating a visible user notification and audit log entry.

### 1.4 `inv_frames` Protocol (Transitional — Stays Only Until v3)
- **Sovereign Master (`inv_frames = ON`)**: A node with `inv_frames` enabled **rejects all incoming database pushes or pulls** from any other node, but can push its own database to nodes across the LAN.
- **Single Authority**: When 1 node has `inv_frames` ON, normal nodes follow this authority and accept its database pushes. Normal nodes cannot push to each other while an authority is active.
- **Multi-Node Freeze (>1 `inv_frames`)**: If more than one node on the LAN enables `inv_frames`, sync across the entire LAN is immediately paused to prevent split-brain collisions and data loss.
- **Zero `inv_frames`**: Normal bidirectional synchronization operates between all nodes.
- **Transitional Status**: `inv_frames` whole-database authority is a transitional protocol used in Sera Sync v2.x and **stays only until Sera Sync v3**. In v3 (Phase 3+), it will be fully superseded by granular, field-level multi-master replication with hybrid logical clocks (HLC) and cryptographic administrator signatures.

### 1.5 Sera Sync Panel & Activity Stream
- Admins can open the **Sera Sync** dialog (**Admin → Sera Sync**) to toggle `inv_frames`, inspect online workstations, review revision scores (`Rev Score`), manage manual peers, and monitor real-time sync events with colored badges (`🛡️ INV_FRAMES`, `📊 REVISION`, `🟢 BEACON`, `📥 PULL`, `📤 PUSH`).

---

## 2. External File Synchronization (Syncthing / Cloud Sync Not Supported)

> [!WARNING]
> **Syncthing and third-party file synchronization tools are NOT supported on active database files.**

Project Sera stores its runtime database files in:

```text
~/AmanAssociates_Sera/
|-- master.db
|-- rawPayload.db
`-- sera.salt
```

Running Syncthing, Dropbox, OneDrive, Google Drive, or any file-copy synchronization software on active SQLite/SQLCipher databases in WAL mode while Project Sera is running can cause torn pages, un-checkpointed WAL loss, and irrecoverable database corruption. All local network synchronization must be handled exclusively through the built-in **Sera Sync** service.

### Historical Conflict File Restoration
For backward compatibility and disaster recovery from historical backups or legacy Syncthing conflict copies (`master.sync-conflict-*.db` / `sera.salt.sync-conflict-*`):
- `restore_from()` in `database.py` automatically scans, matches, and decrypts candidate database and salt pairs.
- Validates SQLCipher HMAC decryption (`SELECT count(*) FROM sqlite_master;`) before applying the restore.
- Admin Mode **Restore DB** allows selecting either a backup directory or a specific conflict `.db` file directly.

---

## 3. Database Restores & Backups

Restoring a database in Admin Mode replaces the database safely and re-authenticates on application restart. Upon a successful restore, the application displays a confirmation message and automatically restarts to re-authenticate SQLCipher and reload all services.

*(In Sera Sync v3, administrator-approved database restores will automatically propagate as the authoritative state to all paired office workstations.)*

---

## 4. Workstation Identity & Audit Attribution

Each workstation prompts for a display name / user label on first launch (`device_identity.txt`). That label is used in the Audit Log for credential access, portal autofill events, and return submission tracking.
