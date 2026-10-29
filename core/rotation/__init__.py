"""
core/rotation/__init__.py — Sera Credential Keeper (SCK) rotation package.

Exposes the sub-modules that together power SCK:

  session   — volatile AttemptSession lifecycle and client suppression
  injector  — Win32 SendInput unicode keystroke engine
  detector  — guarded portal-agnostic vault save (and the old fill-bar detector)
  watcher   — SccHost handler: a failed login for any registered service -> the
              persistent new-password card
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
