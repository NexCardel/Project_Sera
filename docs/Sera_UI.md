# Sera Desktop UI Design System & Component Kit Specifications

> **Official Design System Documentation for Project Sera**  
> Built and validated for PySide6 on Windows desktop.  
> Source of truth: [`ui/utils/tokens.py`](../ui/utils/tokens.py) (design tokens) and [`ui/components/kit.py`](../ui/components/kit.py) (component kit).

---

## 1. Executive Summary & Core Architectural Rules

Project Sera uses a refined, high-density desktop design system specifically engineered for professional accounting, tax, and compliance operations on Windows. The interface provides exceptional readability across laptop and desktop displays (base target: **1366 × 768**, responsive down to **1024 × 700**, display scaling from **0.80 to 1.25**).

The design architecture pairs an uncluttered, deep graphite/dark-mode shell with a high-contrast pure white data grid for primary client records and an inverted dark data grid for live system activity logs.

### The Non-Negotiable Rules of the UI Overhaul

1. **Screens Import, They Do Not Style (Rule 11)**:
   Screen modules (`ui/windows/`, `ui/dialogs/`, `ui/shell/`) **never** call `setStyleSheet()` with literal style strings or hardcoded CSS blocks. All visual styling is encapsulated in `ui/components/kit.py` and powered exclusively by `ui/utils/tokens.py`.
2. **Tokens, Not Hex (Rule 3)**:
   No hardcoded hex (`#xxxxxx`) or RGB strings exist in application screens or dialogs. All colors, radii, paddings, and font sizes originate from `TOKENS` or `KIT` dictionaries in `ui/utils/tokens.py`.
3. **One Emerald Primary CTA Per Screen / Dialog**:
   Every window or modal dialog features exactly **one** primary call-to-action button rendered in accent emerald (`#2E9B5F`). All secondary actions use `soft`, `secondary`, or `ghost` variants.
4. **Red Means Danger (Destructive Actions Only)**:
   The danger color (`#FF4D4D`) is strictly reserved for irreversible operations: permanent delete, database purge, office unpairing, and destructive confirmations. Deactivated or "off" controls are rendered as dimmed neutral grey (`40%` opacity) with an explanatory tooltip.
5. **Disabled With Reason (`set_disabled_reason`)**:
   Controls are never silently disabled with bare `setEnabled(False)` without user explanation. Components implement `set_disabled_reason(reason: str | None)`, which dims the control to `40%` opacity and displays the specific rationale in its tooltip. Passing `None` restores normal state and original tooltip.
6. **Required Tooltips on Icon-Only & Truncated Controls**:
   Any button or action lacking visible text requires a descriptive `tooltip`. Any pill, tag, or label with elided text automatically displays the complete, unabridged value via its tooltip.
7. **Keyboard Operability & Predictable Focus**:
   `Tab` / `Shift+Tab` strictly follow visible layout order, skipping disabled controls. `Space` activates buttons, switches, checkboxes, tiles, and chips. `Enter` triggers primary dialog confirmations, opens chip menus, or activates chips and switches. `Esc` unconditionally dismisses `DialogFrame` and `Scrim`. The keyboard focus cursor is consistently drawn with a crisp mint (`#4CF9B7`) ring/edge.
8. **No Per-Row Cell Widgets (Lightweight Delegation)**:
   Data grids never embed `QWidget` instances inside table cells. All row action buttons, status pills, tags, service dots, checkboxes, and two-line text cells are drawn directly by `GridDelegate` via item data roles (`ROLE_ACTIONS`, `ROLE_PILL`, `ROLE_SERVICES`, etc.).
9. **Zero Network / AI Latency & Strict Data Privacy**:
   The user interface operates strictly offline with zero LLM/cloud calls. All test fixtures and gallery outputs utilize fictional names, dummy PANs, and fake workstation identifiers.

---

## 2. Design Tokens Reference (`ui/utils/tokens.py`)

All tokens are defined in `ui/utils/tokens.py` based on the `:root` specifications of the canonical fleet redesign.

