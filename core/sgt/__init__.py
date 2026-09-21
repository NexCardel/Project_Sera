"""
core/sgt — Sera Global Tracker
==============================
Crosshair-independent, configuration-driven capture: every page of the allowed portals is
read as lines of text, and the datapoints on it are resolved by the field specs in
sgt_fields.json. Nothing about an individual datapoint lives in code - adding one is an
edit to that file. See docs/sgt-blueprint.md.

  sgt_toolbox   the small generic library of transforms and checks specs borrow from
  sgt_specs     loads sgt_fields.json, refuses unsafe or self-contradicting specs
  sgt_resolver  lines of text + specs -> the profile and dataset values on one page
"""
