# Project Sera — Dead Code & Bloat Cleanup Guide

Findings are grouped by risk. Each item lists exactly what to check, then what to remove. Do them in order — some "safe" deletions depend on the caution items being handled first. Run the full app + `python -m unittest discover tests` after each group, not just at the end.

---

## ⚠️ Do this first — one file path is load-bearing

**`Version SKY/` (25 MB) looks like a scratch/design-inspiration dump, but it is not fully dead.**

Three live UI files resolve an icon at runtime from inside it:

```
ui/shell/slide_panel.py:23
ui/windows/admin_window.py:46
ui/windows/client_detail_window.py:37
```
```python
BACK_ICON = str(Path(__file__).resolve().parents[2] / "Version SKY" / "Sera_SVG" / "arrow_back_24dp_E3E3E3_FILL0_wght400_GRAD0_opsz24.svg")
```

This also means the packaged build is currently broken or fragile: `build_tools/Amas_Sera.spec` does **not** bundle `Version SKY/` (only `ui/`, `assets/`, `native_host/`, `sera_extension/`), so the back-arrow icon likely fails to load (or silently no-ops) in the installed `.exe`, even though it works fine when run from source.

**Steps:**
1. Move the single needed file into the shipped assets folder:
   ```
   Version SKY/Sera_SVG/arrow_back_24dp_E3E3E3_FILL0_wght400_GRAD0_opsz24.svg
   →  assets/icons/arrow_back_24dp_E3E3E3_FILL0_wght400_GRAD0_opsz24.svg
   ```
2. Update the three `BACK_ICON` lines above to point at `assets/icons/...` instead of `Version SKY/...`.
3. Confirm the back arrow still renders on Client Detail, Admin, and the slide-panel drawer.
4. **Now** delete the rest of `Version SKY/` — it's pure design scratch (old `APP.zip` backup build, 10 moodboard/inspiration screenshots, two blueprint `.md` docs, a stray `README.md`). Nothing else in the codebase references it.

This single fix removes ~25 MB of dead weight and fixes a latent packaging bug at the same time.

---

## 🟢 Safe to delete outright (verified zero references anywhere in code)

