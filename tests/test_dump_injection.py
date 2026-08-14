"""
test_dump_injection.py
-----------------------
Quick test script to send a mock SAD API Interceptor result to Project Sera's
ExtensionListener on TCP port 49152.

Usage:
  python tests/test_dump_injection.py [client_id] [portal_name]
Example:
  python tests/test_dump_injection.py 432 "Income Tax Portal"
"""

import sys
import socket
import json
import random
import time

def test_inject():
    # Allow specifying client_id via CLI argument (e.g. python tests/test_dump_injection.py 5)
    target_client_id = 1
    portal_name = "GST Portal"

    if len(sys.argv) > 1:
        try:
            target_client_id = int(sys.argv[1])
        except ValueError:
            portal_name = sys.argv[1]

    if len(sys.argv) > 2:
        portal_name = sys.argv[2]

    arn = f"AA270826{random.randint(100000000, 900000000)}"
    payload = {
        "type": "filing_result",
        "client_id": target_client_id,
        "portal": portal_name,
        "arn": arn,
        "capture_method": "SAD_API_Interceptor",
        "period_label": "July 2026",
        "raw_payload": {
            "status_cd": "1",
            "arn": arn,
            "message": f"Test {portal_name} SAD Interception"
        }
    }

    print(f"[Test Inject] Sending SAD capture for Client #{target_client_id} ({portal_name}), ARN: {arn} to TCP port 49152...")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(('127.0.0.1', 49152))
            s.sendall(json.dumps(payload).encode('utf-8'))
            print(f"[Test Inject] SUCCESS! Delivered to ExtensionListener for Client #{target_client_id}.")
    except Exception as e:
        print(f"[Test Inject] FAILED: Could not connect to desktop app port 49152 ({e}). Is Project Sera running?")

if __name__ == '__main__':
    test_inject()
