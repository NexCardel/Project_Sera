# Sera UI/UX Redesign Blueprint

*Companion to `Sera_UI.md` (current design tokens) and `Sera_Audit_Log_Improvement_Blueprint.md` (same format/rollout style). This doc scopes the full visual rework, not just Client Detail.*

---

## 1. Why the current UI feels heavy (root causes)

Pulled directly from the existing token table in `Sera_UI.md`:

- **Three stacked dark surfaces** (`#292929` app bg → `#0A0A0A` panel → `#171717` card tile) are used almost everywhere, so every screen is "boxes inside boxes inside boxes." There's no single screen where you only see one or two surface levels.
- **Red (`#FF4D4D`) is overloaded.** It's the primary CTA color, the scrollbar color, *and* the outline on every service action button. Nothing is reserved as "this red means something important" — so nothing reads as urgent, because everything is red.
- **No spacing/typography scale.** Field labels, section headers, and values are all similar weight/size across windows. Nothing establishes hierarchy.
- **Repeated headers.** "Service Management" labeled twice in Client Detail is one instance of a broader pattern — sections re-announce themselves instead of using position/whitespace to group.
- **Cards over rows.** Every piece of data gets its own bordered container (`ClientDetailField`, `ClientDetailCard`) instead of being grouped into a shared list. This is the single biggest contributor to clutter.

None of this needs new functionality — it's the same data, restructured.

---

## 2. New primitives (define once, reuse everywhere)

Add these to `ui/utils/theme.py` as shared QSS selectors. Every window below should be rebuilt out of these instead of one-off styling.

| Primitive | Replaces | Behavior |
|---|---|---|
| `SectionLabel` | Repeated card headers | 11px, muted (`#6E6D67`), letter-spaced, sits above a group — not boxed |
| `Row` | `ClientDetailField` card tiles | Label (muted, 12px) over/left of value (13–14px), bottom hairline divider (`0.5px solid #232323`) — no border/background of its own |
| `Divider` | Nested card borders | `0.5px solid #2A2A2A`, used to separate sections instead of wrapping each section in its own panel |
| `GhostIconButton` | Red-outlined Assist/Copy buttons | No border by default, muted icon color (`#6E6D67`), only brightens on hover — used for routine/repeatable actions |
| `Badge` (pill) | Token badge, method tags in Tracker Dump | Small rounded pill, tinted background + matching text color from **one** ramp — for status/identity, not decoration |

**Rule going forward:** one accent color per screen for anything that isn't a hover state. Red stays reserved for destructive actions (delete, purge) and the single primary CTA (`+ Add Client`). Everything else — copy, assist, autofill triggers — uses the neutral ghost style and only picks up color on hover/focus.

---

## 3. File-by-file changes

### `ui/utils/theme.py` (do this first — everything else depends on it)
- Add the five primitives above as global QSS classes.
- Collapse the three-surface stack to two in most contexts: page bg (`#1C1C1C`–`#292929` range) and one panel tone. Reserve the third, darkest tone for genuinely nested/modal contexts (dialogs), not routine content.
- Move scrollbar color off red — a neutral `#3A3A3A` handle with hover brighten is enough; red scrollbars currently compete with red CTAs for attention.

### `ui/windows/client_detail_window.py` (highest-visibility fix)
- Rebuild per the mockup already shown: single flowing panel, `SectionLabel` + `Row` for Identity and Security Credentials, `GhostIconButton` pairs for Services, collapsed one-line Notes prompt when empty.
- Delete the duplicate "Service Management" header.
- Fix the content clipping at the top of the scroll area (name/PAN currently cut off above the fold in the screenshot).

### `ui/windows/search_window.py`
- Keep the Excel-style grid — that's a deliberate, useful metaphor for this app, don't flatten it.
- Do fix: the header toolbar's fill/text/clear/undo/redo/refresh/archive icon buttons currently have inconsistent visual weight. Standardize them all as `GhostIconButton` with consistent spacing.
- Move the vertical scrollbar so it doesn't visually cut through the "No." column at the list's right edge (currently reads like a stray red line through the data).

### `ui/shell/sidebar.py`
- Nav items are already fairly clean; main change is swapping the active-item highlight and any icon accents to pull from the new shared tokens instead of ad hoc colors, so sidebar and content don't feel like two different apps.

### `ui/windows/admin_window.py` and `ui/dialogs/*.py`
- Apply the same `Row`/`Divider`/`SectionLabel` pattern to Admin Panel, Sera Sync dialog, MCL Manager, Service Manager, CSV Import — right now each dialog has its own bespoke layout density. Standardizing here is what will make the "smooth" feeling hold up across the whole app, not just one screen.
- Dialog buttons: only the dialog's primary action (Save, Confirm, Push) gets accent color; Cancel/Close stay ghost style.

### `ui/components/toast.py` / `ui/services/alert_service.py`
- No structural change — these already use semantic success/info/warning/error levels correctly. Just make sure their colors are pulled from the same token file so they don't drift from the rest of the app.

### `Sera_UI.md`
- Needs a full rewrite once the above lands — it currently documents the *old* three-surface, red-everywhere system as the source of truth. Update the token table, add the five new primitives with QSS snippets, and replace the Client Detail section's description to match the rebuilt layout.

---

## 4. Rollout order

Same batching logic as the Audit Log blueprint — land shared primitives before anything that depends on them, so you're not restyling the same screen twice.

1. **Foundation:** `theme.py` primitives (`SectionLabel`, `Row`, `Divider`, `GhostIconButton`, `Badge`), surface consolidation, scrollbar recolor.
2. **Highest-impact screen:** `client_detail_window.py` rebuild — this is the one you already flagged, and it exercises every new primitive.
3. **Consistency pass:** `search_window.py` toolbar + scrollbar fix, `sidebar.py` token alignment.
4. **Breadth pass:** Admin window + all dialogs restyled onto the same primitives.
5. **Docs:** rewrite `Sera_UI.md` to describe the new system as current, not aspirational.

Steps 1–2 alone will fix most of what's bothering you. 3–4 are what make the rest of the app feel like it belongs to the same redesign instead of one nice screen surrounded by the old look.
