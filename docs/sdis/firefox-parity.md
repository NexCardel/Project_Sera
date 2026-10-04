# Browser parity (fictional pages)

Written by `tools/browser_parity.py` on 2026-10-04 15:26. Every page and value here is fictional.
Chrome, Edge and Firefox each ran alone with a throwaway profile; client A was opened with client B
in a second tab.

## What W2-5 must fix (found by W1-7)

1. **Firefox: a closed dropdown's options are lines.** The form's `<select>` adds `April`, `May`,
   `June` (Chrome/Edge: only the chosen value `May`). Kind: list items under a collapsed ComboBox
   (option elements of a select) are not page text; the value and the `Selected:` line stay.
2. **Firefox: a radio button's / checkbox's label is read twice** (`Monthly`, `Quarterly`, `Yearly`,
   `Nil return` x2). Firefox exposes the control's name and the label's text node beside it;
   Chromium folds the label into the control. Kind: a text node whose text equals its parent or
   sibling choice control's name (a `<label>` wrapping its input) is one line.
3. **Firefox: `read_page_nodes` keeps background-tab Documents** (client A: 3 documents = A, B and
   Firefox's hidden New Tab page; the form: 2). `vsdc_uia_text.onscreen_only` reads
   `CurrentIsOffscreen`, which raises on the cache-only elements `read_page_nodes` gets
   (`AutomationElementMode_None`), so every Document is kept. Fix: read the cached
   `IsOffscreen` (30022, already in `_CACHED_PROPERTIES`) when the live one is not available.
   SGT's lines are not affected (`read_page_text` uses live elements: 52 lines, A only).
   key_probe's `read_keys` already uses the cached value (W1-7), so the Firefox fixtures hold one
   Document.
4. **Edge's window title** in a profile with a name is `<page> - Profile 1 - Microsoft​ Edge`
   (a zero-width space inside "Microsoft Edge"): `vsdc_router.BROWSER_SUFFIX_RE` strips nothing.
   UIA's window name has neither (`<page> - Microsoft Edge`). Chrome/Edge behaviour must not
   change, so this is a question for W2-5 (title cleanup is shared), not a Firefox line fix.

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
| chrome | yes | yes | 52 | 0 | no | 0 | 65 (1) | 103 | 22 / 48 / 30 / 37 |
| msedge | yes | yes | 52 | 0 | no | 0 | 65 (1) | 103 | 25 / 97 / 39 / 38 |
| firefox | yes | yes | 52 | 0 | no | 0 | 247 (3) | 335 | 21 / 38 / 77 / 65 |

Lines that differ between browsers: none; common lines in the same order: yes

- chrome Selected: lines: (none)
- msedge Selected: lines: (none)
- firefox Selected: lines: (none)

### client_B

| browser | window | URL read | SGT lines | Selected: | password read | other client's lines | nodes control (documents) | nodes raw | ms url / lines / control / raw |
| :--- | :--- | :--- | ---: | ---: | :--- | :--- | ---: | ---: | :--- |
| chrome | yes | yes | 69 | 0 | no |  | 82 (1) | 129 | 25 / 56 / 69 / 56 |
| msedge | yes | yes | 69 | 0 | no |  | 82 (1) | 129 | 25 / 74 / 105 / 80 |
| firefox | yes | yes | 69 | 0 | no |  | 123 (2) | 171 | 61 / 84 / 64 / 75 |

Lines that differ between browsers: none; common lines in the same order: yes

- chrome Selected: lines: (none)
- msedge Selected: lines: (none)
- firefox Selected: lines: (none)

### form

| browser | window | URL read | SGT lines | Selected: | password read | other client's lines | nodes control (documents) | nodes raw | ms url / lines / control / raw |
| :--- | :--- | :--- | ---: | ---: | :--- | :--- | ---: | ---: | :--- |
| chrome | yes | yes | 25 | 3 | no |  | 25 (1) | 43 | 31 / 32 / 24 / 121 |
| msedge | yes | yes | 25 | 3 | no |  | 25 (1) | 43 | 22 / 40 / 28 / 68 |
| firefox | yes | yes | 32 | 3 | no |  | 162 (2) | 199 | 21 / 33 / 45 / 79 |

Lines that differ between browsers: 7; common lines in the same order: yes

| line | chrome | msedge | firefox |
| :--- | ---: | ---: | ---: |
| `May` | 1 | 1 | 2 |
| `Monthly` | 1 | 1 | 2 |
| `Quarterly` | 1 | 1 | 2 |
| `Yearly` | 1 | 1 | 2 |
| `Nil return` | 1 | 1 | 2 |
| `April` | 0 | 0 | 1 |
| `June` | 0 | 0 | 1 |

- chrome Selected: lines: `Selected: Return period = May`; `Selected: Quarterly`; `Selected: Nil return`
- msedge Selected: lines: `Selected: Return period = May`; `Selected: Quarterly`; `Selected: Nil return`
- firefox Selected: lines: `Selected: Return period = May`; `Selected: Quarterly`; `Selected: Nil return`

