"""
core/vsdc/vsdc_session_logger.py — Human-Readable Session Capture Logger
========================================================================
Records the visual capture lifecycle into dedicated text files under
'Vsdc_Captures/' for live inspection, auditability, and debugging.

File Naming Standard:
  Vsdc_Captures/<Client Name> (<PAN>).txt
  Example: Vsdc_Captures/ARIF MOHAMMAD MOLLA (CJLPM0265M).txt

Lifecycle Flow:
  SESSION START -> MILESTONES (Routes, Form Selections, Captures, ARNs) -> SESSION END

Privacy Protection:
  Strictly compliant with client data protection rules. Only logs statutory
  metadata (PAN, Name, GSTIN, Form Type, Period/AY, Status, ARN, timestamps).
  NEVER logs personal identifiers (email, mobile, bank, password, etc.).
"""

import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def sanitize_filename(name: str) -> str:
    """Removes or replaces characters forbidden in Windows filenames."""
    if not name:
        return "UNKNOWN"
    # Replace characters forbidden in Windows: < > : " / \ | ? *
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name.strip())
    # Strip trailing spaces or dots (forbidden on Windows)
    cleaned = cleaned.rstrip('. ')
    return cleaned or "UNKNOWN"


class VSDCSessionLogger:
    """
    Dummy session logger (feature removed by user request).
    """

    def __init__(self, output_dir: Optional[str] = None):
        pass

    def start_session(self, *args, **kwargs):
        pass

    def update_identity(self, *args, **kwargs):
        pass

    def log_route_transition(self, *args, **kwargs):
        pass

    def log_milestone(self, *args, **kwargs):
        pass

    def end_session(self, *args, **kwargs):
        pass
