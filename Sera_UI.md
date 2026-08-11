# Sera UI Design Notes

This document records the current Project Sera desktop UI rules and navigation model. It is intended to keep future screens visually consistent with the client-detail experience.

## Visual language

- Primary sidebar: `#FF4D49` (red)
- Active navigation/action: `#4DFFBC` (mint)
- Application surface: `#F3ECDD` (warm cream)
- Card surface: `#EAE1CB`
- Card border: `#D8CDB4`
- Primary text: `#241F1B`
- Section accent: `#FF4D49`
- Dark-theme equivalents are defined in `ui/utils/theme.py`.

Use rounded corners, light borders, compact vertical spacing, and clear section hierarchy. Inputs and read-only values use white field tiles inside cream card containers.

## Navigation model

The sidebar is the persistent navigation surface. Search and All Clients are available in normal mode. Admin Mode reveals the management actions under Clients, Settings, Services, and Data Management.

The active navigation item uses the mint fill and dark text. Programmatic navigation also updates the active item so the sidebar always reflects the visible workspace.

The profile footer displays the saved staff label next to the profile icon. The sidebar also includes a dedicated Manage Clients entry when Admin Mode is enabled.

## Client workflows

### Normal mode

`+ Add Client` opens the focused `NewClientDialog` form directly. It does not expose the full management workspace or admin mutation controls.

### Admin mode

The search toolbar exposes Material-icon Archive, Edit, Delete, and Attach/Detach actions for the selected client. Archive is admin-gated, asks for confirmation, hides the client from active Search results, and records an audit entry; it does not delete the client. Manage Clients opens the full CRUD workspace. Audit Log, MCL, Settings, Service Management, Filing Types, Filing Periods, CSV Import, Export, Backup, and Restore remain admin-gated.

## Client detail layout

`ui/windows/client_detail_window.py` uses the shared detail-card pattern:

1. Compact header with Material arrow Back control and client profile title.
2. Identity & Contacts card with a two-column field grid.
3. Security Credentials card with masked values and visible Show/Hide and Copy controls.
4. Service Management card with attached-service indicators.
5. Notes area with a mint Save Notes action.
6. Autofill and Manual Copy action rows, followed by optional DRS filing status.

The normal profile view uses compact margins and field spacing so the common client record fits on one screen. DRS rows and long service/action lists remain available through the existing content area when a record contains more content than the available height.

## Shared component rules

The shared rules live in `ui/utils/theme.py` and are applied to:

- Manage Clients
- Audit Log
- Manage MCL
- Settings and its tabs
- Service Management and service configuration
- Filing Types and Filing Periods
- CSV Import and Data Management tools

Use these selectors for new screens where applicable:

- `ClientDetailCard` for grouped cards
- `ClientDetailField` for read-only/value tiles
- `DetailFieldLabel` and `DetailFieldValue` for field typography
- `DetailActionButton` for compact secondary actions
- `DetailNotes` for notes editors
- `ToolDialog` for admin tool dialogs
- `ManageClientsPage` for the CRUD page
- `PageTitle`, `SectionTitle`, and `PageSeparator` for page-level hierarchy

## Navigation controls

Slide-panel tools share a Back button in `ui/shell/slide_panel.py`. Manage Clients and Client Detail provide their own Back controls. Clicking outside the Client Detail panel closes it; other slide-panel forms remain open to prevent accidental data loss. Dashboard retains its existing Back to Search action. Material Design icons are supplied through QtAwesome so icons remain visible against the cream surface.

## Runtime notes

- `main.py` sets Segoe UI before creating widgets for consistent Windows typography.
- Harmless Qt legacy-font diagnostics are suppressed with `QT_LOGGING_RULES=qt.qpa.fonts=false`.
- Shell and Manage Clients layouts are shrinkable so Windows display scaling does not force an oversized minimum window.
