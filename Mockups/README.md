# Sera UI/UX Mockups & Fleet Redesign

**Status: built** (Phases T, K, R, S, and F completed October 2026).

All core screens, shell navigation, and modal dialogs have been redesigned and built into the production application using the shared design tokens ([`ui/utils/tokens.py`](../ui/utils/tokens.py)) and component kit ([`ui/components/kit.py`](../ui/components/kit.py)). Official design system documentation is maintained in [`docs/Sera_UI.md`](../docs/Sera_UI.md). Legacy `settings_dialog.py` was permanently retired and removed in F-1.

Colors follow the application's unified tokens (`#202020` base, `#141414` panels, `#2E9B5F` emerald primary, `#4CF9B7` mint highlight, `#FF4D4D` danger, pure white data grid for client records, dark grid for Tracker Dump). The SGT lab and SDIS panels remain out of the overhaul and retain their existing look.

Live, clickable mockup artifact: https://claude.ai/artifact/1G4UtGzSgDXkzP7hp6s6R5

## Built Screens & Workspaces

- [All Clients / Search](01-all-clients-search.md) (`ui/windows/search_window.py`) — Built in S-6 & S-7
- [Client detail panel](02-client-detail-panel.md) (`ui/windows/client_detail_window.py`) — Built in S-8
- [Audit log (SSAL)](03-audit-log.md) (`ui/dialogs/audit_log_dialog.py`) — Built in S-11
- [Sera Sync](04-sera-sync.md) (`ui/dialogs/sera_sync_dialog.py`) — Built in S-12
- [Settings — General](05-settings-general.md) (`ui/dialogs/unified_settings_dialog.py`) — Built in S-1
- [Settings — Columns](06-settings-columns.md) (`ui/dialogs/unified_settings_dialog.py`) — Built in S-4
- [Manage Clients](07-manage-clients.md) (`ui/windows/admin_window.py`) — Built in S-9
- [Tracker Dump](08-tracker-dump.md) (`ui/windows/tracker_dump_window.py`) — Built in S-10
- [Display scale](09-display-scale.md) (`ui/dialogs/display_scale_dialog.py`) — Built in R-3
- [Modal dialogs](IMPLEMENTATION.md) (`ui/dialogs/*.py`) — Built in S-13 & S-14

## Design Decisions

- **Theme setting:** Shows "Dark" as the only option; no light theme planned.
- **EMAIL and TAN column types:** Kept as type `password` [Secret] for privacy protection.
- **One Primary CTA:** Exactly one emerald primary button per screen or dialog.
- **Red Means Danger:** Reserved strictly for destructive deletions, purges, and unpairing confirmations.
- **Every Prompt on the Kit:** All prompts use `kit.MessageBox` (standard `QMessageBox` dropped).

## Fleet Redesign Mockup

The complete fleet redesign is available as a self-contained, offline HTML page:
- [fleet/index.html](fleet/index.html) — Clickable interactive mockup of all screens in WIDE, COMPACT, and scaled modes.
- [fleet/README.md](fleet/README.md) — Screen specifications, tokens, and "where every control went" tables.
- [fleet/BUILD-PLAN.md](fleet/BUILD-PLAN.md) — The kit-first build plan and phase execution tracker (status: built).

## Build Plan & Implementation Tracking

See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the work package specifications, rules, tests, and phase hand-off notes. The tracker, dispatcher, runner prompts, and batch scripts are in [overhaul/](overhaul/).
