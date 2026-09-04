# Temporary SDC ITR Assessment-Year Issue

## Observed behavior

The Income Tax portal's View Filed Returns page displays a valid filing card for `A.Y. 2026-27`, but also contains introductory text saying returns are available starting from `Assessment Year 2013-14`.

SDC's generic `extractAY()` scanned the whole page and treated the introductory minimum-year notice as the active assessment year. The landing handler then wrote that value into `session.ay`, causing a false LTT row:

`AY 2013-14 — Not submitted`

The finalized filing payload itself correctly reported `AY 2026-27`, ARN `827916720300726`, and status `Filed & Verified (Processed)`.

## Root cause

The protocol's filing-card extractor selects the latest AY among cards associated with acknowledgement numbers. However, other ITR lifecycle handlers used generic page-wide AY extraction and unconditionally overwrote the session cache.

## Fix applied

All Chrome/Firefox and `source_2` ITR protocol copies now use `_preferAssessmentYear()`. A candidate AY can update the cached AY only when it is equal to or newer than the existing value. Therefore, a lower-confidence `AY 2013-14` notice cannot replace a previously captured `AY 2026-27`.

The restore point immediately before this fix is commit `0fa5ecb`.

## Validation

- JavaScript syntax checks should be run for all four ITR protocol copies.
- Test with the View Filed Returns page and confirm the LTT contains only the finalized filing row for `AY 2026-27`; landing/navigation captures may remain in the session timeline but must not create filing rows.
