# Operations & LAN Database Synchronization (Sera Sync v3)

Sera Sync keeps the office's data the same on every workstation. It works only on the local network: no server, no internet, no cloud relay. Since Sera Sync v3 it copies **changes**, field by field, never whole database files. The full design is in [sera-sync-v3-blueprint.md](sera-sync-v3-blueprint.md).

## 1. The office

### 1.1 Office key, devices and the admin PC
- **Office key.** One random key encrypts every Sera database in the office (`master.db`, `rawPayload.db`). Each PC keeps it in `keys/office_key.dpapi`, protected by Windows (DPAPI), so Sera starts without a password prompt. A copy encrypted with the **master password** (`keys/office_key.recovery`) is used when Windows can't unlock the key (profile reset, new Windows account) and for the recovery kit.
- **Recovery kit.** Sera Sync panel → **Export recovery kit**. Keep it on a USB stick somewhere safe. If the master password is forgotten *and* no PC can unlock its key, the data can't be recovered: that is by design.
- **Devices.** Each PC has its own certificate (`keys/device_cert.pem`). Only PCs on the office's members list can connect; all sync traffic uses TLS with both sides showing their certificate.
- **Admin PC.** One PC keeps the final say over the staff roster and the members list, and is the only PC that can restore a backup for the whole office (§4). Anyone with the master password can move the role: **Hand over admin** on the admin PC, or **Become admin** on another PC.
- **Settings are office-wide.** Every setting (including theme, window mode and the admin PIN) applies to every PC. Any PC in admin mode can change them; the latest change wins.
- **Client tokens** made on each PC carry that PC's letter (`A-12` on the admin PC, `B-7` on the next, ...), so two PCs never hand out the same token. Older numeric tokens keep their numbers.

### 1.2 A new PC joins
1. Install Sera on the new PC and choose **Join office** on the first screen.
2. On the admin PC: **Admin → Sera Sync → Add workstation**. It shows a 6-digit code for 5 minutes.
3. Type the code on the new PC. A wrong code three times closes the window. The new PC receives the office key and the members list, downloads a copy of the office data and starts. It never creates default data of its own.

A brand-new office starts with **New office** on the first screen (choose the master password there).

### 1.3 Removing a PC, rejoining
- **Remove** (admin PC, Members list): the PC can no longer connect. Its letter is never reused.
- **Rejoin office**: for a PC whose data went its own way (a copy from before the office key, or a damaged install). It pairs again with a code, keeps its old files aside and offers to import the clients only it has.

## 2. How changes travel

- **Capture.** Every save to a synced table is recorded (database triggers), including writes by the parsers. After each save the change is numbered in this PC's own stream and stamped with a hybrid logical clock (HLC).
- **Merge rules.** Client data merges **per field**: two people editing different fields of the same client both keep their edits; for the same field the later edit wins everywhere. A delete wins over an edit it didn't see; the lost edit goes to the **conflicts list**. Two PCs that create the same client (same PAN) end up with one client. Staff roster changes are accepted only when signed by the admin PC.
- **Sessions.** Every 20 seconds (and within a second after a local save) each PC opens a session with each member it can reach. The two PCs compare what they hold and send each other only what's missing, including changes they got from third PCs, so a change from PC A reaches PC C through PC B even if A and C never meet. Each batch is applied in one transaction, so an interrupted session simply continues next time.
- **No file replacement while Sera runs.** Database files are only replaced when a PC joins, or at start-up from a staged copy (go-live, restore, catch-up).

### 2.1 Finding the other PCs
- Beacons on **UDP 49156** every 10 seconds, to `255.255.255.255` and each network adapter's broadcast address.
- PCs tell each other the addresses they know (address gossip), so a Wi-Fi PC learns a wired PC's address from anyone who knows it.
- **Add PC by IP** (Sera Sync panel) for a PC on another subnet or behind a router that blocks broadcasts. **Remove PC by IP** removes the entry.

### 2.2 Ports and firewall

| Port | Use |
|---|---|
| UDP 49156 | Beacons and "sync now" pokes |
| TCP 49158 | Pairing, open only while **Add workstation** is open |
| TCP 49159 | Sync sessions and snapshots (mutual TLS, members only) |
| TCP 49152 | Browser extension bridge (not sync) |
| TCP 49157 | Old v2 protocol; nothing uses it any more |

The installer opens these ports for `Amas_Sera.exe` on Private, Domain and Public networks. The Sera Sync panel warns when the PC is on a **Public** network: switch it to **Private** in Windows (Network & Internet → Properties).

