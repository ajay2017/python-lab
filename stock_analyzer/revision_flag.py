"""Pre-earnings analyst rating-change flag: the one shared rule and wording.

Owner decision 2026-10-30 (finding #22): the analyst-revision signal is a
WATCH-level flag on every surface. It never sizes a trim; it is shadow-measured
before it may ever size one again.

The data is RATING changes, not earnings-estimate revisions: `bundle["revisions"]`
(built from Yahoo `upgrades_downgrades`) counts upgrades + initiations, downgrades
and `net` = upgrades - downgrades over 90 days. Every sentence here says "rating
changes" and never claims estimates were cut.

Every surface (Catalyst Watch playbook, Brief, Signals) must use this module so
the rule and the wording are identical everywhere. Pure; never raises.
"""
from __future__ import annotations

import math
from numbers import Real

from stock_analyzer import trim_sizing
from stock_analyzer.constants import (
    EARNINGS_HOLD_OR_ADD_MIN_NET_REVISIONS,
    EARNINGS_IMMINENT_DAYS,
    EARNINGS_PARTIAL_TRIM_PCT,
    EARNINGS_REVISION_WATCH_NET,
)

RULE_VERSION = "rev-watch-v1"

_WINDOW_PHRASE = ("the week" if EARNINGS_IMMINENT_DAYS == 7
                  else f"the {EARNINGS_IMMINENT_DAYS} days")
_TAIL = (
    " These are rating changes, not earnings-estimate cuts. Flagged to watch, "
    f"not acted on: in {_WINDOW_PHRASE} before each report the app records whether "
    "this predicts a weak report, and won't size a trim from it unless that "
    "record supports it."
)
_AMBER = "#f59e0b"
_GREY = "#9ca3af"


def _num(v):
    """int of a finite real number, else None (bools rejected)."""
    if isinstance(v, bool) or not isinstance(v, Real):
        return None
    try:
        if not math.isfinite(v):
            return None
        return int(v)
    except Exception:
        return None


def _unknown() -> dict:
    return {"state": "unknown", "net": None, "ups": None, "downs": None,
            "window_days": None}


def read(bundle) -> dict:
    """Read the 90d rating-change counts from a bundle. Never raises."""
    try:
        if not isinstance(bundle, dict):
            return _unknown()
        rev = bundle.get("revisions")
        if not isinstance(rev, dict) or not rev:
            return _unknown()
        if rev.get("basis") == "consensus_snapshot":
            return _unknown()
        net = _num(rev.get("net"))
        if net is None:
            return _unknown()
        return {
            "state": "ok",
            "net": net,
            "ups": _num(rev.get("upgrades_90d")),
            "downs": _num(rev.get("downgrades_90d")),
            # Missing key (cached bundles) = today's producer behaviour, 90d.
            "window_days": _num(rev.get("window_days")) if "window_days" in rev else 90,
        }
    except Exception:
        return _unknown()


def _ok(r) -> bool:
    return isinstance(r, dict) and r.get("state") == "ok" and _num(r.get("net")) is not None


def is_flagged(r) -> bool:
    """True when the reading is ok and net is at or below the watch bound."""
    try:
        return _ok(r) and _num(r["net"]) <= EARNINGS_REVISION_WATCH_NET
    except Exception:
        return False


def _win(r) -> bool:
    """True when the counts cover a 90-day window (missing key = 90)."""
    try:
        return r.get("window_days", 90) == 90
    except Exception:
        return False


def flag_line(r) -> str | None:
    """The Watch-flag sentence, or None when not flagged. Plain text."""
    if not is_flagged(r):
        return None
    net = _num(r["net"])
    ups, downs = _num(r.get("ups")), _num(r.get("downs"))
    span = "the last 90 days" if _win(r) else "recent months"
    if ups is None or downs is None or ups - downs != net:
        head = f"Analyst ratings: net {net:+d} in rating changes over {span}."
    else:
        d = f"{downs} downgrade" + ("" if downs == 1 else "s")
        u = f"{ups} upgrade or initiation" if ups == 1 else f"{ups} upgrades or initiations"
        head = f"Analyst ratings: {d} vs {u} in {span} (net {net:+d})."
    return head + _TAIL


