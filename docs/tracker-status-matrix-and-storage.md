# Tracker Dump — Submission Status Matrix & Storage Architecture

**Subsystem:** Tracker Dump Workspace (TrackerDumpWindow) & Database Layer (awPayload.db)  
**Target Application:** Project Sera Desktop Core (APP)  
**Status:** Active  
**Last Updated:** September 2026  

---

## 1. Executive Summary

The **Tracker Dump Workspace** provides live visibility, auditing, inspection, and lifecycle analysis of government portal return filings and compliance interactions captured via **Sera SDC (DOM Crosshairs)** and **Sera VSDC (Visual Screen Data Capture)**.

All events are stored directly in the SQLite table 	racker_dump inside awPayload.db. To maintain high performance, privacy, and clean disk hygiene:
- **Zero Disk Text Dumps**: Raw payload text dumping (seraRawPayloadDump_*.txt, Raw_Payload_Dump/) and text session logging (Vsdc_Captures/) have been completely retired in favor of native, indexed SQLite transactions.
- **Color-Coded Status Badges**: All return and form statuses are rendered with standardized Google Material Design icons (mdi.*) and theme-consistent color pills.
- **Monotonic Status Promotion Engine**: Submissions only upgrade their lifecycle rank (e.g. Draft -> Submitted -> Verified), ensuring statutory ARNs and valid verifications are never overwritten or downgraded by subsequent navigation passes.

---

## 2. Submission Status Pill Matrix

Status values rendered in the Tracker Dump table and History inspector use Google Material Design icons (mdi.* via QtAwesome) and theme tokens adhering to Obsidian & Emerald dark mode UI standards:

| Status Category | Status Text Label | Material Icon | Foreground / Border | Background Pill Fill | Lifecycle Qualification & Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Filed & Verified** | Filed & Verified (Processed)<br>Filed & Verified<br>Filed<br>e-Verified | mdi.check-decagram | Emerald Green (#00E676) | Deep Forest (#052e16) | Statutory filing completed with full OTP / EVC authentication or processed status confirmed on portal. |
| **Pending e-Verification** | Submitted (e-verification pending)<br>Submitted (Pending e-Verification)<br>Submitted | mdi.clock-alert-outline | Amber Yellow (#FFD600) | Dark Amber (#2e2305) | Return submitted to government portal with valid 15-digit Ack / ARN, but 30-day e-verification is pending (ITR-V mode). |
| **e-Verification Action** | e-Verification completed<br>Bank Account e-Verified | mdi.shield-check-outline | Cyan Blue (#40C4FF) | Deep Ocean (#082f49) | Authentication milestone or standalone bank pre-validation completed via Aadhaar OTP. |
| **Bank Validated** | Validated (Active & Nominated for Refund) | mdi.bank-check | Bright Purple (#E040FB) | Deep Violet (#2e1065) | Assessee bank account successfully pre-validated with NPCI and selected for direct tax refund credit. |
| **In Progress / Draft** | In Progress<br>Draft<br>Processing | mdi.progress-clock | Warm Orange (#FF9100) | Dark Orange (#381a05) | Active form schedule preparation, tax computation, or provisional draft filing in progress. |
| **Failed / Defective** | Failed<br>Rejected<br>Defective | mdi.alert-octagon-outline | Crimson Red (#FF5252) | Deep Burgundy (#450a0a) | Statutory rejection, defective return notice issued, or validation error returned by portal. |
| **Default / Visited** | Visited Site<br>Other | mdi.information-outline | Slate Gray (#94A3B8) | Dark Slate (#1e293b) | Profile verification, dashboard overview, or portal visit without filing submission. |

---

## 3. UI Layout & Sizing Protections

To avoid text truncation, ellipses, or horizontal squashing inside QTableWidget cells:
1. **Dynamic Column Minimum Widths (_adjust_table_columns)**:
   - **Submission Status Column**: Minimum width enforced at 230px (Weight: 2.0).
   - **Actions Column**: Minimum width enforced at 190px (Weight: 1.8).
2. **Resize Protection**:
   - When table layouts are recalculated or restored from previous view settings, columns are bounded by max(prev_width, min_width), preventing squished restoration.
3. **Intrinsic Font Sizing**:
   - QSizePolicy.Minimum is applied to status badge containers and pill widgets, preventing child labels from collapsing below text font metrics.

---

## 4. SQLite Storage Architecture (awPayload.db)

All capture transactions are managed within awPayload.db:

`sql
CREATE TABLE IF NOT EXISTS tracker_dump (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id            INTEGER,
    unassigned_identity  TEXT,
    service_id           INTEGER,
    portal               TEXT,
    period_label         TEXT,
    arn_number           TEXT,
    capture_method       TEXT,
    status               TEXT,
    raw_payload_json     TEXT,
    captured_by          TEXT,
    created_at           TEXT,
    dataset_key          TEXT
);
`

### Key Performance Benefits:
- **Instantaneous Writes**: Zero synchronous disk I/O bottleneck from file-appending .txt streams.
- **Transactional Integrity**: SQLite WAL mode allows concurrent reads from FST Classifier / DOM Parser reports without file-locking conflicts.
- **Privacy Assurance**: No plaintext personal client identifiers or transient audit logs written to unencrypted user home folders.
