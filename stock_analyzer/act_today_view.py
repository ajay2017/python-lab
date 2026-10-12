"""
Single source of truth for "how many Act Today items are there right now" —
every render surface (🏠 Home's subheader badge / Today's Actions chip /
section header/cards, 🧾 Summary's pill / bucket chips) must read this one
function instead of computing its own answer.

Two bugs this closes (2026-10-06, planner-designed):

1. A crashed Daily Brief build falls back to a minimal
   ``{"act_today": [], ...}`` dict (never ``None``), so a naive
   ``len(act_today) == 0`` check reads as a calm, checked "nothing to act on"
   — a false all-clear. Act Today was never actually evaluated that run.
   ``act_today_view`` makes that distinction explicit via a three-state
   ``state`` field (``"offline"`` vs ``"clear"`` vs ``"act"``) and an
   ``n_active`` that is ``None`` (never ``0``) whenever the Brief wasn't
   built — ``0`` means "checked, nothing active"; ``None`` means "not
   checked at all".

2. Home and Summary previously counted "Act Today" differently: Home
   demotes a stop-breach card out of the active count once live price has
   recovered back above the stop (``stock_analyzer.util.stop_recovery_state``,
   compared against ``STOP_RECOVERY_MARGIN_PCT``), while Summary counted the
   raw ``decision_bucket.split_defensive(...)["act"]`` length directly,
   never demoting. Per owner decision, every surface now unifies on Home's
   stricter, post-demotion definition — Summary's count may now drop on a day
   with a recovered breach, and that is intended.

Pure logic — no Streamlit / no I/O. ``app.py`` must contain no direct call to
``decision_bucket.split_defensive`` outside this module (mechanically tested
in ``tests/test_act_today_view.py``); every count surface routes through here
instead, so the two pages can no longer drift apart.
"""

import pandas as pd

from stock_analyzer import decision_bucket
from stock_analyzer.util import stop_recovery_state


# ── The offline Brief stub, made identifiable (2026-10-11 audit B-H2) ──────
#
# Bug 1 above was closed by reading the separate ``_daily_brief_offline``
# session FLAG. The stub dict itself carried nothing that said "I am offline",
# so any path that served it while that flag read False turned it straight
# back into a false all-clear. Brief Lock was such a path: the Lock button was
# disabled only when the brief was ``None`` -- but the crash path has already
# swapped ``None`` for this stub by then, so Lock was clickable on an offline
# Brief. The next render served the locked stub, took the success branch, set
# the flag False, and Home's pointer read "Nothing to act on today" with every
# protective veto gone for the rest of the trading day.
#
# The fix is layered so no single check is load-bearing:
#   1. the stub carries an explicit marker (``offline_brief_stub``);
#   2. ``act_today_view`` treats a marked brief as offline EVEN IF the flag
#      says otherwise -- so no path, present or future, can make it read clear;
#   3. the Lock button and both snapshot-serving sites refuse a marked brief,
#      closing the laundering path itself.
#
# A snapshot locked BEFORE this fix carries no marker, but none can reach a
# post-fix render: lock state lives in ``st.session_state`` and every Railway
# redeploy starts a fresh process, so the deploy that ships this wipes it.
OFFLINE_BRIEF_MARKER = "_brief_offline_stub"


def offline_brief_stub() -> dict:
    """The minimal Brief served when ``build_daily_briefing()`` raised.

    Same keys downstream code reads (so nothing crashes on a missing key),
    plus the marker. The single place this shape is built -- ``app.py`` must
    not construct the stub inline (pinned in tests/test_act_today_view.py).
    """
    return {
        "act_today": [],
        "buy_candidates": [],
        "review_list": [],
        "grow_today": {},
        OFFLINE_BRIEF_MARKER: True,
    }


def is_offline_brief(brief) -> bool:
    """True only for a brief built by ``offline_brief_stub``.

    Compared with ``is True``, not truthiness, so a real brief that happened
    to carry some unrelated value under this key cannot read as offline.
    """
    return isinstance(brief, dict) and brief.get(OFFLINE_BRIEF_MARKER) is True


