"""External dead-man's-switch ping (healthchecks.io) for the Railway cron lanes.

`cron_runner._notify_failure` can only email from inside a run that started.
If Railway never starts a lane, a top-level import in cron_runner.py fails, or
Resend itself is dead, every lane goes silent. Each lane therefore pings
healthchecks.io at its end; the service emails the owner when an expected ping
does not arrive. OBSERVABILITY ONLY: nothing reads this for a decision. Inert
until the HEALTHCHECKS_PING_KEY env var is set. The key is a secret: it is
never logged, printed or returned.
"""
from __future__ import annotations

import os
import re

import requests

from stock_analyzer.providers._util import _redact_url

ENV_KEY = "HEALTHCHECKS_PING_KEY"
PING_BASE = "https://hc-ping.com"
SLUG_PREFIX = "drishta-"
BODY_MAX_CHARS = 1000
DEFAULT_TIMEOUT_S = 5.0

_LANE_RE = re.compile(r"^[a-z0-9-]+$")
# Only the 7 scheduled Railway lanes may auto-create a check. A manual run of
# another mode (ALERT_RUN_MODE=debrief / monthly, or premarket run by hand
# after noon ET, which resolves to "eod" -- itself scheduled) would otherwise
# create a check that then goes "down" a day later and emails.
SCHEDULED_LANES = frozenset(
    {"premarket", "scan", "intraday", "eod", "thesis", "maintenance", "broker"})


def ping_url(key, lane, status) -> str | None:
    """URL for one lane ping, or None when the key or lane is unusable.
    Only status "ok" reports success; any other value (incl. None) is /fail."""
    if not isinstance(key, str) or not key.strip():
        return None
    if not isinstance(lane, str):
        return None
    lane_s = lane.strip().lower()
    if not _LANE_RE.match(lane_s):
        return None
    base = f"{PING_BASE}/{key.strip()}/{SLUG_PREFIX}{lane_s}"
    if status != "ok":
        base += "/fail"
    return f"{base}?create=1" if lane_s in SCHEDULED_LANES else base


def ping(lane, status, detail=None, *, timeout=DEFAULT_TIMEOUT_S) -> str:
    """Send one ping. Returns "off" | "sent" | "failed". Never raises."""
    try:
        url = ping_url(os.environ.get(ENV_KEY), lane, status)
        if url is None:
            return "off"
        key = (os.environ.get(ENV_KEY) or "").strip()
        text = str(detail or "").replace(key, "[redacted]")
        body = _redact_url(text)[:BODY_MAX_CHARS].encode("utf-8")
        resp = requests.post(url, data=body, timeout=timeout)
        return "sent" if 200 <= resp.status_code < 300 else "failed"
    except Exception:  # noqa: BLE001 - observability only; never leak the URL
        return "failed"
