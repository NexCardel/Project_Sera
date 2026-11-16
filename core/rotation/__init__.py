"""
core/rotation/__init__.py - Sera Credential Keeper (SCK) rotation package.

  attempts   - per-(service, window) attempts, expiring candidates, SCA pause and lock
  detector   - guarded portal-agnostic vault save
  watcher    - SccHost handler: a failed login for any registered service (or an unknown ID) ->
               the persistent new-password card (the only SCK widget)
  new_client - creating a client (or a tracker-dump row) from a login that worked
"""

from .attempts import SckAttempt, SckAttempts
from .detector import RotationSave, save_verified_for_rotation
from .watcher import SckFailure, SckWatcher

__all__ = [
    "SckAttempt",
    "SckAttempts",
    "RotationSave",
    "save_verified_for_rotation",
    "SckFailure",
    "SckWatcher",
]
