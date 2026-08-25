# FST Classifier 1 (`FST_Classifier_1`)

**FST Classifier 1** is the high-precision payload analysis and lifecycle correlation engine for **Project Sera**. It ingests raw government portal API interception streams (`seraRawPayloadDump.txt` or database records), disambiguates fragmented taxpayer sessions, and generates structured, color-coded executive spreadsheets (`payload_report.xlsx`).

---

## 🌟 Key Capabilities

1. **Deterministic Identity Resolution (No Client ID Reliance)**:
   - Uses **Temporal Context Proximity** and **Retroactive Acknowledgment Cross-Linking** to accurately map anonymous return wizard submissions (`/returns/submit/wzrd`) to the correct PAN.
   - Extracts deep assessee names and trade names from nested ITR form payloads (`rp["ITR"]["ITR4"]["PersonalInfo"]["AssesseeName"]`).

2. **The 7 Lifecycle Classification Categories**:
   * 🟡 **1. File Submitted (NOT E-Verified)**: Return submitted with HTTP status ACCEPTED, but OTP e-verification is pending.
   * 🟢 **2. File Submitted & E-Verified (ITR)**: Return filing paired with successful OTP validation (or standalone e-verification session).
   * 🔵 **3. Bank Account E-Verified (NO Return Submitted)**: Form `FO-091-EVERI` authenticated via OTP without ITR submission.
   * 🟢 **4. GST Return Filed & E-Verified**: GSTR-1 / GSTR-3B filings authenticated via EVC.
   * 📊 **5. Bank Status Matrix**: Evaluates bank accounts into 4 accurate states (Validated, Validated with Warning, Inactive/Legacy, Revalidation Required).
   * ⚪ **6. Visited But No Return Submission**: Portal interactions with zero ITR/GST submissions.
   * ⚪ **7. Visited Site (All Visits Enumerated)**: Full chronological step-by-step API audit log.

3. **Live Watcher Mode (`--watch`)**:
   - Continuously polls `..\seraRawPayloadDump.txt` for live incoming captures and auto-updates `payload_report.xlsx` in real-time.

4. **Direct Desktop Integration**:
   - Accessible directly from Project Sera's **Tracker Dump Workspace** via **`Preferences` ➔ `FST Classifier (Excel Report)`**.
   - Database sync hook (`sync_fst_classifier`) automatically refreshes reports during database rebuilds.

---

## 🚀 Usage

### Option 1: Double-Click Batch Launcher
Double-click **`run.bat`** in this directory. It automatically installs requirements and starts watching `..\seraRawPayloadDump.txt`.

### Option 2: Command Line Execution
```bash
# Install dependencies
pip install -r requirements.txt

# Live Watcher Mode (Monitors live dump for changes)
python fst_classifier.py "..\seraRawPayloadDump.txt" "payload_report.xlsx" --watch

# One-off Manual Run
python fst_classifier.py "path/to/custom_dump.txt" "custom_output.xlsx"
```

### Option 3: Python In-App API
```python
import fst_classifier

# Run classification programmatically
success = fst_classifier.process_data("seraRawPayloadDump.txt", "payload_report.xlsx")
```

---

## 📁 Output Structure

The generated workbook (`payload_report.xlsx`) contains:
* **Tab 1: Action Summary**: Categorized lifecycle events, discrete individual client rows, PANs, legal names, filing ARNs, and verification remarks.
* **Tab 2: All Logged Entries**: Raw capture audit trail with UTC timestamps, assigned PANs, and endpoint URLs.