### 2.1 Base Theme Surfaces & Colors (`TOKENS`)

| Token Key | Value | Semantic Usage |
| :--- | :--- | :--- |
| `bg` | `#202020` | Base application background for top-level windows |
| `side` | `#171717` | Navigation sidebar background |
| `panel` | `#141414` | Primary container surface for modal dialogs and slide-out drawers |
| `card` | `#191919` | Structured card surfaces |
| `raise` | `#1F1F1F` | Elevated rows and interactive containers |
| `hover` | `#262626` | Hover state background on dark surfaces |
| `line` | `#2A2A2A` | Primary hairlines and container dividers |
| `line2` | `#363636` | Secondary control borders (inputs, steppers, select) |
| `line3` | `#444444` | Highlighted control borders and hover outlines |
| `text` | `#F8FAFC` | High-contrast primary body text |
| `text2` | `#C9D1D9` | Secondary content text and default icons |
| `muted` | `#8E8D88` | Muted labels, secondary descriptions, and field subtitles |
| `faint` | `#6E6E6E` | De-emphasized metadata, timestamps, and placeholder text |
| `page` | `#0B0D0C` | Deep backdrop canvas |
| `ink` | `#E8ECE9` | High-contrast monochromatic text |
| `ink2` | `#A9B3AD` | Medium-contrast monochromatic text |
| `ink3` | `#6F7A74` | Monochromatic caption text |
| `rule` | `#242A27` | Subtle structural dividers |

### 2.2 Semantic Accents & Status Tones

| Accent Category | Token Keys | Values | Usage |
| :--- | :--- | :--- | :--- |
| **Emerald (Primary)** | `em`, `em2`<br>`emdim`, `emdeep`<br>`emline`, `mint` | `#2E9B5F`, `#34B76D`<br>`#1A382B`, `#0E1F16`<br>`#1E4D34`, `#4CF9B7` | The singular primary button accent, hover state, active navigation washes, card edge accents, focus rings, and mint highlight edges. |
| **Danger (Destructive)** | `danger`, `dangertx`<br>`dangerdim`, `dangerline` | `#FF4D4D`, `#FF8D8D`<br>`#3A1A1A`, `#7A2B2B` | Permanent deletion, database purge, card danger border, destructive confirmation modal prompts. |
| **Warning / Attention** | `warn`, `warndim`<br>`warnline` | `#E3B341`, `#33290F`<br>`#5B4814` | Amber attention banners, unsaved changes bar, caution dialog headers. |
| **Information** | `info`, `infodim`<br>`infoline` | `#5AA7FF`, `#16263B`<br>`#24466E` | Informational notices, network synchronization indicators, blue status pills. |
| **Purple** | `purple`, `purpledim` | `#C4A8FF`, `#2A1F3D` | Specialized capture tags and auxiliary service states. |
| **Orange** | `orange`, `orangedim` | `#FFB380`, `#3A2412` | Alternate status pill indicators. |

### 2.3 White Data Grid Tokens (Primary Search & Client Lists)

| Token Key | Value | Description |
| :--- | :--- | :--- |
| `g_bg` | `#FFFFFF` | Crisp pure white grid table background |
| `g_alt` | `#F7FAF8` | Alternating zebra row tint |
| `g_line` | `#E7ECE9` | Subtle grid cell separator lines |
| `g_head` | `#101713` | Deep graphite column header surface |
| `g_headtx`| `#CFE9DC` | High-contrast pale mint header text |
| `g_text` | `#1B2420` | Dark charcoal high-contrast table body text |
| `g_mute` | `#6B7A72` | Muted secondary text inside white cells (e.g. counts) |
| `g_hover`| `#EDF6F1` | Soft mint row hover wash |
| `g_sel` | `#D9F2E4` | Mint selection fill, bounded by emerald `w_sel_edge` (3px) |

### 2.4 Dark Data Grid Tokens (Tracker Dump & Activity Tables)