### 2.3 Keeping the change log small (compaction)
Each PC keeps the changes it has sent or forwarded until every active member has them, then deletes them (every 6 hours). A member that hasn't been seen for 60 days no longer holds this up; deleted-row markers are kept for 180 days. A PC stops compacting when it hasn't seen any other member for 60 days itself, so it never drops its own unsent changes.

**A PC that was away for a long time** (switched off for months, say) may find that the others no longer keep the changes it missed. When that happens it first sends its own changes, then downloads a fresh copy of the office data from that PC. A message asks you to **restart Sera**; at the restart the copy is installed and this PC's own changes are kept. The replaced files are kept in `backups/pre-catchup-<date>/`.

## 3. The Sera Sync panel (Admin → Sera Sync)

- **Members / online PCs**: last successful sync per PC, and warnings: "PC <name> needs updating" (different Sera version), a PC whose clock is ahead, a Public network.
- **Status line**: sync mode, parked changes (waiting for something they refer to) and their age, stuck streams.
- **Conflicts list**: edits that lost to a delete. **Keep** accepts the result; **Use discarded value** writes the lost value back (not possible for a deleted row).
- **Add workstation**, **Remove**, **Hand over admin** / **Become admin**, **Rejoin office**, **Add PC by IP** / **Remove PC by IP**, **Export recovery kit**.
- The shadow-mode buttons (**Start shadow mode**, **Reset shadow mode**, **Run now**, **Go live**) were for the switch-over to v3 and are inactive on a PC that is live.

## 4. Backups and restore

### 4.1 Backups
- **Daily**: at the first idle moment after 13:00, both databases are copied into `backups/daily-<date>/`; the newest 14 are kept.
- **Automatic** extra backups right before a restore (`backups/pre-restore-<date>/`) and before go-live.
- **Manual**: Admin → Settings → Backup & Restore → Backup Database → **Choose Backup Folder**.
A backup folder holds `master.db`, `rawPayload.db` and `backup_manifest.json`. It is encrypted with the office key.

### 4.2 Restoring a backup for the whole office
A restore makes the backup **the data on every PC**.

1. On the **admin PC** (or after **Become admin**): Admin → Settings → Backup & Restore → **Restore from Backup**, and choose the backup folder.
2. Sera shows what will happen: how many clients come back, how many created since the backup will be removed, and how many fields revert. Type `RESTORE` to continue.
3. Sera backs up the current data, then restarts and does the restore at start-up. The other PCs follow at their next sync.

Good to know:
- Settings (including the admin PIN) go back to the backup's values too.
- Edits made anywhere **after** the restore win as usual.
- A PC that is **offline** during the restore keeps any **new** clients it created that the admin PC never saw; its edits to restored clients are lost, because they are older than the restore.
- The audit log is never shortened: entries written since the backup stay. The restore itself is logged (`office_restore`).
- Only complete backups made since the office moved to Sera Sync v3 can be restored this way. The replaced database files are kept in `backups/replaced-by-restore-<date>/`.

## 5. Things that are not supported

> [!WARNING]
> **Do not use Syncthing, Dropbox, OneDrive, Google Drive or any file-copy tool on the Sera data folder.**

Copying live database files while Sera runs can corrupt them, and a copied file brings another PC's sync state with it. Sera Sync is the only supported way to share data between PCs. To move data by hand, use a backup and **Restore from Backup** (§4.2).

## 6. Files on disk

```text
~/AmanAssociates_Sera/
|-- master.db              office data (encrypted with the office key; includes the _sync_* tables)
|-- rawPayload.db          tracker data (same key, its own _sync_* tables)
|-- keys/
|   |-- office.json        office id, name, key id, admin public key, this PC's device id (not secret)
|   |-- office_key.dpapi   the office key, protected by Windows
|   |-- office_key.recovery  the office key, protected by the master password
|   |-- device_cert.pem, device_key.pem, device_key.pass.dpapi   this PC's identity
|   |-- admin_key.dpapi    admin PC only
|   |-- admin_key.recovery
|   `-- sync_seq.json      highest change number this PC has issued
|-- incoming/              staged downloads and swaps (join, go-live, restore, catch-up)
|-- shadow/                files from the v3 switch-over (shadow week, go-live)
|-- backups/               daily, pre-restore, pre-go-live, replaced files
`-- logs/                  sync_shadow.log and other logs
```

Old installs may still have `sera.key` / `sera.salt` (the pre-v3 password key). Nothing uses them any more except **Rejoin office**, which can read this PC's own old data to import it.

## 7. Workstation identity

Each workstation has a display name (`device_identity.txt`), used in the audit log for credential access, autofill and submission tracking. The name a PC shows in the Sera Sync panel is the one given when it joined.
