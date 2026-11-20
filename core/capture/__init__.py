"""
core/capture - the UIA-only half of reading the browser (UCW, docs/ucw/ucw-plan.md)
===================================================================================
reader (lanes, PageReader), uia_text (helpers), scope (portal URLs) and windows (which browser windows
are watched) are what the capture worker process needs; they never import core.vsdc, Qt or the
database while they load. protocol is the frame format between Sera and the worker, worker_main the
worker process itself, remote the Sera side (RemoteReader, the translation layer, and its supervisor).

SGT's own capture loop, frame gate and OCR lane stay in core/sgt/capture.
"""