| Token Key | Value | Description |
| :--- | :--- | :--- |
| `dgrid_head` | `#0F1512` | Deep header background for dark grid |
| `dgrid_line` | `#232323` | Hairline cell dividers |
| `dgrid_alt` | `#171717` | Alternating zebra row background |
| `dgrid_hover`| `#1F2A24` | Row hover wash |
| `dgrid_sel` | `#14301F` | Selected row background |
| `list_hover` | `#1E1E1E` | List mode table hover row |
| `list_sel` | `#162A20` | List mode table selected row |

### 2.5 Standard Radii, Spacing & Typography

| Dimension Group | Tokens | Values / Descriptions |
| :--- | :--- | :--- |
| **Corner Radii** | `r_xs`: 5px, `r_s`: 6px, `r_m`: 8px, `r_l`: 12px<br>`r_chip`: 14px, `r_badge`: 9px, `r_selbar`: 10px, `r_unsaved`: 10px | Small controls (6px), standard inputs & buttons (8px), cards, dialogs & grids (12px). |
| **Spacing Scale** | `s_1`: 4px, `s_2`: 8px, `s_3`: 12px, `s_4`: 16px, `s_5`: 20px, `s_6`: 24px | Uniform layout margins and inter-control padding. |
| **Font Sizes** | `f_xs`: 11px, `f_s`: 12px, `f_m`: 13px, `f_l`: 14px, `f_xl`: 17px, `f_xxl`: 21px | Section labels (11px), hints & chips (12px), body & inputs (13px), titles (14-21px). |
| **Control Heights** | `h_ctl`: 32px, `h_inp`: 34px, `h_chip`: 28px, `h_row`: 40px<br>`h_row_tall`: 52px, `h_grid_head`: 36px, `h_grid_foot`: 30px | Standard interactive controls (32-34px), table rows (40px standard, 52px two-line). |
| **Layout Widths** | `w_ctl`: 230px, `w_ctl_wide`: 360px, `w_side`: 212px, `w_panel`: 540px | Fixed control column (230px), sidebar (212px), slide-over panel (540px). |
| **Font Families** | `ff`: `'Segoe UI', system-ui, sans-serif`<br>`mono`: `Consolas, 'Cascadia Mono', monospace` | Primary UI typography and monospace technical data (PAN, GSTIN, logs). |

---

## 3. Component Kit Architecture (`ui/components/kit.py`)

All visual elements are composed from standardized kit components.

### 3.1 Primitives & Leaves (Phase K-1)

- **`LabelledAction`**:
  Universal icon + text push button.
  - Variants: `primary` (emerald, 1 per screen), `soft` (tinted emerald surface), `secondary` (standard dark border), `ghost` (borderless), `danger` (destructive red).
  - Sizes: Standard (32px), `sm=True` (28px).
  - Methods: `set_disabled_reason(reason)`, `set_icon_only(True)`, `set_swatch(color)`, `set_variant(var)`.
- **`IconButton`**:
  Compact square icon button. Variants: `plain`, `bordered`, `primary`. Parameter `tooltip` is **mandatory**.
- **`Segmented`**:
  Exclusive 1-of-N tab-style button group. Keyboard navigation via `Left`/`Right` arrows. Emits `currentChanged(int)`.
- **`Key`**:
  Keyboard shortcut badge (e.g., `Ctrl+B`, `Alt+1`). Supports `light=True` variant for white grid footers.
- **`Pill`**:
  Compact metadata badge. Tones: `neutral`, `emerald`, `amber`, `red`, `blue`, `purple`, `orange`. Optional `dot=True` embeds a matching `StatusDot`. Automatically elides long text with full-text tooltip.
- **`Tag`**:
  Monospace technical metadata tag. Tones: `neutral`, `emerald`, `amber`, `red`, `blue`.
- **`StatusDot`**:
  8px status indicator with a soft diffused glow. Tones: `ok`, `warn`, `bad`, `info`, `off`.

### 3.2 Form Inputs & Controls (Phase K-2)

- **`FieldLabel`**:
  Uppercase field header (`11px`, letter-spaced). Parameter `required=True` appends a crimson `*`. Automatically links buddy widget via `for_widget`.