def revisions_phrase(r) -> str:
    """Short phrase for the playbook's HOLD sentence."""
    if not _ok(r):
        return "unavailable"
    net = _num(r["net"])
    if is_flagged(r):
        return f"net {net:+d} (flagged to watch)"
    if EARNINGS_REVISION_WATCH_NET < net < EARNINGS_HOLD_OR_ADD_MIN_NET_REVISIONS:
        return "neutral"
    return f"net {net:+d}"


def checklist_line(r) -> tuple[str, str, str]:
    """(emoji, text, css colour) for the Signals checklist."""
    line = flag_line(r)
    if line is not None:
        return ("⚑", line, _AMBER)
    if _ok(r):
        return ("•", f"Analyst rating changes ({'90d' if _win(r) else 'recent'}): net {_num(r['net']):+d}.", _GREY)
    return ("•", "Analyst rating changes unavailable.", _GREY)


def _fnum(v):
    """float of a finite real number, else None (bools rejected)."""
    if isinstance(v, bool) or not isinstance(v, Real):
        return None
    try:
        f = float(v)
    except Exception:
        return None
    return f if math.isfinite(f) else None


def build_shadow_rows(port_df, held_data, earnings_ctx, today, now_et,
                      source: str = "cron") -> list[dict]:
    """One measure-only row per held name inside the earnings window.

    Flagged, unflagged and unknown names all get a row (the unflagged are the
    control group). Days come from `trim_sizing.days_to_earnings` on
    `held_data[t]["earnings"]`, the same helper the Brief / playbook use.
    `earnings_ctx` is `db.load_earnings_context_batch`'s dict, which is {} for
    both "no context" and "read failed", so ctx_state records
    "none_or_unavailable" / "present". `now_et` is part of the caller's
    signature and unused. Pure; a malformed row is skipped, never raised.
    """
    rows: list[dict] = []
    try:
        if port_df is None or getattr(port_df, "empty", True):
            return rows
        held = held_data if isinstance(held_data, dict) else {}
        ctx_all = earnings_ctx if isinstance(earnings_ctx, dict) else {}
        held_u = {str(k).upper(): v for k, v in held.items()}
        seen: set = set()
        for _, row in port_df.iterrows():
            try:
                tk = str(row["Ticker"]).strip().upper()
                if not tk or tk in seen:
                    continue
                data = held_u.get(tk)
                earn = data.get("earnings") if isinstance(data, dict) else None
                days = trim_sizing.days_to_earnings(earn, today)
                if not trim_sizing.in_earnings_window(days):
                    continue
                r = read(data)   # read() is unknown-safe on a non-dict
                shares = trim_sizing.exact_shares(row)
                comp = _fnum(row.get("Score"))
                _sa = row.get("Score Available")
                if _sa is None:
                    flag_ok = True                 # missing column = available
                elif isinstance(_sa, float) and math.isnan(_sa):
                    flag_ok = False                # NaN flag is not "available"
                else:
                    flag_ok = _sa is not False and str(_sa).lower() not in ("false", "0")
                avail = flag_ok and comp is not None
                comp = comp if avail else None
                ctx = ctx_all.get(tk)
                has_ctx = isinstance(ctx, dict)
                rows.append({
                    "capture_date": today.isoformat(),
                    "ticker": tk,
                    "source": source,
                    "earnings_date": str(earn)[:10],
                    "days_until": days,
                    "rev_state": r["state"],
                    "net_rev": r["net"],
                    "ups_90": r["ups"],
                    "downs_90": r["downs"],
                    "window_days": r["window_days"],
                    "flag_net": EARNINGS_REVISION_WATCH_NET,
                    "flagged": is_flagged(r),
                    "weight_pct": _fnum(row.get("Weight (%)")),
                    "shares": shares,
                    "price": _fnum(row.get("Price")),
                    "composite": comp,
                    "score_available": avail,
                    "beat_rate_pct": _fnum(ctx.get("beat_rate_pct")) if has_ctx else None,
                    "reaction": ctx.get("recent_reaction_direction") if has_ctx else None,
                    "ctx_state": "present" if has_ctx else "none_or_unavailable",
                    "partial_trim_pct": EARNINGS_PARTIAL_TRIM_PCT,
                    "would_trim_shares": trim_sizing.fraction_trim_shares(
                        shares, EARNINGS_PARTIAL_TRIM_PCT),
                    "rule_version": RULE_VERSION,
                })
                seen.add(tk)
            except Exception:
                continue
    except Exception:
        return rows
    return rows
