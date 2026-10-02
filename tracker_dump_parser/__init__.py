"""
Sera Tracker Dump Parser & Action Decoder Pipeline
Main package entry point integrating Stages A through H.
"""

import os
from typing import Dict, Any, List, Optional

from tracker_dump_parser.entry_splitter import split_entries
from tracker_dump_parser.header_parser import parse_header
from tracker_dump_parser.json_parser import parse_json_body
from tracker_dump_parser.identity_resolver import (
    resolve_identity,
    extract_identity_evidence,
    extract_session_token,
    resolve_context_identities,
)
from tracker_dump_parser.action_decoder import decode_action
from tracker_dump_parser.name_resolver import extract_name_evidence, choose_client_name
from tracker_dump_parser.mcl_enricher import build_mcl_updates