- **`Input`**:
  34px high text entry (`QLineEdit`). Supports leading icon (`icon="mdi.*"`) and trailing actions slot (`add_trailing()`) for eye/copy buttons.
- **`Select`**:
  Styled combobox with drawn vector chevron. Wheel scrolling is restricted to active keyboard focus to prevent accidental value alterations.
- **`TextArea`**:
  Multiline text editor (`QPlainTextEdit`). `Tab` key cleanly exits the field instead of trapping focus.
- **`Stepper`**:
  Numeric spinner with integrated step controls (`-` / `+`). Supports `Up`/`Down`/`Left`/`Right` and `Home`/`End` keys. Supports suffix units (e.g. `"s"`, `" min"`).
- **`Checkbox`**:
  Custom painted checkbox strictly reserved for lists and table selections. (Single on/off settings always use `Switch`).
- **`Switch`**:
  38 × 22px toggle switch with keyboard operability (`Space` / `Enter`). Instantaneous state change without timer-based animations.

### 3.3 Structural Containers (Phase K-3)

- **`Card`**:
  Standard dark container (`#191919`). Features uppercase header title, optional right-hand action slot, and edge highlight variants: `edge="danger"` (destructive) or `edge="em"` (mint).
  - *Rule*: Cards never contain nested cards. Group internal content with `SectionLabel` or `SettingRow`.
  - Methods: `add_widget(w)`, `add_row(w)` (full-width setting row with hairlines), `add_action(w)`.
- **`SettingRow`**:
  Standard settings layout container: left column has title and muted description; right column houses control(s) aligned in a fixed 230px column (`w_ctl`). Supports `width="wide"` (360px) and `width="auto"`.
- **`SectionLabel`**:
  Standard unboxed uppercase section divider label (`11px`, muted `#8E8D88`).
- **`Banner`**:
  Full-width inline notice box. Tones: `w` (warning amber), `r` (error red), `g` (success emerald), `i` (info blue). Supports trailing dismiss/action button via `add_action()`.
- **`Toast`**:
  Centred alert banner anchored to the bottom of the content viewport. Promotes `SeraAlert` with levels `success`, `info`, `warning`, `error`.
- **`EmptyState`**:
  Standardized placeholder for empty datasets. Consists of a 44px ring icon, title, explanatory sentence, and optional action buttons (`add_action()`).

### 3.4 Toolbars & Control Bars (Phase K-4)

- **`Chip` & `ChipBar`**:
  Filter chip collection (28px height). Supports single-select exclusive groups (`single_select=True`), count badges (`count=12`), active state replacement strings (`value_when_on`), dropdown menus (`menu=QMenu`), and automatic `More ▾` overflow management.
- **`StatusStrip`**:
  44px high status indicator strip across screen or tab headers. Hosts pills, bold metric facts, vertical dividers, and real-time connectivity badges.
- **`SelectionBar`**:
  Persistent 44px contextual action bar anchored above data grids.
  - Features: Target identifier (`who_label`), client action group (Open, Edit, Services, Archive), vertical separators, cell-formatting group (Fill, Text Color, Clear, Undo, Redo), and far-right destructive action (Delete).
  - Enforces `Qt.NoFocus` on buttons to prevent stealing keyboard navigation from the grid.
  - Automatically switches to compact icon-only mode in narrow viewports.
- **`UnsavedBar`**:
  44px high amber notice bar indicating dirty configuration state ("N unsaved changes"). Houses **Discard** (secondary) and **Save changes** (emerald primary). Supports `reserved_space=True` mode to avoid layout jumping.

### 3.5 Data Grid System (Phase K-5)

