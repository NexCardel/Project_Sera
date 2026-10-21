# Deadline Reminder System (DRS) — System Specification & Handover Blueprint

**Target Branch:** `drs-prototype`  
**Worktree Directory:** `C:\Users\Nex\Downloads\Project Sera\APP-drs`  
**Last Update:** October 22, 2026  

---

## 1. Executive Summary & Objective

The **Deadline Reminder System (DRS)** is a real-time compliance tracker for tax and accounting workflows (GST, Income Tax, TDS, etc.). 

While Sera's existing **LTT** (Live Tracker Tool) is *capture-driven* (showing only clients that have appeared in portal captures or hand-ticked lists), **DRS is client- and service-driven from Master DB**:
1. It looks up every registered client in `master.db` that is enrolled in a given service (e.g., GST, Income Tax).
2. It auto-detects all return filing forms from portal captures in `tracker_dump` (e.g. `GSTR-1`, `GSTR-3B`, `CMP-08`, `ITR-1`, `ITR-4`).
3. It links each return form to its portal, which in turn links to its registered Service in `master.db`.
4. It creates a dedicated tab in Excel for each filing form, listing **every registered client enrolled in that service**.
5. For clients that have captures in `tracker_dump`, it reports their latest filing period, acknowledgement number (ARN), and filing status (using Sera's 4-level status ladder).
6. For clients with no filings captured yet, it clearly displays `"no captures yet"`.
7. It maintains a live monthly Excel workbook (`DRS/DRS_YYYY-MM.xlsx`) powered by companion CSVs and Power Query feeds.
8. It updates **strictly on events** (refreshes only when a new entry appears in `tracker_dump`), with zero recurring interval polling.

---

## 2. Agreed Requirements & User Clarifications

The following specifications were explicitly agreed upon with the user:

| Parameter | Specification | User Directive |
| :--- | :--- | :--- |
| **System Title** | Deadline Reminder System (DRS) | Official title in UI and reports |
| **Client Population** | Master DB Service Enrollment | All clients attached to a service via `client_services` appear on that service's form tabs. |
| **Form to Service Link** | Auto-detected from Captures | Forms extracted from portal tags (`GST (GSTR-3B)` $\rightarrow$ `GSTR-3B` $\rightarrow$ GST Portal $\rightarrow$ GST Service). |
| **Default for Uncaptured Clients** | `"no captures yet"` | Both `Latest period` and `Status` display `"no captures yet"` when no capture exists. |
| **Multi-GSTIN Support** | Separate Row per Registration | If a client has multiple GSTINs, each GSTIN gets its own row on GST return sheets. |
| **Storage Location** | `app_dir/DRS/DRS_YYYY-MM.xlsx` | Monthly workbooks saved under the `DRS/` folder next to vault databases. |
| **Configuration Storage** | `master.db` (`settings` table) | Column choices and order saved as JSON in `drs_config` setting. |
| **UI Access Point** | Main Sidebar | Placed under the **Clients** accordion group as `"DRS System"`. |
| **Sidebar Action** | Option B (Opens Configuration Dialog) | Clicking opens `DRSConfigDialog` (which includes "Open DRS Workbook", "Save & Refresh Now", etc.). |
| **Column Arrangement UX** | Modeled on MCL Dialog | Reorderable list with **Up** (`mdi.arrow-up`) and **Down** (`mdi.arrow-down`) buttons and visibility checkboxes. |
| **Status Vocabulary** | 4-Level Status Ladder | `Submitted & Verified`, `Submitted (Not Verified)`, `Draft`, `Not Submitted`, plus `"no captures yet"`. |
| **Refresh Trigger** | **Event-driven only** | Triggers only upon new capture arrival or P2P sync. **No fixed-interval polling timers**. |
| **Visual Style** | **Identical to LTT** | Evergreen `#1F6F4A` header fills, `#0A0A0A` dialog background, Material icons (`mdi.*`), identical cell badge fills. |

---

## 3. Database & Data Architecture

```
[ master.db ]                                      [ rawPayload.db ]
  ├── clients (id, notes, ...)                       ├── tracker_dump (client_id, portal, period_label, arn, status, ...)
  ├── client_values (client_id, column_id, value)    └── client_raw_containers / SRPF (filing_history JSON)
  ├── client_services (client_id, service_id)                       │
  ├── services (id, name, login_page_link, ...)                     ▼
  ├── mcl_columns (id, label, field_type, ...)       [ core/drs/engine.py ]
  └── settings (key='drs_config', value=JSON)                       │
                                                                    ▼
                                                     [ app_dir/DRS/ ]
                                                       ├── DRS_2026-10.xlsx
                                                       ├── DRS_2026-10.csv (Data feed)
                                                       └── DRS_2026-10_overview.csv (Summary)
```

### 3.1. Master DB Entities
* `clients`: Registered clients in the firm.
* `client_services`: Many-to-many relationship attaching clients to active services.
* `mcl_columns`: Custom fields configured by admin (e.g. PAN, Trade Name, Mobile).
* `client_values`: Key-value storage mapping `(client_id, column_id)` to text values.
* `services`: Registered portals/services (e.g. GST, Income Tax, TRACES).

### 3.2. Tracker Dump & SRPF Entities
* `tracker_dump`: Real-time ingestion log of filings with portal name, period, ARN, and status.
* `client_raw_containers`: Grouped container per legal entity, containing `filing_history` (list of historical capture dicts).

---

## 4. Component Breakdown & Implementation

### 4.1. Core DRS Engine (`core/drs/`)

#### A. `core/drs/config.py`
* **Column Categories**:
  1. `[MCL]`: MCL columns retrieved from `db.get_mcl_columns()`. Secret/password fields are excluded.
  2. `[Fixed: DRS Core]`: `Latest period` and `Status`. Positionable anywhere by user.
  3. `[Tracker Dump]`: `ARN`, `Captured on`, `Captured by`, `Portal`.
* **Functions**:
  * `default_drs_columns(db)`: Constructs default sequence: Primary MCL (PAN, Name, GSTIN) $\rightarrow$ Fixed $\rightarrow$ Tracker $\rightarrow$ Rest.
  * `load_drs_config(db)`: Reads `drs_config` from `master.db` settings, reconciling with live MCL columns.
  * `save_drs_config(db, config)`: Saves JSON config into `master.db`.
  * `get_active_columns(db)`: Returns enabled columns in user-configured sequence.

#### B. `core/drs/engine.py`
* **Functions**:
  * `detect_forms_and_services(db)`: Scans capture history to extract return forms (e.g. `GSTR-3B`, `ITR-1`), determines base portal, and calls `db.get_service_for_portal(portal)`.
  * `build_drs_dataset(db, active_cols)`:
    * Selects clients attached to each form's service (`client_services`).
    * Resolves client PAN and GSTIN(s). Multi-GSTIN clients get separate rows for GST forms.
    * Matches against capture history.
    * Ingests latest period and maps status using `core.ltt.feed.ladder(status, arn)`.
    * Uncaptured clients receive `NO_CAPTURES = "no captures yet"`.

#### C. `core/drs/export.py`
* **Output Path**: `Path(app_dir) / "DRS" / f"DRS_{month:%Y-%m}.xlsx"`
* **Styling Palette**:
  * Evergreen header fill: `#1F6F4A`, bold white text.
  * Alternate row band: `#FFF4F8F5`.
  * Status Badge Colors:
    * `Submitted & Verified`: Fill `#86D4A0`, Text `#0F4F2A`
    * `Submitted (Not Verified)`: Fill `#C9E06F`, Text `#4A5A0A`
    * `Draft`: Fill `#FFDB70`, Text `#6B4A00`
    * `Not Submitted`: Fill `#F4A99B`, Text `#7A1F12`
    * `no captures yet`: Fill `#DADADA`, Text `#555555`
* **Workbook Construction**:
  * `build_drs_shell(...)`: Creates workbook shell with openpyxl, formatting widths and titles.
  * `attach_drs_queries(...)`: Connects Windows COM Excel Power Query to the companion CSVs.
  * `populate_direct_openpyxl(...)`: Populates rows directly into openpyxl for immediate visual readiness.
  * `schedule_drs_export(db, delay=3.0)`: Debounced event trigger, invoked upon capture arrival or P2P sync.

### 4.2. UI Components

#### A. `ui/dialogs/drs_config_dialog.py`
* **Visual Style**:
  * Dark palette matching `ui/windows/ltt_rules_dialog.py`: `#0A0A0A` background, `#262626` borders, `#2E9B5F` accent.
  * Material design icons (`mdi.calendar-clock`, `mdi.arrow-up`, `mdi.arrow-down`, `mdi.restore`, `mdi.microsoft-excel`, `mdi.content-save-check-outline`).
* **Controls**:
  * Reorderable column list with checkboxes.
  * Move Up / Move Down buttons (identical UX to MCL dialog).
  * Reset to Defaults button.
  * Live preview card summarizing detected form tabs, mapped services, and client counts.
  * Action buttons: "Open DRS Workbook", "Save & Refresh Now", "Close".

#### B. `ui/shell/sidebar.py` & `main.py`
* Added sub-nav button `btn_drs_system` under `l_clients`.
* Signal `action_open_drs` connected in `main.py` to open `DRSConfigDialog(self.db, self.shell)`.
* `schedule_drs_export(self.db)` called in `_handle_extension_result` and `refresh_after_sync`.

---

## 5. Current Findings & Root Cause Analysis

During user testing on the prototype, two specific questions were raised:

### Issue 1: "Is it connected to the tracker dump? (All rows show 'no captures yet')"
* **Root Cause**:
  In [`core/drs/engine.py`](file:///C:/Users/Nex/Downloads/Project%20Sera/APP-drs/core/drs/engine.py#L220), the engine accessed the container records using:
  ```python
  containers_by_pan[p]["history"].extend(c.get("history", []))
  ```
  However, in Sera's SRPF container implementation ([`core/ltt/feed.py:177`](file:///C:/Users/Nex/Downloads/Project%20Sera/APP-drs/core/ltt/feed.py#L177)), the list is stored under the dictionary key:
  ```python
  c.get("filing_history")
  ```
  Because `c.get("history")` was `None`, the capture list resolved to empty `[]`. Consequently, the engine treated all clients as uncaptured and fell back to `"no captures yet"`.

### Issue 2: "Where are the filters in the table?"
* **Root Cause**:
  In [`core/drs/export.py`](file:///C:/Users/Nex/Downloads/Project%20Sera/APP-drs/core/drs/export.py#L240), header cells were colored with evergreen fills, but the worksheet was not registered with an Excel AutoFilter range (`ws.auto_filter.ref`) or an Excel Table definition (`openpyxl.worksheet.table.Table`) spanning `A3:<LastColumn><LastRow>`.
  Without `ws.auto_filter.ref` or a `Table` object, Excel opens the worksheet as plain colored cells without the native dropdown filter arrows on header columns.

---

## 6. Action Items & Instructions for the Next Agent

The next agent should perform the following targeted tasks in `APP-drs`:

1. **Fix History Retrieval in `core/drs/engine.py`**:
   * Change container history extraction from `c.get("history", [])` to:
     ```python
     hist = c.get("filing_history") or c.get("history") or []
     ```
   * Add a direct secondary check to `tracker_dump` in `rawPayload.db`:
     Query `tracker_dump` rows matching `client_id` or `pan` or `unassigned_identity` so that any capture in `tracker_dump` is guaranteed to match even if SRPF containers haven't finished aggregating.

2. **Enable Excel Table AutoFilter in `core/drs/export.py`**:
   * In `populate_direct_openpyxl`:
     ```python
     last_col_letter = chr(64 + len(col_labels)) if len(col_labels) <= 26 else f"A{chr(64 + len(col_labels) - 26)}"
     max_row = max(4, len(rows) + 3)
     ws.auto_filter.ref = f"A3:{last_col_letter}{max_row}"
     ```
   * Optionally add an openpyxl `Table`:
     ```python
     from openpyxl.worksheet.table import Table, TableStyleInfo
     tab = Table(displayName=f"DRS_{clean_name}", ref=f"A3:{last_col_letter}{max_row}")
     tab.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=True)
     ws.add_table(tab)
     ```

3. **Verify via Tests**:
   * Run the test suite:
     ```powershell
     .\venv\Scripts\python.exe -m pytest --basetemp="$env:TEMP\sera_pytest_drs_tmp" tests/test_drs.py
     ```
   * Confirm that Excel opens with dropdown filter arrows and populated capture statuses.
