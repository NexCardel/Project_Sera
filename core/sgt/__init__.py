"""
core/sgt - Sera Global Tracker (SGT)
====================================
Crosshair-independent, configuration-driven capture: every page of the allowed portals is
read as lines of text, and the datapoints on it are resolved by the field specs in
specs/sgt_fields.json. Nothing about an individual datapoint lives in code - adding one is an
edit to that file. See docs/sgt-blueprint.md.

  capture/        reading the browser (UI Automation first, OCR when a page is blind)
    loop            the capture loop: which windows, the scope gate, when to ask for a read
    reader          page reads as tickets, one read lane per browser window
    ocr_lane        OCR of a blind page off the capture thread
    windows         which browser windows are watched; browser and portal names
    gate            the frame signature that says a page changed
    uia_text        UI Automation text reads (also used by the crosshair engines)
    scope           the portal allowlist: which pages may be read at all
  engine/         the engine (SgtEngine): sessions, identity, datasets, tracker rows
  specs/          the field specs and the code that applies them
    specs           loads sgt_fields.json, refuses unsafe or self-contradicting specs
    resolver        lines of text + specs -> the profile and dataset values on one page
    toolbox         the small generic library of transforms and checks specs borrow from
  router_link     how the VSDC router (core/vsdc/vsdc_router) builds and drives SGT
  containers      SDIS dataset containers and Others values carried on SGT rows
  evidence        the sgt_ev evidence block on every SGT row
  client_link     names a client from a saved client's portal user ID
  corpus          page recording for replay
  replay          replays recorded pages through a fresh engine
  spec_health     spec hit counts: notices a portal that changed
  digest          the daily capture digest
  repair          repairs rows SGT once wrote under a moved key

Old names (before 2026-11): core/sgt/sgt_shadow.py (SgtShadow) is core/sgt/engine (SgtEngine);
core/sgt/sgt_<x>.py is core/sgt/<x>.py, or specs/<x>.py for specs, resolver and toolbox
(sgt_health is spec_health); core/vsdc/vsdc_<x>.py of the capture path is core/sgt/capture/<x>.py
(vsdc_capture is capture/loop). SGT's shadow mode was removed: a stored "shadow" runs live.
"""
