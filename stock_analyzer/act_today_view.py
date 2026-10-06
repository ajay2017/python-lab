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
    if brief_offline or daily_brief is None:
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
