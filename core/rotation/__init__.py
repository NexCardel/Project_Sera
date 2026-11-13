"""
core/rotation/__init__.py — Sera Credential Keeper (SCK) rotation package.

Exposes the sub-modules that together power SCK:

  session   — volatile AttemptSession lifecycle and client suppression
  detector  — guarded portal-agnostic vault save
  watcher   — SccHost handler: a failed login for any registered service -> the
              persistent new-password card (the only SCK widget)
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
