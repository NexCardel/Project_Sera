# Operations and Sync

## Syncthing

Project Sera stores its shared runtime files in:

```text
~/AmanAssociates_Sera/
|-- master.db
`-- sera.salt
```

Both files must sync together. `master.db` is the encrypted database, and `sera.salt` is required for key derivation.

Every employee should point Syncthing at the same `AmanAssociates_Sera` folder.

For detailed setup, see `../docs/Syncthing_Setup_Guide.md`.

## Restores

Restoring a database in Admin Mode overwrites the local `master.db` and `sera.salt`.

If Syncthing is running, the restored version will sync to other staff PCs. Pause teammate Syncthing clients before performing a database restore when the restore should not immediately propagate.

## Audit Attribution

Each employee enters their staff name on first launch. That name is used in the Audit Log for credential access, portal autofill events, and return submission tracking.
