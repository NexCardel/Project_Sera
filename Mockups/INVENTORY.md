# Project Sera — Screen & Controls Inventory (W0-3)

This document is the exhaustive inventory of every screen, dialog, and interactive control in Project Sera, based on current mockups (`Mockups/current/`), production UI code (`ui/`), and the active test suite (`tests/`).

It forms the foundational basis for:
1. **Redesign Work Packages** (Phases T, K, R, S, F in `Mockups/fleet/BUILD-PLAN.md`).
2. **'Where Every Control Goes' Verification** (`Mockups/fleet/README.md`).
3. **Behaviour Lock & Regression Prevention** (preserving widget attributes, shortcuts, and signals reached by tests).

> **Rule 5 & Behaviour-Lock Marker (🎯):**
> Widget attribute names marked with **🎯** are reached and tested by suites in `tests/`. During redesign, internal layouts and style sheets may be restructured onto the component kit, but these attribute names, their types/protocols, shortcut keys, and signal bindings must be preserved as specified in the test contracts.

---

## Catalog Overview

| # | Screenshot / Target | Class Name | Source File | Scope / Phase |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `01_all_clients_search.png` | [`SearchWindow`](file:///ui/windows/search_window.py) | `ui/windows/search_window.py` | Overhaul (S-6, S-7) |
| 2 | `02_manage_clients_admin.png` | [`AdminWindow`](file:///ui/windows/admin_window.py) | `ui/windows/admin_window.py` | Overhaul (S-9) |
| 3 | `03_tracker_dump.png` | [`TrackerDumpWindow`](file:///ui/windows/tracker_dump_window.py) | `ui/windows/tracker_dump_window.py` | Overhaul (S-10) |
| 4 | `04_client_detail_panel.png` | [`ClientDetailWindow`](file:///ui/windows/client_detail_window.py) | `ui/windows/client_detail_window.py` | Overhaul (S-8) |
| 5 | `settings_general.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`general`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-1) |
| 6 | `settings_actions.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`actions`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-2) |
| 7 | `settings_scc.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`scc`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-3) |
| 8 | `settings_mcl.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`mcl`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-4) |
| 9 | `settings_services.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`services`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-5) |
| 10 | `settings_main_vis.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`main_vis`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-4) |
| 11 | `settings_qc.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`qc`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-4) |
| 12 | `settings_admin_vis.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`admin_vis`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-4) |
| 13 | `settings_export.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`export`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-2) |
| 14 | `settings_backup.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`backup`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-2) |
| 15 | `settings_purge.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`purge`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-2) |
| 16 | `settings_tracker.png` | [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) (`tracker`) | `ui/dialogs/unified_settings_dialog.py` | Overhaul (S-2) |
| 17 | `dlg_audit_log.png` | [`AuditLogDialog`](file:///ui/dialogs/audit_log_dialog.py) | `ui/dialogs/audit_log_dialog.py` | Overhaul (S-11) |
| 18 | `dlg_change_master_password.png` | [`ChangeMasterPasswordDialog`](file:///ui/dialogs/change_master_password_dialog.py) | `ui/dialogs/change_master_password_dialog.py` | Overhaul (S-13) |
| 19 | `dlg_csv_import.png` | [`CSVImportDialog`](file:///ui/dialogs/csv_import_dialog.py) | `ui/dialogs/csv_import_dialog.py` | Overhaul (S-13) |
| 20 | `dlg_first_run.png` | [`FirstRunDialog`](file:///ui/dialogs/first_run_dialog.py) | `ui/dialogs/first_run_dialog.py` | Overhaul (S-13) |
| 21 | `dlg_force_update.png` | [`ForceUpdateDialog`](file:///ui/dialogs/update_dialog.py) | `ui/dialogs/update_dialog.py` | Overhaul (S-13) |
| 22 | `dlg_mcl_column_edit.png` | [`ColumnEditDialog`](file:///ui/dialogs/mcl_manager_dialog.py) | `ui/dialogs/mcl_manager_dialog.py` | Overhaul (S-5) |
| 23 | `dlg_mcl_manager.png` | [`MCLManagerDialog`](file:///ui/dialogs/mcl_manager_dialog.py) | `ui/dialogs/mcl_manager_dialog.py` | Overhaul (S-5) |
| 24 | `dlg_office_recovery.png` | [`OfficeRecoveryDialog`](file:///ui/dialogs/office_recovery_dialog.py) | `ui/dialogs/office_recovery_dialog.py` | Overhaul (S-13) |
| 25 | `dlg_rejoin_office.png` | [`RejoinOfficeDialog`](file:///ui/dialogs/rejoin_office_dialog.py) | `ui/dialogs/rejoin_office_dialog.py` | Overhaul (S-13) |
| 26 | `dlg_sera_sync.png` | [`SeraSyncDialog`](file:///ui/dialogs/sera_sync_dialog.py) | `ui/dialogs/sera_sync_dialog.py` | Overhaul (S-12) |
| 27 | `dlg_service_edit.png` | [`ServiceEditDialog`](file:///ui/dialogs/service_manager_dialog.py) | `ui/dialogs/service_manager_dialog.py` | Overhaul (S-5) |
| 28 | `dlg_service_manager.png` | [`ServiceManagerDialog`](file:///ui/dialogs/service_manager_dialog.py) | `ui/dialogs/service_manager_dialog.py` | Overhaul (S-5) |
| 29 | `dlg_settings_legacy.png` | [`SettingsDialog`](file:///ui/dialogs/settings_dialog.py) | `ui/dialogs/settings_dialog.py` | Superseded / Dropped |
| 30 | `dlg_startup_loading.png` | [`StartupLoadingDialog`](file:///ui/dialogs/loading_dialog.py) | `ui/dialogs/loading_dialog.py` | Overhaul (S-13) |
| 31 | *(no PNG)* | [`DisplayScaleDialog`](file:///ui/dialogs/display_scale_dialog.py) | `ui/dialogs/display_scale_dialog.py` | Overhaul (R-3) |
| 32 | *(no PNG)* | [`Sidebar`](file:///ui/shell/sidebar.py) Display Entry | `ui/shell/sidebar.py` | Overhaul (R-2) |
| 33 | `dlg_sgt_lab.png` | [`SgtLabDialog`](file:///ui/dialogs/sgt_lab_dialog.py) | `ui/dialogs/sgt_lab_dialog.py` | **Out of overhaul** |
| 34 | *(no PNG)* | [`SDISDialog`](file:///ui/dialogs/sdis_dialog.py) | `ui/dialogs/sdis_dialog.py` | **Out of overhaul** |
| 35 | *(no PNG)* | [`SDISContainersDialog`](file:///ui/dialogs/sdis_containers_dialog.py) | `ui/dialogs/sdis_containers_dialog.py` | **Out of overhaul** |

---

## 1. Daily Application Pages & Shell

### 1.1. All Clients / Search (`01_all_clients_search.png`)

- **Class & File:** [`SearchWindow`](file:///ui/windows/search_window.py) in `ui/windows/search_window.py`

- **How it is opened:** Default startup landing page in `AppShell` (content area page index 0); Sidebar 'All Clients' or 'Search' navigation buttons (`go_to_search`); Global shortcuts `Ctrl+K` or `Ctrl+F` (`_global_search_shortcut`); Back navigation from Admin Window or Client Detail Slide Panel (`_show_search_from_admin`, `_show_search_from_detail`).

- **Tests covering it:** `tests/test_ui_lock_search.py`, `tests/test_startup_speed.py`, `tests/test_ui_lock_settings.py`, `tests/test_undo_redo_formatting.py`, `tests/test_responsive.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `btn_toggle_sidebar` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> toggle_sidebar_requested.emit()`: Toggles collapsible navigation sidebar (Ctrl+B) |
| `btn_fill_color` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> _open_fill_menu_from_toolbar()`: Opens cell background color palette menu |
| `btn_text_color` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> _open_text_menu_from_toolbar()`: Opens cell text foreground color palette menu |
| `btn_clear_fmt` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> _clear_selected_formatting_from_toolbar()`: Clears fill and text colors on selected grid cells |
| `btn_undo` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> _undo_last_action()`: Undoes previous cell formatting operation (Ctrl+Z) |
| `btn_redo` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> _redo_last_action()`: Redoes undone cell formatting operation (Ctrl+Y) |
| `btn_add_client` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> add_client_requested.emit()`: Emits signal to open Add Client dialog |
| `btn_sync` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py, test_ui_lock_sync.py) | `clicked -> _on_manual_refresh()`: Manually refreshes search table from active database |
| `search_box` 🎯 | `QLineEdit` | **Yes** (test_startup_speed.py, test_ui_lock_search.py) | `textChanged -> _on_search_input_changed`, `returnPressed -> _activate_current_result`: Debounced (150ms) search across all columns |
| `service_filter` 🎯 | `QComboBox` | **Yes** (test_ui_lock_search.py) | `currentIndexChanged -> _on_search_input_changed`: Filters clients by attached service or preset |
| `btn_edit_client` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> _request_edit_client()`: (Admin only) Requests opening editor for selected client |
| `btn_delete_client` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> _request_delete_client()`: (Admin only) Requests permanent deletion of selected client |
| `btn_manage_services` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> _request_manage_services()`: (Admin only) Opens service attachment dialog for client |
| `btn_archive_client` 🎯 | `QPushButton` | **Yes** (test_ui_lock_search.py) | `clicked -> _request_archive_client()`: Toggles client archive status (Archive/Unarchive) |
| `results_table` 🎯 | `QTableWidget` | **Yes** (test_startup_speed.py, test_ui_lock_search.py (+1 more)) | `itemActivated -> _on_item_activated`, `customContextMenuRequested -> _show_cell_formatting_menu`: Main client results grid |
| `copy_shortcut` 🎯 | `QShortcut` | **Yes** (test_ui_lock_search.py) | `activated -> _copy_selection_to_clipboard()`: Copies selected cells or row text (Ctrl+C) |
| `undo_shortcut` 🎯 | `QShortcut` | **Yes** (test_ui_lock_search.py) | `activated -> _undo_last_action()`: Triggers formatting undo (Ctrl+Z) |
| `redo_shortcut` | `QShortcut` | No | `activated -> _redo_last_action()`: Triggers formatting redo (Ctrl+Y) |
| `_search_timer` 🎯 | `QTimer` | **Yes** (test_ui_lock_manage.py, test_ui_lock_search.py (+1 more)) | `timeout -> _on_search_changed`: 150ms debounce timer for keystrokes in search_box |
| `_scroll_save_timer` | `QTimer` | No | `timeout -> _save_scroll_position`: 200ms debounce timer saving grid scroll location |
| `_activity_refresh_timer` | `QTimer` | No | `timeout -> _on_activity_tick`: 60s periodic timer updating relative time tags |
| `_column_resize_timer` | `QTimer` | No | `timeout -> _adjust_column_widths`: 40ms timer recalculating responsive column widths |

