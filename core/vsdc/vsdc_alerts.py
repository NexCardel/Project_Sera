"""
core/vsdc/vsdc_alerts.py — a phone alert when a capture needs a human
=====================================================================
VSDC247 can save a submission whose client it does not know (the unattributed
"Pending_<ARN>" row). Someone has to open the taxpayer's profile page so it can be
attached. This module pushes one short message to the designer's phone through ntfy
(https://ntfy.sh, or a self-hosted server) so that does not depend on anyone looking at
the HUD.

What leaves the machine is deliberately NOT client data: which machine, which form, when,
and the page path with every identifier-shaped token removed (PAN, GSTIN, ARN, long numbers).
An ntfy topic is only as private as its name, so the message must be safe even if the name
leaks.

Off by default: nothing is sent, and no network is touched, until a topic is configured -
either in `vsdc_alert.json` next to the program (repo root when running from source):

    {"topic": "sera-alerts-<a long random string>"}

or through VSDC_ALERT_TOPIC / VSDC_ALERT_SERVER. The same file can be copied to every PC: which PC
an alert is about comes from that PC's own `device_identity.txt` in the Sera data folder (the file the
app writes on first launch and the staff can rename), never from this JSON. The topic must be at
least 16 characters (letters, digits, - or _), and a custom server must be https.

Sending never blocks or breaks capture: it runs on a background thread, failures are only
logged, and it is rate-limited.
"""

import json
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Optional

ALERT_CONFIG_NAME = "vsdc_alert.json"
ALERT_CONFIG_ENV = "VSDC_ALERT_CONFIG"
DEFAULT_SERVER = "https://ntfy.sh"

_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_SERVER_RE = re.compile(r"^https://[A-Za-z0-9.\-]+(:\d{1,5})?$")

