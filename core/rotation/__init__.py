"""
core/rotation/__init__.py — Sera Credential Keeper (SCK) rotation package.

Exposes the sub-modules that together power SCK:

  session   — volatile AttemptSession lifecycle and client suppression
  detector  — guarded portal-agnostic vault save (and the old fill-bar detector)
  watcher   — SccHost handler: a failed login for any registered service -> the
              desktop Test & Fill bar (or the persistent new-password card)
  injector  — retired: the old Win32 SendInput engine. The bar now fills through the
              extension's DOM (automation.sck_fill), so nothing imports this module.
"""

from .detector import RotationSave, save_verified_for_rotation
from .session import AttemptSession, RotationSession
from .watcher import SckFailure, SckWatcher

__all__ = [
    "AttemptSession",
    "RotationSession",
    "RotationSave",
    "save_verified_for_rotation",
    "SckFailure",
    "SckWatcher",
]
