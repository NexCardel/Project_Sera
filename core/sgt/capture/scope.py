"""Moved to core/capture/scope.py (UCW WP1). This name is the same module object, so old imports and
monkeypatches keep working."""
import sys

from core.capture import scope as _moved

sys.modules[__name__] = _moved
