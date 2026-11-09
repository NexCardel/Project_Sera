# Archived pre-dev aliases

Archived on 2026-10-15. Nothing in `tools/pre_dev/class_diff/` imports these six files, and the
cards / arrangement work does not need them.

Each one is a short alias: it puts the repository root on `sys.path` and stands in for the real
module in `core/sdis/`. The real modules are unchanged and are still used by the app.

| File | Stands in for | What the real module does |
|---|---|---|
| `identity.py` | `core/sdis/identity.py` | Works out which client a map belongs to (PAN/GSTIN on the page, SGT session, data fingerprint) |
| `links.py` | `core/sdis/links.py` | Masks client values inside page links so that all clients share one link |
| `memory.py` | `core/sdis/memory.py` | Page memory: merges every client's map of a link and votes on template vs data |
| `noise.py` | `core/sdis/noise.py` | Noise over time: rotating notices, day-to-day changes, furniture, carousel positions |
| `relevance.py` | `core/sdis/relevance.py` | Ranks datapoints by counting (one client = one vote per page) |
| `screens.py` | `core/sdis/screens.py` | Tells apart different screens at one link (form, popup, success message) |

They still work from here. `python tools/pre_dev/class_diff/archive/memory.py` runs the memory tool as before.
To use one as a module again, move it back up one folder.
