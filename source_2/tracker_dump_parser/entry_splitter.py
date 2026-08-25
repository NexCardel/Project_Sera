"""
Stage A: Entry Splitter
Splits raw dump text into individual entry blocks delimited by rule lines.
"""

import re
from typing import List

# Matches the start of an entry header block:
# ========================================================================================
# CAPTURE DUMP ENTRY #<n>
ENTRY_START_RE = re.compile(r"(?:^|\n)(?=={10,}\s*\r?\n(?:CAPTURE DUMP )?ENTRY #\d+)", re.IGNORECASE)
FALLBACK_START_RE = re.compile(r"(?:^|\n)(?=(?:CAPTURE DUMP )?ENTRY #\d+)", re.IGNORECASE)


def split_entries(raw_text: str) -> List[str]:
    """
    Splits raw dump text into individual entry chunks.
    Normalizes line endings and ensures each chunk contains its full header and JSON body.
    """
    if not raw_text:
        return []

    text = raw_text.replace("\r\n", "\n")
    
    # Try splitting by major entry block boundary
    chunks = ENTRY_START_RE.split(text)
    if len(chunks) <= 1:
        chunks = FALLBACK_START_RE.split(text)

    entries = []
    for c in chunks:
        c_clean = c.strip()
        if not c_clean:
            continue
        if re.search(r"(?:CAPTURE DUMP )?ENTRY #\d+", c_clean, re.IGNORECASE):
            entries.append(c_clean)

    return entries
