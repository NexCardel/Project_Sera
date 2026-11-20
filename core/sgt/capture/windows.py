"""Moved to core/capture/windows.py (UCW WP1). This name is the same module object, so old imports and
monkeypatches keep working."""
import sys

from core.capture import windows as _moved

sys.modules[__name__] = _moved
