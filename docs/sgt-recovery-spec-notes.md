# SGT recovery specs: the labels still needed (WP11)

Two list pages can recover a filing whose own confirmation page was missed. Only the page's **latest** filing is taken (`latest_period_only`, R5); the row is marked `recovered`. Both specs ship **disabled** in `core/sgt/sgt_fields.json` because their labels have not been confirmed from the owner's corpus.

## What the owner pastes

Paste **label lines only, never values**. Write `<value>` where a value stood. No names, PAN, GSTIN, ARN or acknowledgement numbers, no links outside the portals.

How to find the pages: `venv/Scripts/python.exe tools/sgt_replay.py show`, open a corpus day that has the page, and copy the label lines (in order, one card).

### 1. GST Portal, Track Return Status (`gst_track_return_status`)

Paste one card's lines, in the order the page gives them, for these labels:

| Datapoint | Label wording on the page | Notes to include |
|---|---|---|
| Return form | `Return Type` is a guess | is the value `GSTR3B`, `GSTR-3B`, or longer? |
| Period | `Return Period` is a guess | month spelling: `August 2026`, `Aug-2026`, ... ? |
| ARN | `ARN` is a guess | the ARN under the label, or on the same line? |
| Filing date | `Date of Filing` is a guess | date format shown |
| Status | `Status` is a guess | the words used for filed and not filed |

Also say: what line starts a card, how many lines a card spans, and whether the financial year is printed once for the page or on each card.

Example of the shape wanted:

```
Return Type
<value>
Return Period
<value>
```

### 2. Income Tax, View Filed Returns (`itr_filed_returns_history`)

The page is already read by `itr_dataset_cards` (golden session `itr_filed_returns_history`). Paste label lines only if the owner's corpus shows a layout that spec cannot read: a card with no `Acknowledgement No :`, or a different list page. If every corpus page reads, write "no change needed" here and leave the stub disabled.

## State of the specs

- `gst_track_return_status`: complete, self-tested, `disabled: true`. **Every label is a guess.** The golden session `gst_track_return_status` enables it through an overlay, so the code path is tested without the product running it.
- `itr_filed_returns_history`: a stub (no fields), `disabled: true`.

## To enable, after the labels are pasted

1. In `core/sgt/sgt_fields.json`, replace the guessed labels and patterns with the confirmed ones. Update the spec's `examples` and the golden page to match.
2. Delete the `"disabled": true` line (and the DISABLED text in `note`).
3. `venv/Scripts/python.exe tools/sgt_replay.py diff` over the corpus.

Pass: the diff adds only rows marked `recovered` (or `uia` after a later confirmation), each under the right client and with an ARN that was on the page; no existing row changes or loses a status; the `tests/test_sgt_replay.py` goldens and noise test pass.