### 1.2. Manage Clients (`02_manage_clients_admin.png`)

- **Class & File:** [`AdminWindow`](file:///ui/windows/admin_window.py) in `ui/windows/admin_window.py`

- **How it is opened:** Sidebar 'Manage Clients' navigation button (visible in Admin Mode); or triggered via SearchWindow client action buttons (`edit_client_requested`).

- **Tests covering it:** `tests/test_ui_lock_manage.py`, `tests/test_tracker_dump_autofill_button.py`, `tests/test_key_fingerprint.py`, `tests/test_sync_hotfix.py`, `tests/test_tracker_dump_columns.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `back_btn` 🎯 | `QPushButton` | **Yes** (test_ui_lock_detail.py) | `clicked -> back_requested.emit()`: Returns to All Clients / Search page (Esc) |
| `count_label` 🎯 | `QLabel` | **Yes** (test_ui_lock_manage.py) | Displays total client count in header (e.g. '14 clients') |
| `new_btn` 🎯 | `QPushButton` | **Yes** (test_ui_lock_manage.py) | `clicked -> _on_new()`: Starts new client creation form (Ctrl+N) |
| `conflict_banner_widget` 🎯 | `QWidget` | **Yes** (test_ui_lock_manage.py) | Syncthing conflict alert banner container (hidden when no conflicts) |
| `conflict_label` 🎯 | `QLabel` | **Yes** (test_ui_lock_manage.py) | Text warning label reporting detected database conflict files |
| `btn_inspect_conflict` | `QPushButton` | No | `clicked -> _on_inspect_conflicts()`: Opens conflict inspection modal |
| `search_input` 🎯 | `QLineEdit` | **Yes** (test_ui_lock_manage.py) | `textChanged -> _on_search_changed`: Debounced (120ms) search input for client list |
| `_filter_chips` 🎯 | `list[QPushButton]` | **Yes** (test_tracker_dump_columns.py, test_ui_lock_manage.py (+1 more)) | `clicked -> _apply_filter()`: Filter chips ('All', 'Has services', 'No services', 'Archived') |
| `more_filters_btn` 🎯 | `QToolButton` | **Yes** (test_ui_lock_manage.py) | ToolButton hosting popup menu for advanced preset filters |
| `_more_filters_menu` 🎯 | `QMenu` | **Yes** (test_ui_lock_manage.py) | Popup menu items: Most viewed, Active today, Has logins, Missing passwords, Services |
| `filter_combo` 🎯 | `QComboBox` | **Yes** (test_ui_lock_manage.py) | `currentIndexChanged -> refresh()`: Underlying hidden dropdown source of truth for filter |
| `show_archived_cb` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_manage.py) | `toggled -> _on_archive_toggle()`: Underlying hidden checkbox driving archived view |
| `sort_combo` 🎯 | `QComboBox` | **Yes** (test_ui_lock_manage.py) | `currentIndexChanged -> refresh()`: Dropdown selecting sort order (ID, Identity, Activity, Dates) |
| `table` 🎯 | `QTableWidget` | **Yes** (sync_harness.py, test_key_fingerprint.py (+20 more)) | `itemSelectionChanged -> _on_row_selected()`: Master client roster list table |
| `bulk_bar` 🎯 | `QFrame` | **Yes** (test_ui_lock_manage.py) | Bulk action bar container (visible when >= 2 clients selected) |
| `selection_label` 🎯 | `QLabel` | **Yes** (test_ui_lock_manage.py) | Displays selection count in bulk bar (e.g. '3 clients selected') |
| `bulk_svc_btn` | `QPushButton` | No | `clicked -> _on_bulk_service()`: Bulk attaches/detaches services across selected clients |
| `bulk_archive_btn` 🎯 | `QPushButton` | **Yes** (test_ui_lock_manage.py) | `clicked -> _on_archive()`: Bulk archives selected clients |
| `bulk_restore_btn` | `QPushButton` | No | `clicked -> _on_restore()`: Bulk restores selected archived clients |
| `bulk_purge_btn` 🎯 | `QPushButton` | **Yes** (test_ui_lock_manage.py) | `clicked -> _on_purge()`: Permanently deletes selected clients after confirmation |
| `bulk_clear_btn` | `QPushButton` | No | `clicked -> _on_new()`: Clears multi-selection |
| `editor_header` | `QFrame` | No | Header container for the right-hand client editor form |
| `editor_avatar` 🎯 | `QLabel` | **Yes** (test_ui_lock_manage.py) | Client initials / avatar badge |
| `editor_title` 🎯 | `QLabel` | **Yes** (test_ui_lock_manage.py) | Client display name title or 'New client' |
| `editor_sub` 🎯 | `QLabel` | **Yes** (test_ui_lock_manage.py) | Subtitle line displaying Client ID, PAN, created/updated timestamps |
| `dirty_label` 🎯 | `QLabel` | **Yes** (test_ui_lock_manage.py) | Displays '● Unsaved changes' warning indicator when form is edited |
| `more_btn` 🎯 | `QToolButton` | **Yes** (test_ui_lock_manage.py) | More ▾ dropdown button for single-client actions |
| `act_archive` | `QAction` | No | `triggered -> _on_archive()`: Archives current client |
| `act_restore` 🎯 | `QAction` | **Yes** (test_ui_lock_manage.py) | `triggered -> _on_restore()`: Restores current archived client |
| `act_purge` 🎯 | `QAction` | **Yes** (test_ui_lock_manage.py) | `triggered -> _on_purge()`: Permanently deletes current client |
| `save_btn` 🎯 | `QPushButton` | **Yes** (test_ui_lock_manage.py) | `clicked -> _on_save()`: Validates and saves client record to database (Ctrl+S) |
| `form_widget` 🎯 | `QWidget` | **Yes** (test_ui_lock_manage.py) | Canvas holding dynamic card sections |
| `_cards` 🎯 | `list[QFrame]` | **Yes** (test_ui_lock_manage.py) | Section cards (Identity, Portal Logins, Contact, Other, Services, Notes) |
| `_input_widgets` 🎯 | `dict` | **Yes** (test_ui_lock_manage.py) | Dictionary of dynamically generated input widgets keyed by MCL column ID |
| `_service_cbs` 🎯 | `dict` | **Yes** (test_ui_lock_manage.py) | Dictionary of service attachment checkboxes / chips |
| `f_notes` 🎯 | `QTextEdit` | **Yes** (test_ui_lock_manage.py) | `textChanged -> _on_form_edited`: Client administrative notes editor |
| `_search_timer` 🎯 | `QTimer` | **Yes** (test_ui_lock_manage.py, test_ui_lock_search.py (+1 more)) | `timeout -> refresh()`: 120ms debounce timer for list search |
| `_activity_timer` | `QTimer` | No | `timeout -> _refresh_activity_tags()`: 60s periodic timer updating activity badges |

### 1.3. Tracker Dump (`03_tracker_dump.png`)

- **Class & File:** [`TrackerDumpWindow`](file:///ui/windows/tracker_dump_window.py) in `ui/windows/tracker_dump_window.py`

- **How it is opened:** Sidebar 'Tracker Dump' navigation button in `AppShell` (page index 2).

- **Tests covering it:** `tests/test_ui_lock_tracker.py`, `tests/test_tracker_dump_columns.py`, `tests/test_tracker_dump_status.py`, `tests/test_tracker_dump_autofill_button.py`, `tests/test_tracker_refresh.py`, `tests/test_sgt_tracker_rows.py`, `tests/test_sgt_containers.py`, `tests/test_raw_payload_db_and_srpf.py`, `tests/test_startup_speed.py`, `tests/test_sdis_dialog.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `btn_refresh` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> load_data()`: Reloads tracker captures from database (F5) |
| `btn_preferences` 🎯 | `QPushButton` | **Yes** (test_ui_lock_tracker.py) | `clicked -> _show_preferences_menu()`: Opens Tools ▾ menu (SRPF Re-resolve, Map MCL, LTT, Export CSV, Distill, Clear) |
| `_summary_tiles` 🎯 | `dict` | **Yes** (test_ui_lock_tracker.py) | `clicked -> _on_summary_tile(key)`: 5 filter tiles (Total, Need Client, Pending e-Ver, Not Submitted, Today) |
| `cmb_view_mode` 🎯 | `QComboBox` | **Yes** (test_tracker_dump_status.py, test_tracker_refresh.py (+1 more)) | `currentIndexChanged -> load_data`, `_sync_view_buttons`: Hidden source of truth (Grouped SRPF Containers vs Raw) |
| `_seg_buttons` 🎯 | `list` | **Yes** (test_tracker_dump_columns.py, test_ui_lock_tracker.py) | Segmented push buttons ('Containers', 'Raw captures') driving view mode |
| `txt_search` 🎯 | `QLineEdit` | **Yes** (test_tracker_dump_columns.py, test_tracker_refresh.py (+2 more)) | `textChanged -> _on_search_text_changed`: Full-text search (Client, PAN, GSTIN, ARN, Period, Portal) (180ms debounce) |
| `cmb_status` 🎯 | `QComboBox` | **Yes** (test_tracker_dump_status.py, test_ui_lock_tracker.py) | `currentIndexChanged -> _on_filter_changed`: Status filter dropdown (E-verified, Submitted, Pending, Not submitted) |
| `cmb_portal` 🎯 | `QComboBox` | **Yes** (test_ui_lock_tracker.py) | `currentIndexChanged -> _on_filter_changed`: Portal filter dropdown (Income Tax, GST, TRACES/TDS) |
| `cmb_client` 🎯 | `QComboBox` | **Yes** (test_ui_lock_tracker.py) | `currentIndexChanged -> _on_filter_changed`: Client assignment status filter (Registered vs Unregistered) |
| `cmb_date` 🎯 | `QComboBox` | **Yes** (test_tracker_refresh.py, test_ui_lock_tracker.py) | `currentIndexChanged -> _on_filter_changed`: Capture recency filter (All Time, Today, Past 7d, Past 30d) |
| `cmb_source` 🎯 | `QComboBox` | **Yes** (test_ui_lock_tracker.py) | `currentIndexChanged -> _on_filter_changed`: Engine source filter (All Sources, Hide SGT, SGT Only) |
| `_filter_chips` 🎯 | `list` | **Yes** (test_tracker_dump_columns.py, test_ui_lock_manage.py (+1 more)) | Filter chips displaying active filter values and hosting popup menus |
| `btn_reset_filters` 🎯 | `QPushButton` | **Yes** (test_ui_lock_tracker.py) | `clicked -> _reset_filters()`: Resets all filter dropdowns and search inputs to defaults |
| `table` 🎯 | `QTableWidget` | **Yes** (sync_harness.py, test_key_fingerprint.py (+20 more)) | `cellDoubleClicked -> _on_cell_double_clicked`, `customContextMenuRequested -> _on_table_context_menu`: Dark-themed 7-column captures grid |
| `lbl_page_info` 🎯 | `QLabel` | **Yes** (test_ui_lock_tracker.py) | Paging status label ('Showing X to Y of Z entries') |
| `cmb_page_size` 🎯 | `QComboBox` | **Yes** (test_ui_lock_tracker.py) | `currentIndexChanged -> _on_page_size_changed`: Rows-per-page selector (25, 50, 100, 200, All) |
| `btn_prev_page` 🎯 | `QPushButton` | **Yes** (test_ui_lock_tracker.py) | `clicked -> _prev_page()`: Paginates to previous page |
| `lbl_current_page` 🎯 | `QLabel` | **Yes** (test_ui_lock_tracker.py) | Current page indicator label ('Page X / Y') |
| `btn_next_page` 🎯 | `QPushButton` | **Yes** (test_ui_lock_tracker.py) | `clicked -> _next_page()`: Paginates to next page |
| `_search_timer` 🎯 | `QTimer` | **Yes** (test_ui_lock_manage.py, test_ui_lock_search.py (+1 more)) | `timeout -> _apply_filters`: 180ms debounce timer for txt_search |
| `_col_resize_timer` | `QTimer` | No | `timeout -> _adjust_table_columns`: 40ms timer adapting columns to window width |
| `_resolve_timer` | `QTimer` | No | `timeout -> _start_periodic_resolve`: 30s background timer for automatic periodic SRPF re-resolution |

### 1.4. Client Detail Panel (`04_client_detail_panel.png`)

- **Class & File:** [`ClientDetailWindow`](file:///ui/windows/client_detail_window.py) in `ui/windows/client_detail_window.py`

- **How it is opened:** Slide-out drawer over `SearchWindow` in `AppShell` (`slide_panel.slide_in()`) triggered by clicking or activating a client row in Search or Tracker Dump.

- **Tests covering it:** `tests/test_ui_lock_detail.py`, `tests/test_tracker_dump_autofill_button.py`, `tests/test_sca_suppress_client.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `back_btn` 🎯 | `QPushButton` | **Yes** (test_ui_lock_detail.py) | `clicked -> _safe_back_request()`: Slides panel out / closes detail view (`back_requested.emit`) |
| `back_shortcut` 🎯 | `QShortcut` | **Yes** (test_ui_lock_detail.py) | `activated -> _safe_back_request()`: Esc key shortcut dismissing panel |
| `primary_name_lbl` 🎯 | `QLabel` | **Yes** (test_ui_lock_detail.py) | Displays primary company / entity name |
| `secondary_name_lbl` 🎯 | `QLabel` | **Yes** (test_ui_lock_detail.py) | Displays proprietor subtitle and meta line |
| `token_badge` 🎯 | `QLabel` | **Yes** (test_ui_lock_detail.py) | `mousePressEvent -> _copy_token()`: Clickable token ID chip copying identifier to clipboard |
| `scroll_widget` 🎯 | `QWidget` | **Yes** (test_ui_lock_detail.py) | Container widget hosting detail cards |
| `scroll_layout` | `QVBoxLayout` | No | Vertical layout hosting identity, login, service, and notes cards |
| `_service_shortcuts` 🎯 | `list[QShortcut]` | **Yes** (test_ui_lock_detail.py) | Shortcuts Alt+1 through Alt+9 triggering respective compliance service actions |
| `Service Autofill Btn` | `QPushButton` | **Yes** (`test_ui_lock_detail.py`) | `clicked -> _launch_extension_autofill()`: Launches browser extension fast autofill for service |
| `Service Assist Btn` | `QPushButton` | **Yes** (`test_ui_lock_detail.py`) | `clicked -> _launch_manual_assist()`: Triggers SMTI manual assist overlay |
| `Service Copy Btn` | `QPushButton` | **Yes** (`test_ui_lock_detail.py`) | `clicked -> _launch_manual_copy()`: Triggers MECP manual credential copy card |
| `Field Copy Btns` | `QPushButton` | **Yes** (`test_ui_lock_detail.py`) | `clicked -> _copy_val()`: Copies individual field values (PAN, GSTIN, User ID, Password) to clipboard |
| `Field Eye Btns` | `QPushButton` | **Yes** (`test_ui_lock_detail.py`) | `clicked -> _toggle_mask()`: Toggles credential masking/unmasking per Settings rules |
| `notes_edit` 🎯 | `QTextEdit` | **Yes** (test_ui_lock_detail.py) | `textChanged -> _on_notes_text_changed`: Multiline client notes editor with auto-save |
| `notes_status_lbl` 🎯 | `QLabel` | **Yes** (test_ui_lock_detail.py) | Displays auto-save feedback ('Saving...' / 'Saved') |
| `_notes_timer` | `QTimer` | No | `timeout -> _auto_save_notes`: 1000ms debounce timer committing edited notes to database |

---

## 2. Unified Settings Hub (`ui/dialogs/unified_settings_dialog.py`)

All 12 settings screens are unified inside [`UnifiedSettingsDialog`](file:///ui/dialogs/unified_settings_dialog.py) as pages in a master navigation stack.

- **How it is opened:** Sidebar 'Options' button (Admin mode); or programmatically from AdminWindow or Settings buttons with `page=...`.

- **Tests covering it:** `tests/test_ui_lock_settings.py`, `tests/test_unified_settings_lazy.py`, `tests/test_scc_vault_tagger.py`, `tests/test_office_recovery.py`, `tests/test_vsdc_hud_switch.py`, `tests/test_ui_lock_manage.py`.

### 2.0. Settings Shell / Nav Controls

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `_search` 🎯 | `QLineEdit` | **Yes** (test_ui_lock_settings.py) | `textChanged -> _filter_nav()`: Nav search filter filtering settings page list |
| `_nav_items` 🎯 | `list[_NavItem]` | **Yes** (test_ui_lock_settings.py) | Navigation list items for the 12 settings pages |
| `_stack` 🎯 | `QStackedWidget` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py (+1 more)) | Page stack container displaying active settings page |
| `_btn_save` 🎯 | `QPushButton` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py (+1 more)) | `clicked -> _on_save_settings()`: Commits staged settings changes to app_settings |
| `Close Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> reject()`: Closes settings hub (prompts if unsaved changes exist) |

