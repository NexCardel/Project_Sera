"""
sera_db/common.py - names shared by database.py and its mixins.
"""

import re

DB_FILENAME = "master.db"

SKELETON_NAME_REGEX = re.compile(
    r'^(?:'
    r'taxpayer|client|user|individual|indicates\s*mandatory\s*fields|mandatory\s*fields|'
    r'goods\s+and\s+services\s+tax|gst\s+common\s+portal|gst\s+portal|status|due\s*date|'
    r'fy|financial\s*year|tax\s*period|return\s*period|filing\s*period|legal\s*name|'
    r'trade\s*name|gstin|pan|na|none|-+|sera(?:\s*assist)?|scc|'
    r'(?:client|taxpayer|user|unregistered|unassigned)?\s*[:\-\#]?\s*(?:\(?\s*[A-Z]{5}[0-9]{4}[A-Z]\s*\)?|\d+|cli-\d+)|'
    r'pan\s*:\s*[A-Z]{5}[0-9]{4}[A-Z].*'
    r')[\s\-:*]*$',
    re.I
)


class DatabaseError(Exception):
    pass
