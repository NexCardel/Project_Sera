# DOM Parser 1 (`DOM_Parser_1`)

**DOM Parser 1** is the high-precision analytical classification and audit reporting subsystem for **Project Sera DOM Tracker**.

It combines **3-Factor DOM Signal Detection** (URL Routes, 15-Digit ARNs, and Page Banners) with **FST Classifier 1's Bi-Directional Identity Stitching** to deliver a pinpoint, color-coded Excel audit workbook (`dom_audit_report.xlsx`).

---

## 🏗️ Architecture & Core Algorithms

### 1. 3-Factor Multi-Signal Classification Matrix
To prevent false triggers and ensure 100% classification accuracy, DOM Parser 1 evaluates 3 simultaneous signals:
* **Factor 1: URL Route & Component Context:** Distinguishes return success routes (`fo-return-success`, `fo-e-verify-later`) from draft schedules (`personal_information`, `parta`, `select-status`) and general navigation (`login`, `dashboard`).
* **Factor 2: 15-Digit Statutory Ack / ARN Number:** Strictly verifies real numeric acknowledgement numbers, rejecting word fragments or UI text.
* **Factor 3: Rendered Page Text & Confirmation Banners:** Inspects confirmation text for exact lifecycle phrases (`"successfully filed"`, `"verified successfully"`, `"e-Verify Later"`, `"Download ITR-V"`).

### 2. Bi-Directional Identity Stitching (Forward & Backward Sweep)
* **Forward Carry:** Once a PAN or Assessee Name is discovered on a page, it applies forward to subsequent entries in the same user session.
* **Backward Carry:** When the assessee's identity is revealed in `Part A General` or a return form, the engine walks **backwards** up the timeline and retroactively attaches the PAN and Legal Name to earlier `login` and `select-status` entries.
* *Result:* Zero orphaned `UNKNOWN` or unlinked entries in the journey trace.

### 3. Entity-Centric Reconciliation (`entities[pan]`)
* Groups all captures by unique Taxpayer Entity (PAN / GSTIN).
* Discards UI tooltips and noise tokens (`menu`, `close`, `help`, `refresh`, `download`, `button`).
* Assembles multi-field split profile names (`FirstName + MiddleName + SurName`) into canonical legal names.

---

## 📊 5 Lifecycle Categories in Excel

| Sheet Tab | Lifecycle Category | Classification Rule & Color |
| :--- | :--- | :--- |
| **Tab 1: Executive Summary** | **Dashboard & KPI Cards** | Consolidated metrics across all taxpayer sessions. |
| **Tab 2: 1. Filed & Verified** | 🟢 **Completed & Verified Filings** | Valid 15-digit Ack + confirmed e-Verification banner (`"successfully filed"`). |
| **Tab 3: 2. Submitted (Pending)** | 🟡 **Submitted Pending Verification** | Valid 15-digit Ack + ITR-V generated (`fo-e-verify-later`, `"Download ITR-V"`). |
| **Tab 4: 3. Return Drafts & Schedules** | 🔵 **Active Return Form Editing** | Draft schedules, `PartA_GEN`, personal info, computation tables. |
| **Tab 5: 4. Taxpayer Identity Ledger** | 🟣 **Reconciled Taxpayer Master** | Canonical PAN, GSTIN, Legal Name, Observed Forms, and total capture counts. |
| **Tab 6: 5. Navigation Journey Trace** | ⚪ **Step-by-Step Clickstream Trace** | Timestamped chronological breadcrumb audit trail. |

---

## 🚀 Quick Start & CLI Usage

### 1. One-Click Watcher (Windows)
Double-click [`run.bat`](file:///c:/Users/Nex/Downloads/Project%20Sera/APP/DOM_Parser_1/run.bat) to launch the real-time polling watcher.

### 2. Manual CLI Execution
```powershell
# Run once against the live SQLite database
python dom_parser.py "..\rawPayload.db" "dom_audit_report.xlsx"

# Run in live watch mode
python dom_parser.py "..\rawPayload.db" "dom_audit_report.xlsx" --watch
```

### 3. Programmatic Python API
```python
import dom_parser

# Generate the audit report
success = dom_parser.process_data("rawPayload.db", "dom_audit_report.xlsx")
```

### 4. Direct Desktop App Integration
In Project Sera:
1. Open the **Tracker Dump Window** (FST / DOM Dump manager).
2. Click **Preferences / Options** menu (⚙️).
3. Select **"DOM Parser 1 (Excel Report)"** to instantly generate and launch `dom_audit_report.xlsx`.