- **`DataGrid`**:
  Advanced `QTableWidget` subclass paired with `GridDelegate`. Drop-in replacement for legacy tables preserving existing item accessors and format properties.
  - **Tones**: `white` (default for search, clients, audit) and `dark` (Tracker Dump).
  - **List Mode** (`list_mode=True`): 52px tall two-line row layout without column headers (used in Manage Clients navigation list).
  - **Item Roles for Lightweight Delegation**:
    - `ROLE_SERVICES`: List of `(name, color)` tuples rendering service dots; overflows to `+N` with tooltip list.
    - `ROLE_PILL`: `(text, tone, dot_bool)` rendering an integrated status pill.
    - `ROLE_TAG`: `(text, tone)` rendering a monospace type tag.
    - `ROLE_ACTIONS`: List of `CellAction` buttons drawn directly in the cell; emits `actionTriggered(row, col, key)` on click.
    - `ROLE_SUB`: Second line string for two-line cell formatting (primary bold text, secondary muted mono text).
    - `Qt.CheckStateRole`: Draws standard kit checkbox.
- **`GridFooter`**:
  30px high footer bar displaying total item counts, contextual keyboard navigation shortcuts (`Key` hints), and pagination controls.
- **`GridFrame`**:
  Outer container providing rounded border radii (12px), masked corners, and unified tone styling across grid and footer.
- **Column Sizing & Responsive Fit (`fit_columns`)**:
  Columns configured via `set_column(col, kind, width, minimum, stretch, max_share, priority, compact_hidden)`. During window resizing or width mode transitions, `fit_columns()` automatically collapses lower-priority columns to eliminate unwanted horizontal scrollbars.

### 3.6 Modal Dialogs, Frames & Alerts (Phase K-6, S-14, S-R)

- **`DialogFrame`**:
  Standardized window/dialog container.
  - Header: 34px `IconTile`, title, purpose sentence, and optional trailing close button (`add_close_button`).
  - Body: Scrollable or fixed widget container (`add_widget`).
  - Footer: Automatically displays upon adding the first action. Strictly enforces **one primary action**.
  - Closes via `Esc` emitting `closeRequested`.
  - Supports `full=True` mode for full-window overlays (e.g. Settings, Audit Log, Sera Sync).
- **`Scrim`**:
  Semi-transparent backdrop layer (`#0F1412` at `140` alpha) centering a modal `DialogFrame`. Clicking outside or pressing `Esc` dismisses the modal.
- **`PageHeader`**:
  Top page bar providing leading action slots (sidebar toggle, back button), title, live count badge, muted subtitle lead, and trailing actions toolbar.
- **`Tile`**:
  Summary metric tile displaying large number above a muted label. Supports click/check states and group selection via `QButtonGroup`.
- **`LogView`**:
  High-performance monospace log viewer with color-coded tags (`GUARD` amber, `SYNC` emerald, `ERROR` red, `PEER` blue, `NET` purple). Automatically anchors to bottom unless user scrolls up.
- **`MessageBox` (Replaces `QMessageBox` Everywhere)**:
  Modal dialog replacements matching Qt static signatures:
  - `MessageBox.information(parent, title, text, ...)`
  - `MessageBox.warning(parent, title, text, ...)`
  - `MessageBox.critical(parent, title, text, ...)`
  - `MessageBox.question(parent, title, text, ..., danger=False)`
  - `MessageBox.choose(parent, title, text, choices, ...)`
  - Parameter `danger=True` styles the primary confirmation button in destructive red.

---

## 4. Responsive Layout & Display Scale Architecture

### 4.1 Width Modes: WIDE vs. COMPACT (`ui/utils/responsive.py`)

The application adapts dynamically to screen width using a hysteresis threshold to prevent layout thrashing:
- **COMPACT Mode**: Triggered when content width drops below **1080 px**.
- **WIDE Mode**: Triggered when content width expands above **1120 px**.

#### `WidthAware` Mixin Architecture
Screens inherit `WidthAware` to automatically receive width change notifications:
- Automatically propagates `apply_width_mode(mode)` to child kit bars (`ChipBar`, `SelectionBar`), grids (`DataGrid`, `GridFrame`), headers, and panels.
- Provides `setup_responsive_columns()` to automatically hide low-priority columns in COMPACT mode.
- Provides `setup_responsive_chips()` to wrap filter chips to a dedicated second row in COMPACT mode.
- Shrinks `SelectionBar` to icon-only buttons (preserving text only on **Open**).

