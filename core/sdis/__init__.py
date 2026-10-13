"""
core/sdis - Sera Distill (SDIS): from what SGT captures to the datapoints of a page
==================================================================================
The pipeline, one line each:

  capture   SGT-C reads a page from the browser's accessibility tree (core/sgt/, not here)
  merge     link_map: the snapshots of one client on one page link merge into one map
  memory    memory: the maps of several clients of one page link say what is fixed and what is data
  labels    labels / relevance: every value gets its label and how much it matters
  dialog    the Distill... dialog shows the result and the user decides

Client data stays in data_dir() (core/sdis/paths.py) on the admin PC and is never logged.
"""
