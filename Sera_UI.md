# Sera UI Design System & Styling Specifications

This document records the official desktop UI design tokens, component architecture, and styling rules for Project Sera. All user interface components adhere to these specifications to guarantee visual consistency and clutter-free readability.

---

## 🎨 Design Tokens & Theme Palette

The application uses a refined 2-tier dark theme with reserved accent highlights:

| System Layer / Element | Hex Token | Description |
| :--- | :--- | :--- |
| **App Main Background** | `#202020` | Base dark tone for main windows and top-level pages |
| **Panel / Group Surface** | `#141414` | Primary panel container surface for sidebars, sections, and dialogs |
| **Input Surface** | `#171717` | Clean dark surface for inputs, text boxes, and combo boxes with subtle `#333333` border |
| **Cell Grid Table Background** | `#FFFFFF` | Pure white background for search results and data tables |
| **Cell Grid Table Text** | `#241F1B` | High-contrast dark charcoal text for table item contents |
| **Table Column Headers** | `#141414` | Dark headers with bold white (`#FFFFFF`) text & `#262626` borders |
| **Scrollbars (Vertical/Horizontal)**| `#3A3A3A` | Neutral graphite handles (`#4F4F4F` hover) on `#141414` tracks |
| **Primary CTA Accent** | `#2E9B5F` | Emerald green for primary action buttons (`+ Add Client`, Save, Confirm) |
| **Destructive Accent** | `#A82424` | Reserved strictly for permanent delete, purge, and danger confirmations |
| **Muted / Secondary Labels** | `#8E8D88` | Muted neutral for field labels, section headers, and ghost icons |
| **Table Selection Overlay** | `rgba(46,155,95,0.4)` | Semi-transparent green fill with `1.5px solid #2E9B5F` border |

---

## 🧱 The 5 Core Primitives (`ui/utils/theme.py`)

All screens and dialogs are constructed from five shared primitives:

| Primitive | QSS Selector | Behavior & Styling |
| :--- | :--- | :--- |
| **`SectionLabel`** | `QLabel[class="SectionLabel"]` | `11px`, uppercase, letter-spaced, muted (`#8E8D88`), unboxed section header |
| **`Row`** | `QWidget[class="Row"]` | Grouped list row with muted label (`#8E8D88`), value (`#F8FAFC`), and hairline bottom border (`0.5px solid #232323`) |
| **`Divider`** | `QFrame[class="Divider"]` | Hairline section separator (`0.5px solid #2A2A2A`) |
| **`GhostIconButton`** | `QPushButton[class="GhostIconButton"]` | Borderless neutral button (`#8E8D88` icon), highlighting to `#FFFFFF` on `#262626` background on hover |
| **`Badge`** | `QLabel[class="Badge"]` | Standardized rounded status/identity pill token (`#4CF9B7` on emerald background) |

---

## 📊 Search Grid & Formatting Engine

The main search table (`results_table` in `ui/windows/search_window.py`) provides an Excel-style workspace:

1. **White Table Grid**: Pure `#FFFFFF` background with crisp `#D8CDB4` gridlines and dark `#241F1B` text.
2. **Dark Headers**: Column headers rendered in `#141414` with bold `#FFFFFF` text.
3. **Ghost Toolbar**: Fill color, text color, eraser, undo, redo, and refresh buttons styled consistently as `GhostIconButton`.
4. **Undo / Redo Engine**:
   - Focus-aware keyboard shortcuts: `Ctrl+Z` (Undo) and `Ctrl+Y` (Redo).
   - Reversible stack tracking prior and new cell formatting states per `(client_id, column_key)`.
5. **Selection & Cursor Navigation**: Arrow key grid navigation with persistent cell focus cursor and range selection preservation.
## 🪪 Client Detail Layout (`ui/windows/client_detail_window.py`)

The Client Detail Window provides a single, flowing, unboxed profile layout:

1. **Header Bar**: Back button (`GhostIconButton`), Client Name (`LargeIdentityText`), and Client ID Token (`Badge`).
2. **Sections (`Identity & Contacts`, `Security Credentials`)**:
   - Section headers styled with `SectionLabel`.
   - Fields organized as clean `Row` elements with hairline dividers.
   - Quick action buttons (Eye toggle, Copy) using neutral `GhostIconButton` components.
3. **Notes**: Streamlined notes editor with real-time debounced auto-save feedback.
4. **Services**: Single de-duplicated panel with `GhostIconButton` actions and clean column headers (`Ext`, `Assist`, `Copy`).

---

## 📦 Tracker Dump Workspace (`ui/windows/tracker_dump_window.py`)

The Tracker Dump Workspace provides a high-density audit log of all intercepted API responses and visual filing confirmations:

1. **Header Toolbar**: Features **Refresh**, **Export CSV**, and red **Clear All** (`DangerBtn`) buttons.
2. **Filter Controls**: Multi-field search box, capture method selector (`All`, `SAD_API_Interceptor`, `DOM_Tracker`), and status filter dropdowns.
3. **Dense Data Table**: Crisp table displaying Client Name & PAN, Service/Portal, Period, Ack/ARN Number, Capture Method, and Timestamp.
4. **Action Column**:
   - **View Payload**: Emerald outlined button (`#1A382B` surface, `#4CF9B7` text) opening the JSON inspection modal drawer.
   - **Delete**: Crimson button (`#331A1A` surface, `#FF6B6B` text) for immediate single-row removal.

---

## ⚙️ Unified Settings Hub (`ui/dialogs/unified_settings_dialog.py`)

A centralized, slide-out configuration drawer with vertical tabbed navigation:

1. **Left Navigation Rail**: Icons and category labels for **General Settings**, **Auto-Fill & Services**, **Theme & Display**, **Security & Admin**, and **About / Update**.
2. **Standardized Subsystem Terminology**:
   - **Sera FST**: File Submission Tracker (umbrella tracking system).
   - **Sera SAD**: API Detector (network layer).
   - **Sera DOM**: DOM Detector (visual layer).
   - **Sera SCA**: Sera Clipboard Assist (ambient autofill).
3. **Settings Persistence**: Synchronizes changes seamlessly to `settings.ini` and `database.py` key-value store.

---

## 📜 Scrollbar System

Neutral graphite scrollbar handles on dark tracks:
```css
QScrollBar:vertical, QScrollBar:horizontal {
    border: none;
    background: #141414;
    width: 8px;
    height: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #3A3A3A;
    border: none;
    border-radius: 4px;
    min-height: 22px;
    min-width: 22px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #4F4F4F;
}
```
