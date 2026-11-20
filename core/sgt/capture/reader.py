"""Moved to core/capture/reader.py (UCW WP1). This name is the same module object, so old imports and
monkeypatches keep working."""
import sys

from core.capture import reader as _moved

sys.modules[__name__] = _moved
