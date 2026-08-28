# Sera Audit Log — Improvement Blueprint (v2)

*Revised after project-structure.md, setup.md, browser-automation-extension.md, and file-submission-tracker.md were added, and features-security.md/build-release.md were updated to v2.3.3.*

## 1. What Changed Since v1

- **Audit Log now has a concrete home:** `database.py` owns "SQLCipher DB setup, CRUD, Audit Log, Filing Status, Backup/Restore" (`project-structure.md`). It is not a separate module — any schema/query change is a `database.py` change.
- **Alerts have a concrete home too:** `ui/services/alert_service.py` (logic) and `ui/components/toast.py` (`SeraAlert` widget). Anomaly flagging (v1 item I) plugs into this, not a new component.
- **A client anonymization system already exists:** MCL now assigns every client a `client_id_token` (`CLI-00001`, ...) specifically so clients can be identified "without exposing sensitive PAN or GST details" for cloud/internet contexts. This directly affects the v1 recommendation to resolve Client ID → name (see §3.A below).
- **Admin PIN gating is now explicit and confirmed to include the audit log viewer** (`setup.md`): Client CRUD, Backup restore, CSV export, and Audit log viewer are all PIN-gated. No change to my earlier assumption, but now documented rather than inferred.
- **A new event source exists that isn't described as audited: the File Submission Tracker.** Tracker-dump writes happen on automated ARN capture. Neither `features-security.md`'s audit list ("client views, autofill triggers, manual copies, backups, restores, CSV exports") nor the screenshot's action set (`view`, `backup`, `manual_assist`, `purge_duplicates`, `update_settings`) mentions filing events. This is a gap worth closing.
- **Purge Duplicates now has documented merge logic** (normalization rules, serial-column exclusion, "keeps lowest client ID"). The existing `purge_duplicates` audit row should be able to show *what* was merged — reinforces the v1 drill-down item.
- **Build/version bumped to 2.3.3**, and output paths changed (`package_dist/`, `installer_output/`) — relevant only in that any schema migration for this blueprint should land in the *next* version bump, not 2.3.3.

---

## 2. Revised Improvements

### A. Client/Service ID readability — **revised approach**
Original plan: join Client ID → client name at query time. **Revise this** given `client_id_token` exists specifically to avoid exposing client identity outside the local vault:
- **In the local Audit Log UI** (never leaves the machine, already PIN-gated): resolve to the human-readable client name, same as Manage Clients does. This is the highest-value fix for readability and carries no extra exposure — it's already local and gated.
- **In any exported or cloud-facing audit view** (Export Log CSV, System Log export, or any future sync/analytics use): use `client_id_token` (`CLI-XXXXX`), not the raw name, consistent with how MCL already treats cloud identification. Add a rule so future features don't accidentally export raw client names via the audit log.
- Still handle archived clients gracefully — token and name should resolve for archived clients too, not show blank.

### B. Date range filter — unchanged
No new dependency found; still a self-contained addition to the Audit Log screen.

### C. Multi-select Action filter — unchanged, but the value set grows
Current actions: `view`, `backup`, `restore`, `manual_assist` (autofill/manual copy), `purge_duplicates`, `update_settings`, CSV export. If §F below is adopted, add `filing_submitted` (and optionally `filing_uncertain`) to the set.

### D. Pagination / indexing — unchanged in intent, now has a concrete owner
Implemented as a migration inside `database.py`. Ship it in the *next* versioned release after 2.3.3 (e.g. 2.3.4), through the existing `version.py` / `version.json` mandatory auto-update path — same reasoning as v1, now confirmed against the real file.

### E. Row drill-down — now has real content to show
With `purge_duplicates` merge details and (if adopted) filing/ARN context, drill-down stops being speculative:
- `purge_duplicates` → which records were merged into the surviving lowest-ID record.
- `manual_assist` → which field was copied/filled (already implied by `Service ID` column present in the log).
- `update_settings` → which settings keys changed.
- `filing_submitted` (new) → ARN (if captured), filing period type (Monthly/Quarterly/Annual), and whether it came from Tier 1 automated capture or Tier 2 fallback confirmation.

