"""
masking.py
----------
Turns a real password into a display-safe masked string.
"""

MODE_LAST_N = "last_n"
MODE_FIRST_N = "first_n"
MODE_FULL_DOTS = "full_dots"

DEFAULT_MODE = MODE_LAST_N
DEFAULT_REVEAL_COUNT = 4

def mask_password(password: str, mode: str = DEFAULT_MODE, reveal_count: int = DEFAULT_REVEAL_COUNT) -> str:
    if not password:
        return ""

    reveal_count = max(0, min(reveal_count, len(password) - 1)) if len(password) > 1 else 0
    hidden_len = max(len(password) - reveal_count, 3)

    if mode == MODE_FULL_DOTS:
        return "*" * max(len(password), 8)

    if mode == MODE_FIRST_N:
        visible = password[:reveal_count]
        return f"{visible}{'*' * hidden_len}"

    visible = password[-reveal_count:] if reveal_count else ""
    return f"{'*' * hidden_len}{visible}"