### 2.1. General Settings (`settings_general.png`)

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `theme_combo` | `QComboBox` | No | `currentIndexChanged -> _on_control_changed`: Application theme selector ('Dark' only) |
| `window_mode_combo` 🎯 | `QComboBox` | **Yes** (test_ui_lock_settings.py) | `currentIndexChanged -> _on_control_changed`: Startup window mode (Fullscreen, Square, Rectangular) |
| `DisplayScaleControl` | `QWidget` | **Yes** (`test_ui_scale.py`) | Embedded per-PC scale controls (Automatic..125%, Apply, Restart now) |
| `mask_mode_combo` 🎯 | `QComboBox` | **Yes** (test_ui_lock_settings.py) | `currentIndexChanged -> _on_control_changed`: Password mask mode (Last N, First N, Full Dots) |
| `reveal_count_spin` 🎯 | `QSpinBox` | **Yes** (test_ui_lock_settings.py) | `valueChanged -> _on_control_changed`: Number of unmasked characters shown in password |
| `clipboard_spin` 🎯 | `QSpinBox` | **Yes** (test_ui_lock_settings.py, test_unified_settings_lazy.py) | `valueChanged -> _on_control_changed`: Clipboard auto-wipe interval in seconds |
| `quick_copy_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py) | `toggled -> _on_control_changed`: Master switch enabling/disabling Quick-Copy |
| `run_in_bg_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py) | `toggled -> _on_control_changed`: Minimize to system tray on window close |
| `autostart_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py) | `toggled -> _on_control_changed`: Launch Sera automatically on Windows boot |
| `sca_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py) | `toggled -> _on_control_changed`: Master switch for Sera Clipboard Assist (SCA) |
| `sca_mode_combo` 🎯 | `QComboBox` | **Yes** (test_ui_lock_settings.py) | `currentIndexChanged -> _on_control_changed`: SCA action mode (Ambient Autofill vs SCA Widget) |
| `sca_max_uses_spin` 🎯 | `QSpinBox` | **Yes** (test_ui_lock_settings.py) | `valueChanged -> _on_control_changed`: Maximum allowable SCA autofills per copied UID |
| `Schema ID Status` | `QLabel` | No | Read-only indicator showing active Primary Key anchor column |