### F. Log File Submission Tracker events — **new in v2**
Right now a completed filing via automated ARN capture writes to the tracker dump but isn't described as producing an `audit_log` row. Given the audit log's stated purpose is tracking "credential access, portal autofill events, **and return submission tracking**" (`operations-sync.md`), this looks like a gap rather than an intentional omission. Recommend adding:
- `filing_submitted` — written alongside the tracker-dump insert, with the same timestamp and staff attribution, referencing Client ID and the filing period.
- `filing_uncertain` (optional, lower priority) — written when Tier 2's fallback modal is triggered, so admins can see when automated capture failed and required manual confirmation, even before the staffer resolves it.

### G. Filtered export — unchanged, plus token-safety
Export currently-filtered results as before; per §A, exported rows use `client_id_token` rather than raw client name/PAN/GST-adjacent identity.

### H. Self-referential audit (log-viewing-the-log) — unchanged
`audit_log_viewed` / `audit_log_exported` entries, same as v1.

### I. Analytics strip — unchanged, now wired to a real component
Still a small summary above the grid; if it needs alert-style emphasis (e.g. flagging a burst), route through `ui/services/alert_service.py` / `SeraAlert`, not a bespoke widget.

### J. Anomaly flagging — unchanged in concept, now has a concrete integration point
Emits through `alert_service.py` using existing `success/info/warning/error` levels and the `toast.py` bottom-left pattern — no new alert system needed.

---

## 3. Cross-Component Impact (updated)

| Improvement | Files/components touched | Notes |
|---|---|---|
| A. ID → name (local) / ID → token (exported) | `database.py` (query layer), Audit Log UI | Must **not** leak raw client names into CSV/system-log exports — enforce token-only on any export path |
| B. Date range filter | Audit Log UI only | — |
| C. Multi-select actions | Audit Log UI only | Extend enum if F is adopted |
| D. Pagination/indexing | `database.py` schema migration, `version.py`/`version.json`, GitHub release | Must sync safely across all Syncthing-linked `master.db` copies — ship as one clean version bump, not mid-cycle |
| E. Drill-down | `database.py` (new/extended columns for `purge_duplicates` merge list, `update_settings` diff, filing context) | Same migration batch as D |
| F. Filing audit events | `database.py` (tracker-dump write path), `ExtensionListener`/`tracker.js` result handling | New action types touch the File Submission Tracker flow directly — coordinate with whoever owns `automation.py`/extension messaging so the audit write happens exactly once per captured filing, not once per retry |
| G. Filtered export | Existing CSV export path in `database.py`/CSV module | Reuse, don't fork, the export formatter |
| H. Self-referential audit | `database.py` write on Audit Log screen open/export | Minor write-volume increase |
| I. Analytics strip | Audit Log UI, optionally `alert_service.py` | — |
| J. Anomaly flagging | `alert_service.py`, `toast.py` (`SeraAlert`) | No new UI system; reuse existing alert levels |

### Specific interactions to plan around (updated)

**Syncthing / shared `master.db`:** unchanged from v1 — any schema change (D, E, F) must land as one coordinated migration tied to an `APP_VERSION` bump and the mandatory auto-update flow, so no machine reads a new-schema `master.db` with old code or vice versa.

**Client anonymization (`client_id_token`):** new consideration — the audit log must respect the same boundary MCL already draws: real names/PAN/GST context stay local and PIN-gated; anything that could leave the machine (CSV export, future cloud sync) uses the token. This should be a documented rule in the export path, not left to each export call site to remember.

**File Submission Tracker (F):** new consideration — adding filing audit events means the audit write needs to happen at exactly the point the tracker dump is written, so a filing isn't double-logged on retries or under-logged if the tab closes mid-capture.

**Admin Gating:** unchanged — no new privilege tier needed; Audit Log viewer, drill-down, and filtered export all stay behind the existing Admin PIN.

**Sera Alert System:** unchanged — anomaly flagging and any "burst of activity" surfacing goes through `alert_service.py`/`toast.py`, not a new notification path.

---

## 4. Revised Rollout Order

1. **No schema change, no cross-file risk:** B (date range), C (multi-select), G (filtered export using existing CSV path + token-safety rule).
2. **Read-only, needs the token-vs-name rule written down:** A (local name resolution + export-time token substitution).
3. **Coordinated migration (ship together in one version bump, e.g. 2.3.4):** D (index + pagination), E (drill-down columns), F (filing audit events — coordinate with the automation/extension messaging owner), H (self-referential audit).
4. **Later:** I (analytics strip), J (anomaly flagging via `alert_service.py`).

Same grouping rationale as v1: batch schema changes into a single release so synced `master.db` copies never see a partial migration.
