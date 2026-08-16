# Setup

Use these steps for local development or first-time app setup.

---

## Install Dependencies

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

The Playwright browser install can be skipped if the deployment uses Chrome/Edge extension channel automation.

---

## Run Application

```bash
python main.py
```

---

## First Run & Auto-Unlock

On first launch, Project Sera auto-derives and secures your vault using PBKDF2 with 480,000 iterations and the local `sera.salt`.

- **Keyfile Auto-Unlock**: The master key is stored in your local vault keyfile (`sera.key`). No login password prompt appears on startup — the app opens instantly into your workspace.
- **Workstation Identity**: Workstation identity defaults to your local hostname (`socket.gethostname()`) and is saved in `device_identity.txt` for audit attribution.

---

## Admin PIN

The Admin PIN (default `1234` or set by the user) is separate from vault decryption. It is set the first time someone accesses Admin Mode.

The Admin PIN gates:

- Admin Panel & Master Column List (MCL) configuration.
- Client Edit, Delete, and Service attachment actions.
- Backup creation and restore operations.
- CSV import/export tools.
- System Audit Log viewer.
- Application settings and autostart toggles.
