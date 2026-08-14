# Sera Dead Code & Subsystem Cleanup Specifications

This document records all legacy, unused, or obsolete subsystems, files, and design patterns that have been safely removed from Project Sera to optimize performance, simplify architecture, and reduce technical debt.

---

## 1. Removed Subsystems & Source Files

### A. DRS (Deadline Reminder System) Engine & Dashboard UI
- **`ui/windows/dashboard_window.py`**: Removed the standalone DRS Return Status Dashboard window.
- **`ui/dialogs/form_type_manager_dialog.py`**: Removed the DRS Filing Type Manager dialog.
- **`ui/dialogs/filing_period_manager_dialog.py`**: Removed the DRS Filing Period Manager dialog.
- **`main.py`, `sidebar.py`, `admin_window.py`, `app_shell.py`**: Removed DRS navigation routes, menu triggers, and dashboard signal bridges.
- **Retained Subsystem**: The core **File Submission Tracker (FST)** remains active, automatically recording tax return filing results, ARN captures, and fallback confirmations into `filing_status`.

### B. Dual-Theme Engine Redundancy
- **`ui/utils/theme.py`**: Removed obsolete `DARK_STYLESHEET` string definitions and duplicate theme switching code.
- **`ui/dialogs/settings_dialog.py`**: Removed obsolete Theme dropdown options, standardizing on the global high-contrast dark palette (`#292929` body, `#0A0A0A` surface, `#171717` cards, `#FFFFFF` white cell grid tables).

### C. Legacy Emojis & Text Icons
- Removed hardcoded emoji prefixes (`🟡`, `🟢`, `🔴`, `🪪`, `🔒`) from context menus, table items, and window labels.
- Replaced all visual icons with vector-scalable **Google Material Design Icons** via QtAwesome (`qtawesome`).

---

## 2. Source Code & Snapshot Cleanups

- **`source_2/`**: Created a clean snapshot of active source code modules, excluding all compiled `__pycache__` binaries, temporary logs (`*.log`, `*.tmp`), database vaults (`master.db`), and virtual environments (`venv/`, `.build_venv/`).

---

## 3. Active System Verification Matrix

| Component | Status | Active Handler |
| :--- | :--- | :--- |
| **Search & Excel Grid** | Active | `ui/windows/search_window.py` |
| **Cell Formatting & Undo/Redo** | Active | `database.py`, `search_window.py` |
| **File Submission Tracker (FST)** | Active | `sera_extension/tracker.js`, `ui/extension_listener.py` |
| **Client Detail Workspace** | Active | `ui/windows/client_detail_window.py` |
| **LAN P2P Database Sync** | Active | `sync_peer.py`, `ui/dialogs/sera_sync_dialog.py` |
| **Master Column List (MCL)** | Active | `ui/dialogs/mcl_manager_dialog.py` |
| **Audit Logging System** | Active | `database.py`, `ui/dialogs/audit_log_dialog.py` |
| **DRS Dashboard & Managers** | **Removed** | Dead code eliminated |
