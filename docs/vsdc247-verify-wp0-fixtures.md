# VSDC247-V — WP0 fixtures and page evidence

**Status (2026-10-18):** WP1 and WP2 are built in this worktree on synthetic fixtures (section 7). The
**live owner check is still open**: the screenshots below are wording and layout evidence, not OCR runs on
the live portal. Plan: [vsdc247-sgt-verifier-plan.md](vsdc247-sgt-verifier-plan.md), section 8.

## 1. Page kinds and where they live

The owner's screenshots (2026-10-17/18) are the authority for the filing pages. The SGT corpus
(`~/AmanAssociates_Sera/sgt_corpus/pages_*.jsonl`, 2026-09-22 to 2026-10-06) is the authority for the login
page and the list pages. Query and fragment are stripped everywhere.

| Plan page kind | URL path (host + path) | Evidence |
| :-- | :-- | :-- |
| `gst_login` | `services.gst.gov.in/services/login` | screenshot + corpus (364 UIA / 174 OCR records) |
| `gstr1_submit` | `return.gst.gov.in/returns/auth/file` | screenshot: green success banner after filing |
| `gstr3b_submit` | `return.gst.gov.in/returns/auth/gstr3b/filing` | screenshot: "Filing Successful" popup, and the verification page before filing |

Paths that are **not** registered (the plan says ignore everything else):
* `return.gst.gov.in/returns/auth/gstr1` and `.../gstr3b` (no `/filing`): list and status pages, no ARN.
  The corpus holds 82 UIA / 68 OCR records on the GSTR-1 one and 59 / 33 on the GSTR-3B one.
* `services/auth/fowelcome`, `services/auth/dashboard`, `returns/auth/dashboard`, `returns/auth/menucomp`,
  `returns/auth/comparison`, `returns2/auth/cmp08`, `returns/auth/gstr1/b2b/summary`,
  `returns/auth/gstr1/gstr1sum`, `services/auth/myprofile`, `services/auth/manageapiaccess`, `gst.gov.in/docadvisor/`.

## 2. What the evidence shows

* **Login username.** The field is labelled `Username*`, with the placeholder `Enter Username` while empty.
  Once typed, the value sits on its own line under the label. The corpus also has an extension label
  `? Username Injected` (48 OCR records). It is not the portal's, and the reader ignores it.
* **Success wording, GSTR-1** (banner on `/returns/auth/file`): "GSTR1 of GSTIN - … for the Return Period -
  '<Month> - <FY>' has been successfully filed. The Acknowledgment Reference Number is <ARN>." The form is
  written **GSTR1 without a hyphen**. A card below shows `Return Period - <Quarter>(Q)` and `Status - Filed`.
* **Success wording, GSTR-3B** (popup on `/gstr3b/filing`): "GSTR-3B of GSTIN … for the period Jul-Sep - 2026
  has been successfully filed on <date> at <time>. The Acknowledgment Reference Number: is <ARN>."
* **Before filing** (`/gstr3b/filing`, and the GSTR-1 page before submission): the declaration text and buttons
  (`FILE GSTR-3B WITH EVC`, `FILE GSTR-3B WITH DSC`), no ARN. Must give no reading.
* **ARN shape, as seen on the two screenshots** (shape only, not the values): the GSTR-3B ARN is `AB` + 13
  digits; the GSTR-1 ARN is `AB` + 12 digits + one letter. Both match the existing token pattern
  (`[A-Z]{2}` + 11–14 digits + optional check letter).
* **Period.** The shared reader reads `Jul-Sep` from the 3B popup and `September (FY 2026-27)` from the GSTR-1
  banner. The verifier reads the full wording, `Jul-Sep - 2026` and `September - 2026-27`, for the ledger.
* **Corpus limits.** No corpus record holds an ARN-labelled line. The success wording above comes from the
  screenshots, not from the corpus.

## 3. Fixtures in `tests/fixtures/vsdc247_verify/`

All files are **synthetic**. Each copies the line structure and wording of a screenshot. Every value is a
placeholder (`EXAMPLEUSER01`, GSTIN `00AAAAA0000A1Z0`, GSTR-1 ARN `AB000000000000U`, GSTR-3B ARN
`AB0000000000000`, `EXAMPLE TAXPAYER`). **Nothing from the screenshots or the corpus is committed**: they
show a real taxpayer's name, GSTIN and ARNs.

