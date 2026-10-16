# Experiment: anchor-relative Y in the look-alike score (2026-10-17)

Script: `tools/pre_dev/class_diff/exp_anchor_y.py`. core/sdis is untouched: the script swaps
`compare.align` for a copy of `core.sdis.align.align` whose look-alike score adds

    W * 1 / (1 + |dy_latest - dy_previous| / row_height)

where dy = node top minus the top of the anchor before the gap (the anchor after it when the gap is at
the page top). W = 0 is checked to reproduce today's engine row for row.

## Data
Every comp page in `output/` paired with every other comp page of the same link from another session,
both directions: 8 comparisons, 4 page pairs, 2 links (ITR dashboard x3 sessions, GST dashboard x2).
GSTR-1 summary has one session only, so it is not in the sample.

## Results (totals over the 8 comparisons)

| W   | ambiguous | types differ | furniture | VARIABLE_ALIGNMENT | pairs changed vs baseline |
|-----|----------:|-------------:|----------:|-------------------:|--------------------------:|
| 0   | 14        | 0            | 0         | 20                 | -                         |
| 0.5 | 14        | 0            | 0         | 20                 | 0                         |
| 1   | 2         | 0            | 0         | 20                 | 0                         |
| 2   | 2         | 0            | 0         | 20                 | 0                         |

- All look-alike groups are on the ITR dashboard (step dates, value-box links, the session timer).
  The GST dashboard pair has no look-alikes at all.
- W = 1 settles 12 of the 14 ambiguous flags: the chosen partner sits 21-29 px from where the anchor
  predicts, the alternatives 70-760 px. W = 0.5 is too weak to clear `AMBIGUOUS_MARGIN` (0.5).
- The 2 left are the session-timer texts: all on one row (same Y), so Y cannot help. That's the
  column term's job.
- No pairing changed, and types-differ / VARIABLE_ALIGNMENT did not move. On this data the Y term makes
  the engine **more confident, not more correct**: the baseline already picked these partners.

## Limits
- Small sample: 16 look-alike pairs on one portal page.
- The term only acts on uneven look-alikes. Look-alikes with even counts are paired in page order and never scored.
- Comp pages merge snapshots without scroll correction (the GST dashboards hold negative Y values).
  The anchor and the node can come from different snapshots, so dy can be off by a scroll.

## Next
To show an accuracy gain (not just confidence), it needs pages where look-alikes are actually
mispaired: list pages with an extra row on one client (GST returns lists, GSTR-1 summary from a second
client). Re-run `python exp_anchor_y.py` once those comp pages exist. The repaired pairs land in
`output/exp_anchor_y.csv`, which holds real values, so keep it on this PC.