def brief_lock_allowed(brief, brief_offline) -> bool:
    """May this render's Brief be frozen by Brief Lock?

    Only a genuinely built Brief. ``brief_offline`` must be EXPLICITLY False:
    ``None`` means the offline state was never established this render, and
    the safe answer to "freeze this for the whole day?" is no. Both the flag
    and the marker are checked, so either alone is enough to refuse.
    """
    return (
        brief is not None
        and brief_offline is False
        and not is_offline_brief(brief)
    )


def locked_snapshot_servable(snapshot) -> bool:
    """May a stored Brief Lock snapshot be served in place of a fresh build?

    Refuses a marked offline stub, so a stub can never be re-laundered as a
    real Brief -- by the Lock serve path or by Evening Debrief's AM baseline.

    Requires a NON-EMPTY dict. The two callers this replaced disagreed: the
    Lock serve path tested ``is not None`` (accepting ``{}``) while Evening
    Debrief tested truthiness (rejecting it). A shared helper takes the
    stricter of the two, so neither caller loosens.
    """
    return (
        isinstance(snapshot, dict)
        and bool(snapshot)
        and not is_offline_brief(snapshot)
    )


def act_today_view(
    daily_brief: dict | None,
    brief_offline: bool,
    port_df_enriched,
    live_prices: dict | None,
    margin_pct: float,
) -> dict:
    """Return the single canonical Act Today read for this render.

    Parameters
    ----------
    daily_brief : the Brief dict (``{"act_today": [...], "review_list": [...],
        ...}``) or ``None``.
    brief_offline : the ``_daily_brief_offline`` session flag — True whenever
        the most recent ``build_daily_briefing()`` call raised.
    port_df_enriched : the live-price-rebuilt portfolio DataFrame (
        ``st.session_state["_port_df_enriched"]``), or ``None``/empty.
    live_prices : ``st.session_state["_live_prices"]`` map, or ``None``.
    margin_pct : the stop-recovery buffer (``STOP_RECOVERY_MARGIN_PCT`` from
        ``constants.py`` — caller imports and passes it; this module never
        hardcodes a threshold).

    Returns
    -------
    dict with keys:
      state     : "offline" | "clear" | "act"
      active    : list of items still counted as Act Today (post-demotion)
      resolved  : stop_breach items demoted because live price has recovered
      aware     : the Monitoring / Awareness bucket (unaffected by demotion)
      n_active  : int, or None when state == "offline" (NOT checked this run
                  — never 0, which would mean "checked, nothing active")
    """
    # is_offline_brief: the B-H2 backstop. A marked stub is offline even when
    # the flag reads False -- the flag is exactly what a laundering path flips.
    if brief_offline or daily_brief is None or is_offline_brief(daily_brief):
        return {
            "state": "offline",
            "active": [],
            "resolved": [],
            "aware": [],
            "n_active": None,
        }

    split = decision_bucket.split_defensive(
        daily_brief.get("act_today"), daily_brief.get("review_list")
    )

    lp = live_prices if isinstance(live_prices, dict) else {}
    pe = port_df_enriched

    active: list[dict] = []
    resolved: list[dict] = []
    for item in split["act"]:
        if item.get("kind") == "stop_breach":
            ticker = str(item.get("ticker", "")).upper()
            gap: float | None = None
            stop_px: float | None = None
            if pe is not None and not pe.empty:
                if "Gap to Stop (%)" in pe.columns:
                    row = pe[pe["Ticker"].astype(str).str.upper() == ticker]
                    if not row.empty:
                        g = row["Gap to Stop (%)"].iloc[0]
                        gap = float(g) if pd.notna(g) else None
                        if "Stop" in row.columns:
                            s = row["Stop"].iloc[0]
                            stop_px = float(s) if pd.notna(s) else None
            state = stop_recovery_state(gap, margin_pct)
            lp_entry = lp.get(ticker)
            item_copy = dict(item)
            item_copy["_breach_state"] = state
            item_copy["_live_gap"] = gap
            item_copy["_live_px"] = lp_entry.get("price") if isinstance(lp_entry, dict) else None
            item_copy["_live_stop"] = stop_px
            if state == "recovered":
                resolved.append(item_copy)
            else:
                active.append(item_copy)
        else:
            active.append(item)

    n_active = len(active)
    return {
        "state": "act" if n_active > 0 else "clear",
        "active": active,
        "resolved": resolved,
        "aware": split["aware"],
        "n_active": n_active,
    }
