# Browser parity (fictional pages)

Written by `tools/browser_parity.py` on 2026-10-04 (rerun after W2-5's fixes). Every page and value here is fictional.
Chrome, Edge and Firefox each ran alone with a throwaway profile; client A was opened with client B
in a second tab.

## What W2-5 fixed (found by W1-7)

1. **Firefox: a closed dropdown's options were lines** (`April`, `May`, `June`; Chrome/Edge: only the
   chosen value). Fixed as a kind for Gecko elements: the descendants of a ComboBox are not page text
   (`vsdc_uia_text._collect_descendant_lines`, `uia_nodes.lines_from_nodes`); the value and the
   `Selected:` line stay.
2. **Firefox: a radio button's / checkbox's label was read twice.** Firefox gives the `<label>` as a named
   container right before the control; a container whose name is repeated by the choice control
   directly after it is one line (Gecko only; a text box labelled by a same-named container keeps both).
3. **`onscreen_only` kept background-tab Documents in `read_page_nodes`** (Firefox: 3 documents).
   `CurrentIsOffscreen` raises on cache-only elements; it now falls back to the cached `IsOffscreen`
   (30022). After the fix every page reads 1 Document in all three browsers.
4. **Edge's window title** `<page> - Profile 1 - Microsoft<zero-width space> Edge`: the title cleanup
   (`vsdc_router.BROWSER_SUFFIX_RE`) now allows zero-width spaces inside "Microsoft Edge", so
   the suffix is stripped. The profile name ("- Profile 1") stays in the cleaned title; it only matters
   when the address bar cannot be read, and it is stable for the window.

Gecko is recognised by the element's framework id (`Gecko`), never by the window title. Node trees
carry `gecko: true` on a Firefox document's first node so `lines_from_nodes` gives the same lines as
the live reader. The node tree itself still holds the option elements (SDIS's alignment sees them; W3-4).

Same in all three: every client A/B line, in the same order; the three `Selected:` lines; the
fictional password is never read; client B's values never appear while A is in front; the address
is read (Firefox's has `http://`, Chromium's not; `page_link` gives the same link).

Measured on the way (not bugs in SGT): a browser window behind other windows yields **no lines** -
Chromium builds no page tree for an occluded window (even with `--force-renderer-accessibility`) and
Firefox marks its front page offscreen. The tool turns occlusion off in its throwaway browsers
(`--disable-features=CalculateNativeWinOcclusion --disable-backgrounding-occluded-windows`;
Firefox pref `widget.windows.window_occlusion_tracking.enabled` false). Firefox also needed four more
first-run prefs than the blueprint listed (`browser.preonboarding.enabled`, `termsofuse.acceptedDate`,
two `datareporting.policy.*`), or the Terms of Use modal covers the page.

The tables below are the tool's output; rerun `tools/browser_parity.py --md docs/sdis/firefox-parity.md`
to refresh them (that rewrites this file, so keep the section above).

### client_A

| browser | window | URL read | SGT lines | Selected: | password read | other client's lines | nodes control (documents) | nodes raw | ms url / lines / control / raw |
| :--- | :--- | :--- | ---: | ---: | :--- | :--- | ---: | ---: | :--- |
| chrome | yes | yes | 52 | 0 | no | 0 | 65 (1) | 103 | 69 / 88 / 95 / 90 |
| msedge | yes | yes | 52 | 0 | no | 0 | 65 (1) | 103 | 32 / 48 / 39 / 42 |
| firefox | yes | yes | 52 | 0 | no | 0 | 66 (1) | 98 | 18 / 46 / 40 / 46 |

Lines that differ between browsers: none; common lines in the same order: yes

- chrome Selected: lines: (none)
- msedge Selected: lines: (none)
- firefox Selected: lines: (none)

### client_B

| browser | window | URL read | SGT lines | Selected: | password read | other client's lines | nodes control (documents) | nodes raw | ms url / lines / control / raw |
| :--- | :--- | :--- | ---: | ---: | :--- | :--- | ---: | ---: | :--- |
| chrome | yes | yes | 69 | 0 | no |  | 82 (1) | 129 | 23 / 52 / 36 / 43 |
| msedge | yes | yes | 69 | 0 | no |  | 82 (1) | 129 | 29 / 54 / 40 / 49 |
| firefox | yes | yes | 69 | 0 | no |  | 83 (1) | 124 | 16 / 54 / 31 / 36 |

Lines that differ between browsers: none; common lines in the same order: yes

- chrome Selected: lines: (none)
- msedge Selected: lines: (none)
- firefox Selected: lines: (none)

### form

| browser | window | URL read | SGT lines | Selected: | password read | other client's lines | nodes control (documents) | nodes raw | ms url / lines / control / raw |
| :--- | :--- | :--- | ---: | ---: | :--- | :--- | ---: | ---: | :--- |
| chrome | yes | yes | 25 | 3 | no |  | 25 (1) | 43 | 22 / 35 / 26 / 36 |
| msedge | yes | yes | 25 | 3 | no |  | 25 (1) | 43 | 24 / 42 / 27 / 46 |
| firefox | yes | yes | 25 | 3 | no |  | 40 (1) | 56 | 17 / 32 / 23 / 22 |

Lines that differ between browsers: none; common lines in the same order: yes

- chrome Selected: lines: `Selected: Return period = May`; `Selected: Quarterly`; `Selected: Nil return`
- msedge Selected: lines: `Selected: Return period = May`; `Selected: Quarterly`; `Selected: Nil return`
- firefox Selected: lines: `Selected: Return period = May`; `Selected: Quarterly`; `Selected: Nil return`