### 4.2 Display Scale Adaptation (`ui/utils/ui_scale.py`)

Sera supports granular application-level display scaling (**0.80× to 1.25×**), allowing users on high-DPI screens or small laptops (1366 × 768) to customize UI density independently of Windows OS scaling:
- Saved per workstation in `QSettings("AmanAssociates", "Sera")` under key `ui_scale`.
- Accessed universally from the sidebar **Display** entry or **Settings → General**.
- Verified across all converted screens offscreen at 0.80, 1.00, and 1.25 scales without layout clipping.

---

## 5. Application Screens Architecture

| Screen / Workspace | Primary File | Key Kit Components Used | Architectural Highlights |
| :--- | :--- | :--- | :--- |
| **All Clients / Search** | `ui/windows/search_window.py` | `PageHeader`, `ChipBar`, `DataGrid` (white), `GridFrame`, `GridFooter`, `SelectionBar` | 9-preset filter chip bar driving hidden combo; dynamic column visibility menu; contextual selection bar with cell formatting actions. |
| **Client Detail Panel** | `ui/windows/client_detail_window.py` | `SlidePanel` (540px), `Card`, `LabelledAction`, `Pill`, `Key`, `EmptyState` | Slide-over drawer; autofill service actions with Alt+1..9 shortcuts; real-time notes debounced auto-save. |
| **Manage Clients** | `ui/windows/admin_window.py` | `ChipBar`, `DataGrid` (list mode), `Card`, `SelectionBar`, `SettingRow` | Split view with client navigation list on left and dynamic MCL editor cards on right; bulk operations bar. |
| **Tracker Dump** | `ui/windows/tracker_dump_window.py` | `PageHeader`, `Tile` summary cards, `Segmented`, `ChipBar`, `DataGrid` (dark) | High-density audit grid with dark tone; action buttons drawn by delegate; adaptive column priorities. |
| **Unified Settings Hub** | `ui/dialogs/unified_settings_dialog.py` | `DialogFrame` (full), `Card`, `SettingRow`, `Switch`, `Select`, `Stepper`, `UnsavedBar`, `DarkListTable` | Full-window dialog; 12 unified settings pages; dirty-tracking engine with live unsaved counter and discard restoration. |
| **Audit Log (SSAL)** | `ui/dialogs/audit_log_dialog.py` | `DialogFrame` (full), `DarkListTable`, `ChipBar`, `DataGrid` (white), `Card` | Full-window dialog; workstation peer aggregator filter; entry inspection panel. |
| **Sera Sync Hub** | `ui/dialogs/sera_sync_dialog.py` | `DialogFrame` (full), `StatusStrip`, `Segmented`, `DarkListTable`, `Card`, `LogView`, `Banner` | Peer discovery table; office member management; real-time color-coded replication activity log. |
| **Modal Dialogs** | `ui/dialogs/*.py` | `DialogFrame`, `FieldLabel`, `Input`, `Select`, `Banner`, `MessageBox` | Standardized dialog wrappers with single primary CTA, icon badges, and keyboard dismissibility. |

---

## 6. Verification, Testing & Tooling

1. **Rule Enforcement Suite (`tests/test_ui_overhaul_rules.py`)**:
   - Automated AST and regex checks asserting zero literal `setStyleSheet()` calls across all screens.
   - Asserts zero hardcoded hex colors outside authorized data palettes.
   - Asserts zero raw `QMessageBox` calls, ensuring 100% adherence to `kit.MessageBox`.
2. **Component Gallery (`tools/ui_gallery.py`)**:
   - Generates offscreen pixel-perfect snapshots of all kit components across all interaction states (normal, hover, focus, disabled, checked, compact) at 100% and 125% scales to `Mockups/built/kit/`.
3. **Screen Goldens (`tools/ui_snapshots.py`)**:
   - Renders complete application screens and dialogs offscreen against an ephemeral database with fictional client records.
