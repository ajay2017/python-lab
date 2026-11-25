"""Gate module: "did a buy check actually run?" states for the entry checks
(docs/plans/entry-fail-closed.md §2; chunk C1).

A buy check that couldn't run must not read as a check that passed. This module
holds the pure state readers the entry surfaces will consult. Pure: no clock, no
streamlit, no db; inputs are never mutated; nothing here raises.

C1 (this chunk) ships ONLY `earnings_state`. Nothing reads it for a decision yet
(C3 wires `entry_eligibility.check_earnings`); the cron scan log counts it so the
real failure rate is visible first. `fit_state`, `beta_leg_could_bind`,
`tone_unread` and the banner text builders arrive in C2a / C4.

  earnings_state(bundle) -> {"state": "not_applicable" | "date" | "no_date" | "failed",
                             "date": "YYYY-MM-DD" | None}

THE FOUR STATES (the U2 owner decision, 2026-11-25):
  not_applicable  an ETF / fund (`asset_type` == the app's ETF constant): earnings
                  don't apply, even with a stray date on the bundle.
  date            a parseable earnings date is present (`str(x)[:10]` parsed as
                  %Y-%m-%d, the same read the app's surfaces use).
  no_date         the lookup succeeded and Yahoo announced no date (provider status
                  "no_date", e.g. a fresh IPO): passes.
  failed          the lookup failed, couldn't be told, or the bundle is a stale
                  last-known-good copy: a buy check that did not run.

Rule order: not a dict -> failed; ETF -> not_applicable; stale cached bundle
(`stale_as_of` set by `bundle_loader.load_bundle` on the Supabase-cache path) ->
failed, even if it carries a date; a truthy `earnings` value -> `date` when it
parses, else failed (a value we can't read is not "nothing announced"); provider
status "no_date" -> no_date; everything else (status "failed", no status key at all,
an unknown status, a falsy date with no status) -> failed. An absent
`earnings_status` key is "couldn't tell"; that is safe because `load_bundle` is the
only production bundle producer and always stamps the key (a test pins it).
"""
from __future__ import annotations

from datetime import datetime

from stock_analyzer import asset_type

STATE_NOT_APPLICABLE = "not_applicable"
STATE_DATE = "date"
STATE_NO_DATE = "no_date"
STATE_FAILED = "failed"

_FAILED = {"state": STATE_FAILED, "date": None}


def _parse_date(raw) -> "str | None":
    """'YYYY-MM-DD' for a date-like value (a timestamped string is cut to its
    first 10 characters), else None. Never raises."""
    try:
        s = str(raw)[:10]
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except Exception:
        return None


def earnings_state(bundle) -> dict:
    """The earnings-read state of one loaded bundle (see the module docstring).
    Never raises: any bad input reads as `failed`."""
    try:
        if not isinstance(bundle, dict):
            return dict(_FAILED)
        if bundle.get("asset_type") == asset_type.ASSET_TYPE_ETF:
            return {"state": STATE_NOT_APPLICABLE, "date": None}
        if bundle.get("stale_as_of") is not None:
            return dict(_FAILED)
        raw = bundle.get("earnings")
        if raw:
            d = _parse_date(raw)
            if d is not None:
                return {"state": STATE_DATE, "date": d}
            return dict(_FAILED)
        if bundle.get("earnings_status") == STATE_NO_DATE:
            return {"state": STATE_NO_DATE, "date": None}
        return dict(_FAILED)
    except Exception:
        return dict(_FAILED)
