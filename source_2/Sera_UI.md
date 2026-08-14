# Sera UI Design System & Styling Specifications

This document records the official desktop UI design tokens, component architecture, and styling rules for Project Sera. All user interface components adhere to these specifications to guarantee visual consistency.

---

## 🎨 Design Tokens & Theme Palette

The application uses a high-contrast dark theme optimized for readability:

| System Layer / Element | Hex Token | Description |
| :--- | :--- | :--- |
| **App Main Background** | `#292929` | Lighter dark tone for the main window body |
| **Panel / GroupBox Surface** | `#0A0A0A` | Darker surface (`bl`) for sidebars, cards, and group boxes |
| **Input / Card Tile Surface** | `#171717` | Deep dark surface for text boxes, combo boxes, and field cards |
| **Cell Grid Table Background** | `#FFFFFF` | Pure white background for all search results and data tables |
| **Cell Grid Table Text** | `#241F1B` | High-contrast dark charcoal text for table item contents |
| **Table Column Headers** | `#0A0A0A` | `bl` dark shade with bold white (`#FFFFFF`) text & `#262626` borders |
| **Scrollbars (Vertical/Horizontal)**| `#0A0A0A` | `bl` dark shade for all scrollbar tracks, handles, and corners |
| **Primary Accent / CTA** | `#FF4D4D` | Vivid red for primary action buttons (`btn_add_client`, `btn_ext`) |
| **Sidebar Accent** | `#2E9B5F` | Emerald green sidebar background and card title accents |
| **Table Selection Overlay** | `rgba(46,155,95,0.4)` | Semi-transparent green fill with `1.5px solid #2E9B5F` border |

---

## 📊 Search Grid & Formatting Engine

The main search table (`results_table` in `ui/windows/search_window.py`) provides an Excel-style workspace:

1. **White Table Grid**: Pure `#FFFFFF` background with crisp `#D8CDB4` gridlines and dark `#241F1B` text.
2. **`bl` Dark Headers**: Column headers rendered in `#0A0A0A` (`bl` dark shade) with bold `#FFFFFF` text.
3. **Right-Click & Header Toolbar Formatting**:
   - **Cell Fill Color (Background)**: Custom preset colors (Yellow, Green, Red, Blue, Purple, Gray, Navy).
   - **Cell Text Color (Foreground)**: Text color presets for emphasis.
   - **Clear Formatting**: Resets selected cell ranges to default styling.
4. **Undo / Redo Engine**:
   - Focus-aware keyboard shortcuts: `Ctrl+Z` (Undo) and `Ctrl+Y` (Redo).
   - Reversible stack tracking prior and new cell formatting states per `(client_id, column_key)`.
5. **Selection Preservation**: Search query updates preserve active cell cursor (`currentRow()`, `currentColumn()`) and range selections (`selectedRanges()`).

---

## 🪪 Client Detail Layout (`ui/windows/client_detail_window.py`)

The Client Detail Window provides a high-contrast view of client profiles:

1. **Header Bar**: Title in bold white (`#FFFFFF`), token badge in emerald (`#2E9B5F`), vector back button in `#FFFFFF`.
2. **Field Cards (`Identity & Contacts`, `Security Credentials`)**:
   - Card surface `#171717` with `#262626` border.
   - Field Labels: Soft slate `#94A3B8` (11px, bold).
   - Field Values / Password Masks: High-contrast white `#FFFFFF` (13px).
   - Quick action buttons (Eye toggle, Copy): `#262626` tile with `#FFFFFF` icons.
3. **Notes Field**: Pure white (`#FFFFFF`) input container with `#241F1B` text and `#D8CDB4` border.
4. **Service Management**:
   - Service labels in crisp white (`#FFFFFF`).
   - Action buttons (`Assist` and `Copy`) feature red outlined borders (`#FF4D4D`) with matching red icons and subtle hover highlights.

---

## 📜 Scrollbar System

All scrollbars across the app feature vibrant red (`#FF4D4D`) handles on `bl` dark tracks:
```css
QScrollBar:vertical, QScrollBar:horizontal {
    border: none;
    background: #0A0A0A;
    width: 8px;
    height: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #FF4D4D;
    border: 1px solid #E63939;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #FF6666;
    border-color: #FF4D4D;
}
```

---

## 🛠️ Shared Component Selectors (`ui/utils/theme.py`)

- `ClientDetailCard`: Grouped card containers (`#0A0A0A`).
- `ClientDetailField`: Read-only field tiles (`#171717`).
- `DetailFieldLabel` / `DetailFieldValue`: Typography hierarchy (`#94A3B8` / `#FFFFFF`).
- `DetailNotes`: Notes text editors (`#FFFFFF`).
- `ToolDialog`: Admin tool dialog windows (`#292929`).
- `PageTitle` / `SectionTitle`: High-contrast headers (`#F8FAFC`).
