"""
tools/vsdc_alert_test.py - send one test phone alert
=====================================================
Checks that the phone alert is set up: reads the same configuration the app uses
(vsdc_alert.json next to the program, or the VSDC_ALERT_* environment variables), sends a
clearly labelled TEST message through ntfy, and says whether it went.

The message carries no client data - the same rule as a real alert.

Setup (once):
  1. Install the "ntfy" app on the phone (Android / iOS) and subscribe to a topic name that
     nobody could guess, e.g.  sera-alerts-9f3k2m8q7x1z5w0p   (16-64 letters, digits, - or _).
  2. Save vsdc_alert.json in the project root (or next to the installed program):
         {"topic": "sera-alerts-9f3k2m8q7x1z5w0p"}
     Copy the same file to every PC. The PC name in each alert comes from that PC's own
     device_identity.txt (in the AmanAssociates_Sera data folder).
     The file is git-ignored on purpose: this repository is public, and the topic is a secret.
  3. python tools/vsdc_alert_test.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.vsdc.vsdc_alerts import AlertSender, _config_path, device_name, load_alert_config


def main() -> int:
    cfg = load_alert_config()
    if not cfg:
        print(f"Phone alerts are not configured (looked for {_config_path()} and VSDC_ALERT_TOPIC).")
        print("See the setup steps at the top of this file.")
        return 2
    pc = device_name()
    print(f"Sending a test alert to {cfg['server']} (topic ...{cfg['topic'][-4:]}, this PC: '{pc}')")
    ok = AlertSender(config=cfg, sync=True, sleep=lambda s: None)._deliver(
        f"{cfg['server']}/{cfg['topic']}",
        "Sera: TEST alert",
        f"PC: {pc}\nSent: {datetime.now().strftime('%d-%b %H:%M')}\n\n"
        "This is a test of the Sera phone alert. No client data is included.",
    )
    print("RESULT:", "sent - check your phone" if ok else "FAILED - see the message above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
