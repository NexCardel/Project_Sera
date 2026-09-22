"""
test_dump_injection.py
-----------------------
Manual dev tool: sends a mock extension capture to Project Sera's WebSocket bridge
(ui/ws_bridge.py), the same way the real Chrome extension would. Not an automated test - it
just prints whether delivery worked; run it by hand with the app open.

Presents the Chrome extension's own Origin, computed from sera_extension/manifest.json's key
(see ui.ws_bridge._chrome_extension_id), so the bridge accepts the connection exactly as it
would from the real extension.

Usage:
  python tests/test_dump_injection.py [client_id] [portal_name]
Example:
  python tests/test_dump_injection.py 432 "Income Tax Portal"
"""

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def inject():
    from PySide6.QtCore import QCoreApplication, QUrl, QTimer
    from PySide6.QtNetwork import QHostAddress, QNetworkRequest
    from PySide6.QtWebSockets import QWebSocket

    from ui.ws_bridge import WS_PORTS, _load_chrome_extension_id

    target_client_id = 1
    portal_name = "GST Portal"
    if len(sys.argv) > 1:
        try:
            target_client_id = int(sys.argv[1])
        except ValueError:
            portal_name = sys.argv[1]
    if len(sys.argv) > 2:
        portal_name = sys.argv[2]

    chrome_id = _load_chrome_extension_id(ROOT / "sera_extension" / "manifest.json")
    if not chrome_id:
        print("[Test Inject] FAILED: could not derive a Chrome extension id from "
              "sera_extension/manifest.json.")
        return

    arn = f"AA270826{random.randint(100000000, 900000000)}"
    payload = {
        "_id": "diag-inject-" + str(random.randint(1000,9999)),
        "type": "filing_result",
        "client_id": target_client_id,
        "portal": portal_name,
        "arn": arn,
        "capture_method": "Extension_Capture",
        "period_label": "July 2026",
        "raw_payload": {
            "status_cd": "1",
            "arn": arn,
            "message": f"Test {portal_name} Extension Capture",
        },
    }

    app = QCoreApplication(sys.argv)
    result = {"sent": False}

    def try_port(index=0):
        if index >= len(WS_PORTS):
            print(f"[Test Inject] FAILED: no WebSocket bridge answered on {WS_PORTS}. "
                  f"Is Project Sera running?")
            app.quit()
            return
        port = WS_PORTS[index]
        client = QWebSocket()
        req = QNetworkRequest(QUrl(f"ws://127.0.0.1:{port}/"))
        req.setRawHeader(b"Origin", f"chrome-extension://{chrome_id}".encode("ascii"))

        def on_open():
            client.sendTextMessage(json.dumps(payload))

        def on_message(text):
            print(f"[Test Inject] SUCCESS on port {port}! Bridge replied: {text}")
            result["sent"] = True
            client.close()
            app.quit()

        def on_error(*_):
            client.close()
            QTimer.singleShot(50, lambda: try_port(index + 1))

        client.connected.connect(on_open)
        client.textMessageReceived.connect(on_message)
        client.errorOccurred.connect(on_error)
        client.open(req)

    print(f"[Test Inject] Sending extension capture for Client #{target_client_id} "
          f"({portal_name}) as chrome-extension://{chrome_id} ...")
    QTimer.singleShot(0, try_port)
    QTimer.singleShot(5000, app.quit)
    app.exec()
    if not result["sent"]:
        print("[Test Inject] FAILED: no reply within 5 seconds.")


if __name__ == "__main__":
    inject()