# Anything shaped like an identifier is removed from text that leaves the machine.
_REDACTIONS = (
    (re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b", re.IGNORECASE), "[gstin]"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE), "[pan]"),
    (re.compile(r"\b[A-Z]{2}\d{11,14}[A-Z0-9]?\b", re.IGNORECASE), "[arn]"),
    (re.compile(r"\d{8,}"), "[number]"),
)


def redact_identifiers(text: str) -> str:
    out = text or ""
    for pattern, label in _REDACTIONS:
        out = pattern.sub(label, out)
    return out


SERA_DATA_DIR_NAME = "AmanAssociates_Sera"        # main.py: APP_DIR = Path.home() / this
DEVICE_IDENTITY_NAME = "device_identity.txt"
DEVICE_IDENTITY_ENV = "VSDC_DEVICE_IDENTITY_FILE"


def device_name() -> str:
    """
    This PC's name for the alert: the first line of device_identity.txt in the Sera data folder
    (the same file main.py writes on first launch and reads back as the workstation's identity),
    falling back to the computer's hostname. Cleaned and capped - it ends up in a message body.
    """
    path = Path(os.environ.get(DEVICE_IDENTITY_ENV) or (Path.home() / SERA_DATA_DIR_NAME / DEVICE_IDENTITY_NAME))
    name = ""
    try:
        name = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        name = ""
    name = re.sub(r"[^\w .\-]", "", name).strip()[:40]
    return name or re.sub(r"[^\w .\-]", "", socket.gethostname()).strip()[:40] or "unknown PC"


def stamp_device_name(payload):
    """
    Records which PC produced a capture inside its JSON payload: "device_name" at the top level
    and inside "raw_payload" (the envelope the tracker stores). An existing value is never
    overwritten, so a payload that already says where it came from keeps saying it. Returns the
    payload (unchanged if it is not a dict) so callers can use it inline.
    """
    if not isinstance(payload, dict):
        return payload
    name = device_name()
    payload.setdefault("device_name", name)
    raw = payload.get("raw_payload")
    if isinstance(raw, dict):
        raw.setdefault("device_name", name)
    return payload


def _config_path() -> Path:
    override = os.environ.get(ALERT_CONFIG_ENV)
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / ALERT_CONFIG_NAME
    return Path(__file__).resolve().parents[2] / ALERT_CONFIG_NAME


def load_alert_config(path: Optional[Path] = None) -> Dict[str, str]:
    """
    {"topic", "server"} when alerts are configured and valid, otherwise {}.
    Environment variables win over the file. An invalid setting disables alerts with a console
    line rather than sending somewhere unintended.
    """
    data: Dict[str, Any] = {}
    p = path or _config_path()
    try:
        if p.is_file():
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
            else:
                print(f"[VSDC Alert] {p.name} must be a JSON object - alerts stay off")
    except Exception as e:
        print(f"[VSDC Alert] could not read {p.name}: {e} - alerts stay off")
        return {}

    topic = str(os.environ.get("VSDC_ALERT_TOPIC") or data.get("topic") or "").strip()
    if not topic:
        return {}
    if not _TOPIC_RE.match(topic):
        print("[VSDC Alert] topic must be 16-64 letters, digits, - or _ (a short topic is guessable) - alerts stay off")
        return {}
    server = str(os.environ.get("VSDC_ALERT_SERVER") or data.get("server") or DEFAULT_SERVER).strip().rstrip("/")
    if not _SERVER_RE.match(server):
        print("[VSDC Alert] server must be an https:// address - alerts stay off")
        return {}
    if "machine" in data:
        print(f"[VSDC Alert] 'machine' in {p.name} is ignored - the PC name comes from {DEVICE_IDENTITY_NAME}")
    return {"topic": topic, "server": server}


def _default_post(url: str, title: str, body: str, priority: str, tags: str, timeout: float = 8.0) -> None:
    req = urllib.request.Request(
        url, data=body.encode("utf-8"), method="POST",
        headers={"Title": title, "Priority": priority, "Tags": tags, "Content-Type": "text/plain; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 - https only, validated above
        resp.read(64)


class AlertSender:
    """One per router. Cheap to construct; does nothing at all when alerts are not configured."""

    RETRY_DELAY_SEC = 20.0
    MAX_PER_HOUR = 20
    RELOAD_INTERVAL_SEC = 15.0      # how often an app started WITHOUT a config looks for one

    def __init__(self, config: Optional[Dict[str, str]] = None,
                 post: Optional[Callable[..., None]] = None,
                 sync: bool = False,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._reads_config_file = config is None
        self.config = load_alert_config() if config is None else dict(config)
        self._last_reload = time.time()
        self._warned_off_at = 0.0
        self._post = post or _default_post
        self._sync = sync
        self._sleep = sleep
        self._sent: Deque[float] = deque()
        self._warned_limit = False
        if self.config:
            t = self.config["topic"]
            print(f"[VSDC Alert] phone alerts ON via {self.config['server']} (topic ...{t[-4:]}, this PC: '{self._machine()}')")

    def _machine(self) -> str:
        return self.config.get("machine") or device_name()      # "machine" only ever set explicitly, by tests

    @property
    def enabled(self) -> bool:
        return bool(self.config)

    def notify_unattributed_submission(self, form: Optional[str], page_url: Optional[str],
                                       when: Optional[datetime] = None) -> bool:
        """
        A submission was captured but no client is known. True when an alert was queued.
        The text carries no client data (see the module docstring).
        """
        now = time.time()
        if not self.enabled and self._reads_config_file and now - self._last_reload >= self.RELOAD_INTERVAL_SEC:
            # The app may have been started before the config file existed (or before it was
            # fixed). Pick it up now rather than staying silent until the next restart.
            self._last_reload = now
            cfg = load_alert_config()
            if cfg:
                self.config = cfg
                print(f"[VSDC Alert] phone alerts ON via {cfg['server']} (topic ...{cfg['topic'][-4:]}, "
                      f"this PC: '{self._machine()}') - config found after start-up")
        if not self.enabled:
            if now - self._warned_off_at > 600:
                self._warned_off_at = now
                print(f"[VSDC Alert] a submission with no client was captured, but phone alerts are not configured "
                      f"({_config_path()} / VSDC_ALERT_TOPIC) - no alert sent")
            return False
        while self._sent and now - self._sent[0] > 3600:
            self._sent.popleft()
        if len(self._sent) >= self.MAX_PER_HOUR:
            if not self._warned_limit:
                self._warned_limit = True
                print(f"[VSDC Alert] {self.MAX_PER_HOUR} alerts in the last hour - holding further ones back")
            return False
        self._sent.append(now)
        self._warned_limit = False

        when = when or datetime.now()
        page = redact_identifiers(page_url or "") or "(address bar not readable)"
        body = "\n".join((
            f"PC: {self._machine()}",
            f"Form: {redact_identifiers(form or 'unknown')}",
            f"Seen: {when.strftime('%d-%b %H:%M')}",
            f"Page: {page}",
            "",
            "A submission was captured but no client is attached. Open the client's profile "
            "page on that machine so it can be linked.",
        ))
        print(f"[VSDC Alert] submission with no client captured - alerting ({self._machine()})")
        title = "Sera: submission captured, client unknown"
        url = f"{self.config['server']}/{self.config['topic']}"
        if self._sync:
            self._deliver(url, title, body)
        else:
            threading.Thread(target=self._deliver, args=(url, title, body), daemon=True, name="vsdc-alert").start()
        return True

    def _deliver(self, url: str, title: str, body: str) -> bool:
        for attempt in (1, 2):
            try:
                self._post(url, title, body, "high", "warning")
                print("[VSDC Alert] sent")
                return True
            except Exception as e:
                print(f"[VSDC Alert] send failed ({type(e).__name__}: {e})" + (" - retrying" if attempt == 1 else " - giving up"))
                if attempt == 1:
                    self._sleep(self.RETRY_DELAY_SEC)
        return False