### 2.2. Action Buttons Settings (`settings_actions.png`)

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `show_hide_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py) | `toggled -> _on_control_changed`: Toggle 'Show/Hide' password eye buttons in Client Detail view |

### 2.3. SCC Vault Presets (`settings_scc.png`)

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `scc_check` 🎯 | `QCheckBox` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py (+1 more)) | `toggled -> _on_control_changed`: Master toggle for SCC One-Time Verification |
| `scc_detect_combo` 🎯 | `QComboBox` | **Yes** (test_ui_lock_settings.py) | `currentIndexChanged -> _on_control_changed`: Automatic portal login detection (Off / On) |
| `scc_counts_label` | `QLabel` | No | Summary count stats across the 8 verification outcomes |
| `scc_opt1_label_edit` 🎯 | `QLineEdit` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py) | `textChanged -> _on_control_changed`: Option 1 formula label |
| `scc_opt1_str_edit` 🎯 | `QLineEdit` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py) | `textChanged -> _on_control_changed`: Option 1 fixed formula piece |
| `scc_opt2_label_edit` 🎯 | `QLineEdit` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py) | `textChanged -> _on_control_changed`: Option 2 formula label |
| `scc_opt2_str_edit` 🎯 | `QLineEdit` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py) | `textChanged -> _on_control_changed`: Option 2 fixed formula piece |
| `scc_opt3_label_edit` 🎯 | `QLineEdit` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py) | `textChanged -> _on_control_changed`: Option 3 formula label |
| `scc_opt3_str_edit` 🎯 | `QLineEdit` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py) | `textChanged -> _on_control_changed`: Option 3 fixed formula string |
| `scc_opt4_label_edit` 🎯 | `QLineEdit` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py) | `textChanged -> _on_control_changed`: Option 4 formula label |
| `scc_opt4_str_edit` 🎯 | `QLineEdit` | **Yes** (test_scc_vault_tagger.py, test_ui_lock_settings.py) | `textChanged -> _on_control_changed`: Option 4 fixed formula string |
| `scc_sample_pan_edit` | `QLineEdit` | No | `textChanged -> _update_previews`: Test PAN input driving live formula resolution |
| `scc_preview_lbl1..4` | `QLabel` | No | Resolved password preview lines for Options 1-4 |

### 2.4. Master Column List (MCL) (`settings_mcl.png`)

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `_mcl_list` | `QListWidget` | No | `currentRowChanged -> _mcl_on_sel_changed`: Schema column list with type tags |
| `Add Column Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> _on_mcl_add()`: Opens `ColumnEditDialog` to add a new column |
| `Edit Column Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> _on_mcl_edit()`: Opens `ColumnEditDialog` for selected column |
| `Delete Column Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> _on_mcl_delete()`: Deletes selected column after confirmation |
| `Move Up Btn` | `QPushButton` | No | `clicked -> _on_mcl_move_up()`: Reorders column up in schema |
| `Move Down Btn` | `QPushButton` | No | `clicked -> _on_mcl_move_down()`: Reorders column down in schema |

### 2.5. Services Catalog (`settings_services.png`)

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `_svc_list` | `QListWidget` | No | `currentRowChanged -> _svc_on_sel_changed`: Compliance services list with mode tags |
| `Add Service Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> _on_service_add()`: Opens `ServiceEditDialog` to create service |
| `Edit Service Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> _on_service_edit()`: Opens `ServiceEditDialog` for selected service |
| `Delete Service Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> _on_service_delete()`: Deletes service after confirmation |

### 2.6. Column Visibility: Main Screen (`settings_main_vis.png`)

- **Controls:** Dynamic list of column checkboxes stored in `self.vis_cbs` (`dict[int, QCheckBox]`).

- **Tested in tests/?:** **Yes** (test_ui_lock_settings.py)

- **Behavior:** Toggles `show_in_search` flag in database for each column; determines visibility on main search grid.

### 2.7. Column Visibility: Quick-Copy (`settings_qc.png`)

- **Controls:** Dynamic list of column checkboxes stored in `self.qc_cbs` (`dict[int, QCheckBox]`).

- **Tested in tests/?:** **Yes** (test_ui_lock_settings.py)

- **Behavior:** Toggles `allow_quick_copy` flag in database; controls click-to-copy capability on Search grid and Client Detail.

### 2.8. Column Visibility: Admin Screen (`settings_admin_vis.png`)

- **Controls:** Dynamic list of column checkboxes stored in `self.admin_vis_cbs` (`dict[int, QCheckBox]`).

- **Tested in tests/?:** **Yes** (test_ui_lock_settings.py)

- **Behavior:** Toggles `admin_show_in_search` flag in database; controls visibility on Manage Clients table.

### 2.9. Export CSV (`settings_export.png`)

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `Export All Clients Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> _on_export_csv()`: Opens save dialog and exports client database to CSV |
| `Download Template Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> _on_download_template()`: Exports blank CSV schema template with current MCL columns |

### 2.10. Backup & Restore (`settings_backup.png`)

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `Choose Backup Folder Btn` | `QPushButton` | No | `clicked -> _on_choose_backup_dir()`: Chooses automated local backup directory |
| `Export Recovery Kit Btn` | `QPushButton` | **Yes** (`test_office_recovery.py`) | `clicked -> _on_export_recovery_kit()`: Prompts for master password and exports `.serakit` file |
| `Change Password Btn` | `QPushButton` | **Yes** (`test_office_recovery.py`) | `clicked -> _on_change_master_password()`: Opens `ChangeMasterPasswordDialog` |
| `Restore Backup Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> _on_restore_backup()`: Prompts confirmation and restores encrypted database from backup |

### 2.11. Purge Duplicates (`settings_purge.png`)

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `Run Deduplication Btn` | `QPushButton` | **Yes** (`test_ui_lock_settings.py`) | `clicked -> _on_purge_duplicates()`: Danger zone action: prompts confirmation and purges duplicate records |

### 2.12. Tracker Settings (`settings_tracker.png`)

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `sgt_mode_combo` 🎯 | `QComboBox` | **Yes** (test_ui_lock_settings.py) | `currentIndexChanged -> _on_control_changed`: SGT operational mode (Live / Shadow) |
| `sgt_record_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py) | `toggled -> _on_control_changed`: Record HTML pages for SGT offline testing |
| `sdis_record_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py) | `toggled -> _on_control_changed`: Record HTML pages for Sera Distill (SDIS) |
| `sgt_i_combo` 🎯 | `QComboBox` | **Yes** (test_ui_lock_settings.py) | `currentIndexChanged -> _on_control_changed`: SGT-I Smart Form Field Resolution (Off / On) |
| `Open SGT Lab Btn` | `QPushButton` | **Yes** (`test_sgt_i_lab.py`) | `clicked -> _open_sgt_lab()`: Opens `SgtLabDialog` diagnostic window |
| `vsdc_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py) | `toggled -> _on_control_changed`: Enable Visual SDC (VSDC) |
| `vsdc_x_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py) | `toggled -> _on_control_changed`: Enable Visual SDC Exact (VSDC-X) |
| `vsdc247_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py) | `toggled -> _on_control_changed`: Enable VSDC 24/7 background capture daemon |
| `vsdc_hud_check` 🎯 | `QCheckBox` | **Yes** (test_ui_lock_settings.py, test_vsdc_hud_switch.py) | `toggled -> _on_control_changed`: Show on-screen toast HUD notification for VSDC/SGT captures |

