# Setup

Use these steps for local development or first-time app setup.

## Install Dependencies

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

The Playwright browser install can be skipped if the deployment only uses extension or Edge-channel automation.

## Run

```bash
python main.py
```

## First Run

On first launch, the app asks for:

- Staff label/name, used for audit attribution.
- Master password, minimum 8 characters.

The master password is never stored. It is re-derived into the database encryption key every time the app starts using PBKDF2 with 480,000 iterations and the local `sera.salt`.

Every employee must use the same master password because all synced machines need to open the same encrypted `master.db`.

## Admin PIN

The Admin PIN is separate from the master password. It is set the first time someone clicks Admin Mode.

The Admin PIN gates:

- Client CRUD screens.
- Backup restore.
- CSV export.
- Audit log viewer.