| Path | What it is | Verification |
|---|---|---|
| `scratch/generate_pdf.py`, `scratch/temp_arch.html` | Dev scratch files used to generate `docs/codebase-architecture-nodes.pdf` once, not imported by anything | `grep -r "generate_pdf" .` → only self-reference |
| `tests/test_scraper.py` | Not an actual test (no `TestCase`, no assertions) — a standalone `if __name__ == "__main__"` script for auto-detecting login-form selectors. Its function `auto_scrape_selectors` is never imported by `automation.py` or anything else — a dead prototype that was superseded by the extension's own injection logic | `grep -r "auto_scrape_selectors" .` → only defined in this file |
| Root `sera_extension.crx` and `sera_extension.pem` | Stray duplicates. The real signing key lives at `build_tools/sera_extension.pem` (that's what `build_extension.py` reads/writes), and the real packaged `.crx` output goes to `package_assets/extension/ProjectSeraCompanion.crx`. These two root-level copies aren't referenced by any script | `grep -r "sera_extension.crx\|sera_extension.pem" .` → only `build_tools/sera_extension.pem` referenced |
| `package_assets/extension/` (whole folder) | Build **output**, regenerated fresh each time `build_tools/build_extension.py` runs. Shouldn't be committed at all — add to `.gitignore` instead of tracking it | Only writer is `build_extension.py`; no reader elsewhere |

> ⚠️ Before deleting the root `.pem`: diff it against `build_tools/sera_extension.pem`. If they're *different* keys, someone may have manually re-signed the extension once using the root copy — in that case the extension ID staff already have installed was derived from whichever key was actually used for the last real release. Confirm which key matches the currently-deployed `ProjectSeraCompanion.crx` before removing either, or you'll silently change the extension ID on the next build and break `NativeMessagingHosts` registration for existing installs.

---

## 🟡 Dead code inside files you're keeping (safe, but edit carefully)

### 1. `database.py` — duplicate `backup_to()` method (first definition is unreachable)

```
Line 851:  def backup_to(self, dest_dir: str) -> str:   # ← dead, silently overridden
Line 1374: def backup_to(self, dest_dir: str) -> str:   # ← the one Python actually uses
```

Python keeps only the *last* method definition with a given name in a class body — the version at line 851 (37 lines, with the stricter "salt file missing" error message) is never called by anyone. `pyflakes` flags this as `redefinition of unused 'backup_to'`.

**Action:** Delete the block at line 851–874 (the first `backup_to`). Before deleting, check whether its extra validation (raising `DatabaseError` if the salt file is missing) is something you actually want — if so, port that check into the *surviving* version at line 1374 rather than losing it.

### 2. `database.py` — orphaned DRS-Dashboard-era methods

`docs/file-submission-tracker.md` already notes: *"The legacy DRS Engine UI components, DRS Dashboard window, and DRS Manager dialogs have been removed."* The dialog/window files are indeed gone, but their backing database methods were left behind and are called by nothing:

```
get_filing_types()                 (line 1601)
upsert_filing_type()               (line 1626)
set_client_filing_type_enabled()   (line 1655)
attach_client_filing_type()        (line 1666)
detach_client_filing_type()        (line 1669)
get_filing_status()                (line 1740)
get_client_filing_statuses()       (line 1758)
get_all_filing_statuses()          (line 1785)
get_dashboard_batch_data()         (line 1810)  — docstring literally says "for the DRS Dashboard"
```

**Keep** (actively used by the current FST flow):
```
set_filing_status()          — called from main.py:258
get_client_filing_types()    — called from ui/dialogs/filing_confirmation_dialog.py:84
```

**Action:** Delete the nine unused methods listed above (roughly lines 1601–1655 and 1740–1876 — check the exact boundaries in your editor since line numbers shift as you go). Leave `set_filing_status` and `get_client_filing_types` untouched — FST depends on them.

> Search the whole repo for each method name once more before deleting, in case a UI file outside what was in this review calls them dynamically (e.g. via `getattr`) — none did in this codebase, but it's a 10-second check.

### 3. `ui/windows/admin_window.py` — redundant top-level import

```python
# line 39, module level:
from ui.dialogs.audit_log_dialog import AuditLogDialog   # ← never used at module level

# line 705, inside _on_view_audit_log():
from ui.dialogs.audit_log_dialog import AuditLogDialog   # ← this local import is what's actually used
```

**Action:** Delete the module-level import at line 39. Leave the local import inside `_on_view_audit_log` — it looks like it was deliberately deferred (likely to avoid a circular import or speed up app startup), so keep that pattern, just drop the redundant top-level copy.

### 4. Unused imports (cosmetic, zero behavior risk, but worth a pass)

```
sync_peer.py           — sys, hashlib, pathlib.Path
version.py              — os
ui/shell/sidebar.py     — QPen
ui/dialogs/sera_sync_dialog.py   — QFont
ui/dialogs/loading_dialog.py     — QTimer, QFont
ui/dialogs/update_dialog.py      — QFont, QIcon
ui/windows/admin_window.py       — QIcon, QTableWidgetItem
ui/windows/client_detail_window.py — webbrowser, QIcon, QCheckBox, QComboBox, QFormLayout
ui/windows/search_window.py      — QPoint, QColor, QBrush, QFont (imported inside a function, unused there)
ui/utils/theme.py                — Qt
build_tools/build_extension.py   — shutil
```

**Action:** Run `pyflakes .` from the project root and delete each flagged import line. This is low-value but zero-risk cleanup — good candidate for a single mechanical commit, separate from the logic-affecting changes above so it's easy to review/revert independently.

---

## Suggested execution order

1. Relocate the SVG icon out of `Version SKY/` and repoint the 3 `BACK_ICON` paths (fixes a real packaging bug).
2. Delete `Version SKY/`, `scratch/`, `tests/test_scraper.py`.
3. Diff and resolve the root `sera_extension.crx`/`.pem` duplicates; delete the confirmed-stray one; `.gitignore` `package_assets/`.
4. Remove the dead `backup_to()` (line 851) from `database.py`, porting over its salt-missing check if wanted.
5. Remove the 9 orphaned DRS-dashboard-era methods from `database.py`.
6. Drop the redundant top-level `AuditLogDialog` import in `admin_window.py`.
7. Run `pyflakes .` and clean up the unused-import list as a separate, easy-to-review commit.
8. Full regression pass: launch the app, open Client Detail (check back arrow), run Admin → Backup, run Admin → Restore, trigger a File Submission Tracker autofill + confirmation flow, then `python -m unittest discover tests`.

Nothing in this list touches `automation.py`, the extension (`sera_extension/`), `sync_peer.py`'s core logic, or any dialog still wired into `admin_window.py` / `client_detail_window.py` — those are all live and referenced.