---

## 3. Dialogs

### 3.1. Audit Log (`dlg_audit_log.png`)

- **Class & File:** [`AuditLogDialog`](file:///ui/dialogs/audit_log_dialog.py) in `ui/dialogs/audit_log_dialog.py`

- **How it is opened:** Sidebar 'Audit Log' navigation button (Admin mode only).

- **Tests covering it:** `tests/test_ui_lock_audit.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `btn_close_top` | `QPushButton` | No | `clicked -> accept()`: Closes dialog |
| `host_badge` | `QLabel` | No | Host aggregator connection status badge |
| `workstation_list` 🎯 | `QListWidget` | **Yes** (test_ui_lock_audit.py) | `currentItemChanged -> _on_workstation_changed`: Workstation selection filter list |
| `txt_search` 🎯 | `QLineEdit` | **Yes** (test_tracker_dump_columns.py, test_tracker_refresh.py (+2 more)) | `textChanged -> _on_search_changed`: Full-text search (Client, PAN, Actor, Action, Service, Details) (180ms debounce) |
| `action_combo` 🎯 | `QComboBox` | **Yes** (test_ui_lock_audit.py) | `currentIndexChanged -> _load_logs()`: Filter by 18 audit action types |
| `actor_input` 🎯 | `QLineEdit` | **Yes** (test_ui_lock_audit.py) | `textChanged -> _on_search_changed`: Filter by staff/actor username |
| `btn_refresh` | `QPushButton` | No | `clicked -> _load_logs()`: Refreshes audit logs from database |
| `btn_export` | `QPushButton` | No | `clicked -> _on_export_log_csv()`: Exports filtered audit logs to CSV |
| `cmb_date_preset` 🎯 | `QComboBox` | **Yes** (test_ui_lock_audit.py) | `currentIndexChanged -> _on_date_preset_changed`: Date range presets (All Time, Today, Yesterday, 7d, 30d, Custom) |
| `dt_from` 🎯 | `QDateEdit` | **Yes** (test_ui_lock_audit.py) | `dateChanged -> _on_custom_date_changed`: Custom range start date |
| `dt_to` 🎯 | `QDateEdit` | **Yes** (test_ui_lock_audit.py) | `dateChanged -> _on_custom_date_changed`: Custom range end date |
| `lbl_record_count` 🎯 | `QLabel` | **Yes** (test_ui_lock_audit.py) | Log count indicator label ('N log entries') |
| `table` 🎯 | `QTableWidget` | **Yes** (sync_harness.py, test_key_fingerprint.py (+20 more)) | `itemSelectionChanged -> _on_row_selected()`: 7-column audit log grid (ID, Date/Time, Actor, Action, Client, Service, Detail) |
| `inspector_box` 🎯 | `QTextEdit` | **Yes** (test_ui_lock_audit.py) | Formatted JSON/text drill-down details viewer for selected log entry |
| `btn_copy_insp` 🎯 | `QPushButton` | **Yes** (test_ui_lock_audit.py) | `clicked -> _on_copy_inspector()`: Copies inspector details to clipboard |
| `btn_filter_client` 🎯 | `QPushButton` | **Yes** (test_ui_lock_audit.py) | `clicked -> _on_filter_by_selected_client()`: Filters log to show only the selected client |
| `_search_timer` 🎯 | `QTimer` | **Yes** (test_ui_lock_manage.py, test_ui_lock_search.py (+1 more)) | `timeout -> _apply_client_side_filter()`: 180ms debounce timer for text filtering |

### 3.2. Change Master Password (`dlg_change_master_password.png`)

- **Class & File:** [`ChangeMasterPasswordDialog`](file:///ui/dialogs/change_master_password_dialog.py) in `ui/dialogs/change_master_password_dialog.py`

- **How it is opened:** Settings -> Backup & Restore -> 'Change Password' button.

- **Tests covering it:** `tests/test_office_recovery.py`, `tests/test_ui_lock_sync.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `current_pw` 🎯 | `QLineEdit` | **Yes** (test_office_recovery.py) | Current office master password input (echo mode: Password) |
| `new_pw` 🎯 | `QLineEdit` | **Yes** (test_office_recovery.py) | New master password input (min 8 characters) |
| `confirm_pw` 🎯 | `QLineEdit` | **Yes** (test_office_recovery.py) | Confirm new master password input |
| `lbl_error` 🎯 | `QLabel` | **Yes** (test_office_recovery.py) | Validation error message label |
| `btn_cancel` | `QPushButton` | No | `clicked -> reject()`: Cancels dialog |
| `btn_save` | `QPushButton` | No | `clicked -> _on_save()`: Validates password strength and re-encrypts office key |

### 3.3. CSV Import (`dlg_csv_import.png`)

- **Class & File:** [`CSVImportDialog`](file:///ui/dialogs/csv_import_dialog.py) & `ImportResultDialog` in `ui/dialogs/csv_import_dialog.py`

- **How it is opened:** Sidebar 'Import CSV' button (Tools group); or from Settings -> Import.

- **Tests covering it:** `tests/test_startup_speed.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `file_label` | `QLabel` | No | Displays selected CSV file path or 'No CSV file selected' |
| `browse_btn` | `QPushButton` | No | `clicked -> _on_browse()`: Opens file browser dialog to pick CSV file |
| `table` 🎯 | `QTableWidget` | **Yes** (sync_harness.py, test_key_fingerprint.py (+20 more)) | Column mapping preview table (CSV Column Header -> Target MCL Column) |
| `close_btn` | `QPushButton` | No | `clicked -> reject()`: Cancels dialog |
| `import_btn` | `QPushButton` | No | `clicked -> _on_import()`: Executes batch client import and shows `ImportResultDialog` |

### 3.4. First Run Setup (`dlg_first_run.png`)

- **Class & File:** [`FirstRunDialog`](file:///ui/dialogs/first_run_dialog.py) in `ui/dialogs/first_run_dialog.py`

- **How it is opened:** Application launch when no `master.db` exists in `APP_DIR`.

- **Tests covering it:** `tests/test_startup_key_resolution.py`, `tests/test_sync_office_ui.py`, `tests/test_sync_hotfix.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `stack` 🎯 | `QStackedWidget` | **Yes** (test_sdis_tables.py, test_sync_office_ui.py) | Step container stack (Page 0: Choice, Page 1: New Office, Page 2: Join Office) |
| `new_btn` | `QPushButton` | No | `clicked -> stack.setCurrentIndex(1)`: Selects 'Create a New Office' step |
| `join_btn` | `QPushButton` | No | `clicked -> _start_office_join_page()`: Selects 'Join an Existing Office' step |
| `exit_btn` | `QPushButton` | No | `clicked -> reject()`: Quits application |
| `office_name_input` 🎯 | `QLineEdit` | **Yes** (test_sync_office_ui.py) | Office name input (Page 1) |
| `office_new_pwd_input` 🎯 | `QLineEdit` | **Yes** (test_sync_office_ui.py) | Office master password input (Page 1) |
| `office_new_pwd_confirm` 🎯 | `QLineEdit` | **Yes** (test_sync_office_ui.py) | Confirm master password input (Page 1) |
| `office_new_error_label` 🎯 | `QLabel` | **Yes** (test_sync_office_ui.py) | Validation error label (Page 1) |
| `office_create_btn` | `QPushButton` | No | `clicked -> _handle_create_office_v3()`: Initializes office master database (Page 1) |
| `office_scan_btn` | `QPushButton` | No | `clicked -> _scan_office_peers()`: Scans LAN for open pairing beacons (Page 2) |
| `office_peers_table` | `QTableWidget` | No | Table of discovered admin PCs broadcasting pairing windows (Page 2) |
| `office_manual_ip_input` 🎯 | `QLineEdit` | **Yes** (test_sync_office_ui.py) | Manual IP address entry for admin PC (Page 2) |
| `office_code_input` 🎯 | `QLineEdit` | **Yes** (test_sync_office_ui.py) | 6-digit pairing code input (Page 2) |
| `office_join_status_label` 🎯 | `QLabel` | **Yes** (test_sync_office_ui.py) | Pairing connection status and error label (Page 2) |
| `office_join_progress` | `QProgressBar` | No | Progress bar during database snapshot download (Page 2) |
| `office_join_btn` | `QPushButton` | No | `clicked -> _handle_office_join()`: Initiates pairing and snapshot download (Page 2) |

### 3.5. Force Update (`dlg_force_update.png`)

- **Class & File:** [`ForceUpdateDialog`](file:///ui/dialogs/update_dialog.py) in `ui/dialogs/update_dialog.py`

- **How it is opened:** Modal update gate displayed when mandatory version update is reported by GitHub API or LAN peer update beacon.

- **Tests covering it:** `tests/test_updater.py`, `tests/test_update_agent.py`, `tools/ui_snapshots.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `notes_txt` | `QTextEdit` | No | Read-only release notes container |
| `progress_bar` | `QProgressBar` | No | Payload download progress bar |
| `status_label` 🎯 | `QLabel` | **Yes** (test_tracker_dump_status.py, test_tracker_refresh.py (+2 more)) | Update status text message |
| `exit_btn` | `QPushButton` | No | `clicked -> _on_exit_clicked()`: Confirms and quits application |
| `update_btn` | `QPushButton` | No | `clicked -> _start_download()`: Downloads update installer and launches setup executable |

### 3.6. Column Edit (`dlg_mcl_column_edit.png`)

- **Class & File:** [`ColumnEditDialog`](file:///ui/dialogs/mcl_manager_dialog.py) in `ui/dialogs/mcl_manager_dialog.py`

- **How it is opened:** Opened from `MCLManagerDialog` or `UnifiedSettingsDialog` MCL page via 'Add Column' or 'Edit'.

- **Tests covering it:** Tested indirectly via `tests/test_ui_lock_settings.py` and `tools/ui_snapshots.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `label_input` | `QLineEdit` | No | Column name label input |
| `type_combo` | `QComboBox` | No | `currentIndexChanged -> _update_options_visibility()`: Data type selector (Text, Number, Alphanumeric, Password, Dropdown, Date, ID) |
| `options_label` | `QLabel` | No | Label for dropdown options (visible only for Dropdown type) |
| `options_input` | `QLineEdit` | No | Comma-separated dropdown choices (visible only for Dropdown type) |
| `identity_cb` | `QCheckBox` | No | Toggle 'Display identity column' |
| `internal_pk_cb` | `QCheckBox` | No | Toggle 'Internal Primary Key Anchor' |
| `cancel_btn` 🎯 | `QPushButton` | **Yes** (test_sdis_dialog.py) | `clicked -> reject()`: Cancels dialog |
| `save_btn` | `QPushButton` | No | `clicked -> _on_accept()`: Validates and accepts column configuration |

### 3.7. MCL Manager (`dlg_mcl_manager.png`)

- **Class & File:** [`MCLManagerDialog`](file:///ui/dialogs/mcl_manager_dialog.py) in `ui/dialogs/mcl_manager_dialog.py`

- **How it is opened:** Standalone schema management dialog opened from Admin window.

- **Tests covering it:** `tools/ui_snapshots.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `col_list` | `QListWidget` | No | List of columns with type and role badges |
| `add_btn` | `QPushButton` | No | `clicked -> _on_add()`: Opens `ColumnEditDialog` to add a new column |
| `edit_btn` | `QPushButton` | No | `clicked -> _on_edit()`: Opens `ColumnEditDialog` to edit selected column |
| `del_btn` | `QPushButton` | No | `clicked -> _on_delete()`: Confirms and deletes selected column |
| `up_btn` | `QPushButton` | No | `clicked -> _on_move(-1)`: Moves selected column up in display order |
| `down_btn` | `QPushButton` | No | `clicked -> _on_move(1)`: Moves selected column down in display order |
| `close_btn` | `QPushButton` | No | `clicked -> accept()`: Closes dialog |

### 3.8. Office Recovery (`dlg_office_recovery.png`)

- **Class & File:** [`OfficeRecoveryDialog`](file:///ui/dialogs/office_recovery_dialog.py) in `ui/dialogs/office_recovery_dialog.py`

- **How it is opened:** Application launch when Windows DPAPI cannot decrypt the office key.

- **Tests covering it:** `tests/test_office_recovery.py`, `tests/test_startup_key_resolution.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `pw_edit` 🎯 | `QLineEdit` | **Yes** (test_office_recovery.py) | `returnPressed -> _on_unlock()`: Master password entry (echo mode: Password) |
| `btn_toggle_pw` | `QPushButton` | No | `clicked -> _toggle_password_visibility()`: Eye button toggling password visibility |
| `lbl_error` 🎯 | `QLabel` | **Yes** (test_office_recovery.py) | Error and attempts remaining display label |
| `btn_restore_kit` | `QPushButton` | No | `clicked -> _on_restore_kit()`: Opens file chooser to restore from `.serakit` recovery file |
| `btn_exit` | `QPushButton` | No | `clicked -> reject()`: Quits application |
| `btn_unlock` | `QPushButton` | No | `clicked -> _on_unlock()`: Validates password and recovers office DEK |

### 3.9. Rejoin Office (`dlg_rejoin_office.png`)

- **Class & File:** [`RejoinOfficeDialog`](file:///ui/dialogs/rejoin_office_dialog.py) in `ui/dialogs/rejoin_office_dialog.py`

- **How it is opened:** From Sera Sync -> Devices -> 'Rejoin Office' button; or during startup if a pending rejoin is detected.

- **Tests covering it:** `tests/test_sync_rejoin.py`, `tests/test_startup_key_resolution.py`, `tests/test_ui_lock_sync.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `legacy_pw` 🎯 | `QLineEdit` | **Yes** (test_sync_rejoin.py) | Current local database password input (if verification required) |
| `host` 🎯 | `QLineEdit` | **Yes** (test_clipboard_assist.py, test_key_fingerprint.py (+9 more)) | Admin PC IP address input |
| `code` 🎯 | `QLineEdit` | **Yes** (sync_harness.py, test_key_fingerprint.py (+9 more)) | 6-digit pairing code input |
| `device_name` 🎯 | `QLineEdit` | **Yes** (test_sgt_shadow.py, test_sgt_tracker_rows.py (+3 more)) | This workstation's name input |
| `error` 🎯 | `QLabel` | **Yes** (sync_harness.py, test_network_category_probe.py (+6 more)) | Validation and connection error label |
| `buttons` 🎯 | `QDialogButtonBox` | **Yes** (test_ui_lock_settings.py) | `accepted -> _on_ok()`, `rejected -> reject()`: Rejoin and Cancel buttons |

### 3.10. Sera Sync (`dlg_sera_sync.png`)

- **Class & File:** [`SeraSyncDialog`](file:///ui/dialogs/sera_sync_dialog.py) in `ui/dialogs/sera_sync_dialog.py`

- **How it is opened:** Clicking profile row / avatar or auto-sync status pill in sidebar (Admin mode); or via Admin actions.

- **Tests covering it:** `tests/test_ui_lock_sync.py`, `tests/test_sync_office_ui.py`, `tests/test_sync_status_panel_ui.py`, `tests/test_sync_golive_ui.py`, `tests/test_sync_shadow_start_ui.py`, `tests/test_sync_rejoin.py`, `tests/test_sync_hotfix.py`, `tests/test_key_fingerprint.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `status_label` 🎯 | `QLabel` | **Yes** (test_tracker_dump_status.py, test_tracker_refresh.py (+2 more)) | Online workstation count banner |
| `network_warning_banner` 🎯 | `QLabel` | **Yes** (test_ui_lock_sync.py) | Network interface / subnet mismatch warning banner |
| `tabs` 🎯 | `QTabWidget` | **Yes** (test_sdis_dialog.py) | Master tabs (Devices, Office Members, Sync Status) |
| `table (table_peers)` | `QTableWidget` | **Yes** (`test_ui_lock_sync.py`) | `customContextMenuRequested -> _on_table_context_menu`: Discovered LAN devices table |
| `btn_sync_selected` | `QPushButton` | No | `clicked -> _on_sync_selected()`: Syncs selected workstation(s) |
| `btn_sync_all` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> _on_sync_all()`: Broadcasts sync to all online peers |
| `btn_refresh` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> _refresh_peers()`: Rescans LAN for peer workstations |
| `btn_add_ip` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> _on_add_pc_by_ip()`: Manually adds workstation by IP |
| `btn_remove_ip` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> _on_remove_pc_by_ip()`: Removes manually added workstation IP |
| `btn_rejoin` 🎯 | `QPushButton` | **Yes** (test_sync_rejoin.py, test_ui_lock_sync.py) | `clicked -> _on_rejoin_office()`: Opens `RejoinOfficeDialog` |
| `btn_export_kit` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> _on_export_recovery_kit()`: Exports office recovery kit (`.serakit`) |
| `members_table` 🎯 | `QTableWidget` | **Yes** (test_sync_office_ui.py, test_sync_status_panel_ui.py) | Authorized office workstations table (Office members tab) |
| `members_network_warning` 🎯 | `QLabel` | **Yes** (test_sync_status_panel_ui.py) | Advisory label for offline workstations |
| `btn_add_workstation` 🎯 | `QPushButton` | **Yes** (test_sync_office_ui.py, test_ui_lock_sync.py) | `clicked -> _on_add_workstation()`: Opens 6-digit workstation pairing code modal |
| `btn_remove_member` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> _on_remove_member()`: De-authorizes selected workstation |
| `btn_hand_over_admin` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> _on_hand_over_admin()`: Transfers primary admin role |
| `btn_become_admin` 🎯 | `QPushButton` | **Yes** (test_sync_office_ui.py, test_ui_lock_sync.py) | `clicked -> _on_become_admin()`: Claims primary admin role via master password |
| `sync_status_summary_label` 🎯 | `QLabel` | **Yes** (test_sync_status_panel_ui.py) | Pending and parked change summary label (Sync status tab) |
| `sync_status_warning_label` 🎯 | `QLabel` | **Yes** (test_sync_status_panel_ui.py) | Sync status warning label |
| `shadow_mode_label` 🎯 | `QLabel` | **Yes** (test_sync_golive_ui.py, test_sync_shadow_start_ui.py) | Shadow engine status indicator |
| `btn_start_shadow` 🎯 | `QPushButton` | **Yes** (test_sync_golive_ui.py, test_sync_shadow_start_ui.py (+1 more)) | `clicked -> _on_start_shadow_mode()`: Arms shadow testing mode |
| `btn_reset_shadow` 🎯 | `QPushButton` | **Yes** (test_sync_golive_ui.py, test_sync_shadow_start_ui.py (+1 more)) | `clicked -> _on_reset_shadow_mode()`: Resets shadow testing mode |
| `btn_go_live` 🎯 | `QPushButton` | **Yes** (test_sync_golive_ui.py, test_ui_lock_sync.py) | `clicked -> _on_go_live()`: Promotes shadow engine to live production sync |
| `btn_run_shadow_check` 🎯 | `QPushButton` | **Yes** (test_sync_status_panel_ui.py, test_ui_lock_sync.py) | `clicked -> _on_run_shadow_check()`: Runs manual shadow verification pass |
| `conflicts_table` 🎯 | `QTableWidget` | **Yes** (test_sync_status_panel_ui.py) | Detected data conflicts resolution table |
| `btn_conflict_keep` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> _on_conflict_keep()`: Resolves conflict keeping local value |
| `btn_conflict_use_discarded` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> _on_conflict_use_discarded()`: Resolves conflict adopting remote value |
| `log_list` 🎯 | `QListWidget` | **Yes** (test_ui_lock_sync.py) | Real-time live sync activity and event log stream |
| `btn_clear_log` | `QPushButton` | No | `clicked -> log_list.clear()`: Clears activity log list |
| `btn_close` 🎯 | `QPushButton` | **Yes** (test_ui_lock_sync.py) | `clicked -> accept()`: Closes dialog |
| `_refresh_timer` | `QTimer` | No | `timeout -> _refresh_peers()`: 3s periodic timer polling peers and status |

### 3.11. Service Edit (`dlg_service_edit.png`)

- **Class & File:** [`ServiceEditDialog`](file:///ui/dialogs/service_manager_dialog.py) in `ui/dialogs/service_manager_dialog.py`

- **How it is opened:** Opened from `ServiceManagerDialog` or `UnifiedSettingsDialog` Services page via 'Add Service' or 'Edit'.

- **Tests covering it:** `tests/test_service_automation_mode.py`, `tests/test_ui_lock_settings.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `name_input` | `QLineEdit` | No | `textChanged -> _auto_detect_portal_presets`: Service name input (auto-detects URL and selectors) |
| `url_input` | `QLineEdit` | No | `textChanged -> _auto_detect_portal_presets`: Portal login URL |
| `uid_combo` | `QComboBox` | No | Dropdown mapping to MCL column for User ID / PAN / TAN |
| `pwd_combo` | `QComboBox` | No | Dropdown mapping to MCL column for Password |
| `mode_combo` 🎯 | `QComboBox` | **Yes** (test_service_automation_mode.py) | `currentIndexChanged -> _on_mode_changed`: Primary automation mode (Fast Autofill, SMTI Assist, MECP Copy) |
| `mode_2_combo` 🎯 | `QComboBox` | **Yes** (test_service_automation_mode.py) | `currentIndexChanged -> _on_mode_changed`: Secondary automation mode (None, Fast Autofill, SMTI Assist, MECP Copy) |
| `ext_flow_combo` 🎯 | `QComboBox` | **Yes** (test_service_automation_mode.py) | Extension login flow (Two-Step vs Single Page) |
| `browser_combo` | `QComboBox` | No | Target browser override (Default, Chrome, Edge, Firefox) |
| `cancel_btn` | `QPushButton` | No | `clicked -> reject()`: Cancels dialog |
| `save_btn` | `QPushButton` | No | `clicked -> _on_accept()`: Validates and saves compliance service mapping |

### 3.12. Service Manager (`dlg_service_manager.png`)

- **Class & File:** [`ServiceManagerDialog`](file:///ui/dialogs/service_manager_dialog.py) in `ui/dialogs/service_manager_dialog.py`

- **How it is opened:** Standalone compliance service catalog opened from Admin window.

- **Tests covering it:** `tests/test_service_automation_mode.py`, `tools/ui_snapshots.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `list_widget` | `QListWidget` | No | List of services with primary and secondary mode badges |
| `add_btn` | `QPushButton` | No | `clicked -> _on_add()`: Opens `ServiceEditDialog` to add service |
| `edit_btn` | `QPushButton` | No | `clicked -> _on_edit()`: Opens `ServiceEditDialog` for selected service |
| `del_btn` | `QPushButton` | No | `clicked -> _on_delete()`: Confirms and deletes selected service |
| `close_btn` | `QPushButton` | No | `clicked -> accept()`: Closes dialog |

### 3.13. Legacy Settings Dialog (`dlg_settings_legacy.png`)

- **Class & File:** [`SettingsDialog`](file:///ui/dialogs/settings_dialog.py) in `ui/dialogs/settings_dialog.py`

- **How it is opened:** Legacy tabbed settings dialog. Superseded by `UnifiedSettingsDialog`. Permanently dropped from sidebar and active UI per owner decision (2026-10-10).

- **Tests covering it:** `tests/test_ui_lock_settings.py`, `tests/test_unified_settings_lazy.py`, `tests/test_scc_vault_tagger.py`, `tests/test_office_recovery.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `theme_combo` | `QComboBox` | No | Theme selector dropdown ('light') |
| `window_mode_combo` | `QComboBox` | No | Window display mode (fullscreen, square, rectangular) |
| `mask_mode_combo` | `QComboBox` | No | Masking mode (last_n, first_n, full_dots) |
| `reveal_count_spin` | `QSpinBox` | No | Mask character count |
| `clipboard_spin` | `QSpinBox` | No | Auto clipboard wipe seconds |
| `quick_copy_check` | `QCheckBox` | No | Master quick-copy toggle |
| `run_in_bg_check` | `QCheckBox` | No | Keep app running in background when closed |
| `autostart_check` | `QCheckBox` | No | Launch on Windows boot |
| `fst_enabled_check` | `QCheckBox` | No | Enable Sera DOM detector |
| `sca_enabled_check` | `QCheckBox` | No | Enable Sera Clipboard Assist |
| `sca_mode_combo` | `QComboBox` | No | SCA mode (autofill vs widget) |
| `sca_max_uses_spin` | `QSpinBox` | No | SCA max uses |
| `btn_ext_check` | `QCheckBox` | No | Enable 'Ext' button in Client Detail |
| `btn_assist_check` | `QCheckBox` | No | Enable 'Assist' button in Client Detail |
| `btn_copy_check` | `QCheckBox` | No | Enable 'Copy' button in Client Detail |
| `show_hide_enabled_check` | `QCheckBox` | No | Enable Show/Hide password eye buttons |
| `visibility_cbs` | `dict[int, QCheckBox]` | No | Checkboxes for main search column visibility |
| `quick_copy_cbs` | `dict[int, QCheckBox]` | No | Checkboxes for quick-copy field permissions |
| `admin_visibility_cbs` | `dict[int, QCheckBox]` | No | Checkboxes for admin search column visibility |
| `btn_save` | `QPushButton` | No | `clicked -> _save_settings()`: Saves settings to database |
| `btn_cancel` | `QPushButton` | No | `clicked -> reject()`: Cancels dialog |

### 3.14. Startup Loading Modal (`dlg_startup_loading.png`)

- **Class & File:** [`StartupLoadingDialog`](file:///ui/dialogs/loading_dialog.py) in `ui/dialogs/loading_dialog.py`

- **How it is opened:** Displayed during launch PBKDF2 derivation and database initialization in `main.py` (`loading_dlg = StartupLoadingDialog(); loading_dlg.show()`).

- **Tests covering it:** `tests/test_sdis_dialog.py`, `tools/ui_snapshots.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `lbl_status` | `QLabel` | No | Status message label ('Initializing security vault...') |
| `progress` | `QProgressBar` | No | Indeterminate animated progress bar |

---

## 4. Display Scale Dialog & Sidebar Display Entry (No PNG)

### 4.1. Display Scale Dialog (`DisplayScaleDialog`)

- **Class & File:** [`DisplayScaleDialog`](file:///ui/dialogs/display_scale_dialog.py) & `DisplayScaleControl` in `ui/dialogs/display_scale_dialog.py`

- **How it is opened:** Sidebar 'Display' entry (accessible to all staff, non-admin and admin); Settings -> General page (Screen scale row); or via Ctrl+= / Ctrl+- / Ctrl+0 shortcuts.

- **Tests covering it:** `tests/test_ui_scale.py`, `tests/test_ui_lock_display.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `DisplayScaleDialog.control` | `DisplayScaleControl` | **Yes** (`test_ui_scale.py`) | Embedded instance of `DisplayScaleControl` |
| `DisplayScaleDialog.close` | `QPushButton` | No | `clicked -> accept()`: Closes dialog |
| `combo` 🎯 | `QComboBox` | **Yes** (test_ui_scale.py) | Scale selector dropdown: Automatic (recommended), 80 %, 90 %, 100 %, 110 %, 125 % |
| `btn_apply` | `QPushButton` | No | `clicked -> _apply()`: Saves chosen scale to local `scale.ini` (QSettings) |
| `btn_restart` | `QPushButton` | No | `clicked -> _restart_now()`: Saves choice, asks confirmation, and restarts application |
| `lbl_status` | `QLabel` | No | Displays active scale and save status ('Running at X %.') |

### 4.2. Sidebar Display Entry (`Sidebar.btn_display_scale`)

- **Class & File:** [`Sidebar`](file:///ui/shell/sidebar.py) in `ui/shell/sidebar.py`, wired in `main.py` (`self._open_display_scale_dialog`)

- **How it is opened:** Navigation item located under Tools group in Sidebar; always visible for all users in both normal and admin modes.

- **Tests covering it:** `tests/test_ui_lock_display.py`, `tests/test_responsive.py`.

| Attribute Name | Widget Type | Tested in tests/? | Triggers / Action / Behavior |
| :--- | :--- | :--- | :--- |
| `btn_display_scale` 🎯 | `QPushButton` | **Yes** (test_ui_lock_display.py) | `clicked -> action_display_scale.emit()`: Emits display scale open request; labelled 'Display', icon `mdi.monitor` |
| `action_display_scale` 🎯 | `Signal` | **Yes** (test_ui_lock_display.py) | Signal connected in `main.py` to `self._open_display_scale_dialog()`, executing `DisplayScaleDialog.exec()` |

---

## 5. Out of the Overhaul (Leave Look As-Is)

The following screens and dialogs are **explicitly out of the overhaul**.
Per Rule 7 and project specifications, their current visual styling, layouts, and implementations remain intact. They are listed here for completeness with **no control table**:

1. **SGT Lab Dialog** (`dlg_sgt_lab.png`)
   - **Class:** [`SgtLabDialog`](file:///ui/dialogs/sgt_lab_dialog.py) in `ui/dialogs/sgt_lab_dialog.py`
   - **How it is opened:** Reached from Settings -> Tracker -> 'Open SGT Lab' button (`SgtLabDialog(self).exec()`).
   - **Covering Tests:** `tests/test_sgt_i_lab.py`
   - **Status:** **Out of overhaul**. Retains current look and dark theme; Settings links to it unchanged.

2. **SDIS Main Distill Dialog** (*no PNG in current/*)
   - **Class:** [`SDISDialog`](file:///ui/dialogs/sdis_dialog.py) in `ui/dialogs/sdis_dialog.py`
   - **How it is opened:** Reached from Tracker Dump -> Tools ▾ -> 'Distill...' menu action (Admin PC only).
   - **Covering Tests:** `tests/test_sdis_dialog.py`
   - **Status:** **Out of overhaul**. Retains current non-modal desktop layout and styling.

3. **SDIS Containers Dialog** (*no PNG in current/*)
   - **Class:** [`SDISContainersDialog`](file:///ui/dialogs/sdis_containers_dialog.py) (including `ContainerEditor` and `LevelsDialog`) in `ui/dialogs/sdis_containers_dialog.py`
   - **How it is opened:** Opened from the SDIS Distill dialog side panel.
   - **Covering Tests:** `tests/test_sdis_dialog.py`
   - **Status:** **Out of overhaul**. Retains current layout and styling.


---

*Inventory compiled and verified against test suite on 2026-10-10.*
