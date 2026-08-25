# Sera FST Classifier — Technical Specification & Lifecycle Correlation Engine

**FST Classifier 1** (`FST_Classifier_1`) is an advanced, offline-first analysis subsystem of **Project Sera** designed to process continuous government tax portal API interception streams (`seraRawPayloadDump.txt` or `rawPayload.db`), resolve complex multi-session taxpayer identities, and output meticulously formatted, color-coded audit spreadsheets (`payload_report.xlsx`).

---

## 1. Core Architecture

The classifier operates in 4 decoupled processing stages:

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                      Input: Raw Interception Stream                       │
│    (seraRawPayloadDump.txt, FST TCP captures, or rawPayload.db records)   │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                                      ▼
             Stage 1: Multi-Format Parser & Payload Extraction
                                      │
                                      ▼
             Stage 2: Deterministic Identity & Acknowledgment Map
                                      │
                                      ▼
             Stage 3: Cross-Entry Lifecycle Correlation Engine
                                      │
                                      ▼
             Stage 4: Styled Executive Excel Report Generator
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                    Output: payload_report.xlsx                            │
│           • Tab 1: Action Summary (7 Categorized Lifecycle Rows)          │
│           • Tab 2: All Logged Entries (Chronological Audit Log)           │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 2. The 7 Lifecycle Classification Categories

| Category ID | Display Label | OpenPyXL Fill Color | Condition Rules |
| :--- | :--- | :--- | :--- |
| **Category 1** | `1. File Submitted (NOT E-Verified)` | `FFF2CC` (Yellow) | Endpoint: `/returns/submit/wzrd` with `httpStatus: "ACCEPTED"` and `successFlag: true`, but `evc` is `null` or unverified. |
| **Category 2** | `2. File Submitted & E-Verified (ITR)` | `E2EFDA` (Green) | Endpoint: `/verificationservices/auth/validateOTP` with `moduleCode: "ITR"`, `formCd: "1"-"7"`, and status `SUCCESS`. Paired with filing submit or processed as standalone e-verification. |
| **Category 3** | `3. Bank Account E-Verified (NO Return Submitted)` | `DDEBF7` (Blue) | Endpoint: `/validateOTP` with `moduleCode: "NON-ITR"` or form `FO-091-EVERI`. Identifies bank pre-validation OTPs without tax return filings. |
| **Category 4** | `4. GST Return Filed & E-Verified` | `E2EFDA` (Green) | Endpoint: `/formdetails` with `status: "FIL"`, `evc_chk: "E"`. GSTR-1, GSTR-3B filings. |
| **Category 5** | `5. Bank Status: [Sub-State] ([Bank Name])` | Dynamic (Green / Yellow / Gray / Red) | Multi-state matrix evaluating bank verification flags (`accValidity`, `accountStatus`, `activeFlag`, `refundFlag`, `remarks`, `errorCd`). |
| **Category 6** | `6. Visited But No Return Submission` | `EDEDED` (Gray) | Taxpayer engaged with the portal (profile lookups, wizard checks, bank checks) but filed no ITR or GST return. |
| **Category 7** | `7. Visited Site (All Visits Enumerated)` | `F2F2F2` (Light Gray) | Complete step-by-step chronological audit trail enumerating all API calls per taxpayer. |

---

## 3. Bank Account 4-State Verification Matrix (Category 5)

The classifier avoids false "failure" flags by evaluating 6 discrete payload attributes:

```python
def classify_bank_entry(rp):
    bank_name = rp.get("bankName", "BANK ACCOUNT")
    acc_validity = rp.get("accValidity", "")
    acc_status = rp.get("accountStatus", "")
    active_flag = rp.get("activeFlag", "")
    refund_flag = rp.get("refundFlag", "")
    remarks = rp.get("remarks", "")
    error_cd = rp.get("errorCd", "")
    
    # 1. Validated and Open for Refund
    if acc_validity == "V" and (acc_status == "Account Valid and Open" or not acc_status):
        refund_status = "Nominated for Refund" if refund_flag == "Y" else "Validation Active"
        return {"label": f"5. Bank Status: Validated ({bank_name})", "color": "E2EFDA", "details": ...}
        
    # 2. Validated with Warning (e.g. Name Mismatch)
    if acc_validity == "V" and ("Invalid" in acc_status or "NAME_MATCH" in remarks):
        return {"label": f"5. Bank Status: Validated with Warning ({bank_name})", "color": "FFF2CC", "details": ...}
        
    # 3. Inactive / Legacy Account (Historical merged/closed bank)
    if active_flag == "D" or (acc_validity == "I" and "Linkage failed" in remarks and not error_cd):
        return {"label": f"5. Bank Status: Inactive / Legacy Account ({bank_name})", "color": "F2F2F2", "details": ...}
        
    # 4. Revalidation Required (Failed / Rejected)
    return {"label": f"5. Bank Status: Revalidation Required ({bank_name})", "color": "FCE4D6", "details": ...}
```

---

## 4. Multi-Pass Identity Resolution Algorithm

To handle anonymous ITR submissions (where the government API intentionally omits PAN and Name), the engine uses a 2-pass topological algorithm:

1. **Pass 1 — Immutable Map Construction**:
   $$\text{AckMap}[\text{AckNo}] \longleftarrow \text{PAN}$$
   $$\text{NameMap}[\text{PAN}] \longleftarrow \{\text{Extracted Legal / Trade Names}\}$$
2. **Pass 2 — Topological Identity Binding**:
   For any anonymous capture $E_i$ lacking a PAN:
   $$\text{PAN}(E_i) = \begin{cases} \text{AckMap}[\text{Ack}(E_i)] & \text{if } \text{Ack}(E_i) \in \text{AckMap} \\ \text{PAN}(E_{i-1}) & \text{otherwise (Active Channel Context)} \end{cases}$$
3. **Deep ITR Structure Parser**:
   Extracts assessee names directly from nested JSON schemas:
   * `rp["ITR"]["ITR4"]["PersonalInfo"]["AssesseeName"]`
   * `rp["ITR"]["ITR4"]["Verification"]["Declaration"]["AssesseeVerName"]`
   * `rp["ITR"]["ITR4"]["ScheduleBP"]["NatOfBus44AD"][0]["NameOfBusiness"]`

---

## 5. Live File Watcher Mode

When executed with `--watch` (or launched via `run.bat`), `fst_classifier.py` runs a lightweight polling loop:
```python
last_mtime = -1
while True:
    if os.path.exists(target_dump):
        mtime = os.path.getmtime(target_dump)
        if mtime > last_mtime:
            process_data(target_dump, output_excel)
            last_mtime = mtime
    time.sleep(2)
```

---

## 6. Desktop Integration & Preferences UI

The classifier is connected directly to Project Sera's desktop application:
1. **Header Card Preferences Menu**:
   * Location: [`ui/windows/tracker_dump_window.py`](file:///C:/Users/Nex/Downloads/Project%20Sera/APP/ui/windows/tracker_dump_window.py)
   * Action: **`FST Classifier (Excel)`** (`mdi.file-excel`)
   * Trigger: Compiles `payload_report.xlsx` and opens it in Microsoft Excel.
2. **Database Hook (`database.py`)**:
   * Method: `sync_fst_classifier()`
   * Automatically refreshes `FST_Classifier_1/payload_report.xlsx` whenever raw dumps are updated or rebuilt.