| File | Kind | Expected |
| :-- | :-- | :-- |
| `gst_login_01.json` | `gst_login` | empty field, placeholder shown: no username |
| `gst_login_02.json` | `gst_login` | typed value, masked password: `EXAMPLEUSER01` (password never returned) |
| `gstr1_submit_success_01.json` | `gstr1_submit` | ARN found; pending (first read) |
| `gstr1_submit_success_02.json` | `gstr1_submit` | same ARN on a different frame: saved, `period = September - 2026-27` |
| `gstr1_list_negative_01.json` | `gstr1_submit` (pre-filing page) | no reading |
| `gstr3b_submit_success_01.json` | `gstr3b_submit` | ARN found; saved on a second different frame, `period = Jul-Sep - 2026` |
| `gstr3b_prefiling_negative_01.json` | `gstr3b_submit` (verification page) | no reading |
| `gstr3b_list_negative_01.json` | not registered (list path) | not read at all |

## 4. Gaps still open

1. **Live OCR of the login field.** Whether the typed username stays readable in the field after the page
   changes (plan risk 4). The fixtures show the value on screen, not what OCR returns.
2. **Live OCR of the success page.** Whether OCR reads the ARN and the green banner cleanly at real window
   sizes (plan WP7 covers this).
3. **GST ARN shape.** Two real ARNs now match the shape (plan risk 5). Confirm with more filings before the
   shape stops being evidence.
4. **Several GST tabs in one window** can mislink a username (plan 5.3). The page GSTIN check covers this, but
   it is untested on real tabs.

## 5. Owner walk-through (now partly covered)

The screenshots answered most of the earlier checklist. Still needed on the live portal, one filing of each
kind: take one OCR capture per page (or a screenshot kept out of the repo) with the window at its normal size,
and say whether the username stays in the field after you click Login. Do not paste the password, ARN, GSTIN or
username into chat.

## 6. Notes for the implementer

* `tests/test_page_gst_submission.html` is a simulation, not the real portal. Its wording was the earlier
  provisional cue. The screenshots now replace it.
* Work is on branch `vsdc247-verify` in worktree `../APP-vsdc-verify`. Nothing is committed yet.

## 7. Built on synthetic fixtures (2026-10-18)

* Code: `core/vsdc/vsdc247_verify_pages.py` (registry, login username), `core/vsdc/vsdc247_verify.py`
  (window link, period reader, observation, two-read ledger, jsonl sink). The shared VSDC247 code is unchanged.
* Tests: `tests/test_vsdc247_verify_fixtures.py` and `tests/test_vsdc247_verify_ledger.py`, 36 tests, all pass.
  The existing `tests/test_vsdc247.py` and `tests/test_vsdc247_integration.py` also pass (114 across the four
  files). The full suite was not run, because only new files were added.
* **Correction to the earlier note:** the PROBABLE result (score 80) came from wording I had invented for the
  simulation, which had no period label. With the wording the portal actually shows, the shared reader gives
  CERTAIN (85) on all three success frames. So no period rule was needed in the tier, and none was added.
* Ledger entries hold structured fields only: ARN, form, period, page kind, sanitized URL, window id, score,
  confirmation (single frame or two reads), client block and device name. No page text and no password.

## 8. Real Windows OCR check (2026-10-18)

* `tools/vsdc247_verify_ocr_check.py` renders each synthetic page, reads it with the project's Windows OCR,
  and feeds the verifier. Run it on Windows: `python tools/vsdc247_verify_ocr_check.py`. Result: 8 of 8 frames
  as expected, and the GSTR-1 ARN is saved on the second frame.
* The first real-OCR run found a bug the text-only tests missed. OCR reads "GSTR1" as "GSTRI", so the GSTR-1
  page was never registered. Fixed: the page cues and the form reading accept 1/I/l and 3B/38.
* Usernames: OCR reads "EXAMPLEUSER01" as "EXAMPLEUSEROI". The client lookup now tries OCR-confusable variants
  after an exact miss. It accepts a match only when every hit is one client, and marks it `match: "ocr_variant"`.
* Real OCR output is kept as fixtures (`gstr1_ocr_real_01/02.json`, `gst_login_ocr_real_02.json`).
* GSTIN digits can also come out as O, so the page-GSTIN identity check can miss on OCR. That leaves
  `agreement: "unknown"`, never a false "agrees". Not yet repaired.
