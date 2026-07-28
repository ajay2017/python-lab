"""
Account-level pure calculations (account-baseline v2 — contributions & growth).

Pure functions (no I/O, no Streamlit) so they're trivially testable and shared
by the app + any future broker-sync path. The flow ledger separates external
CONTRIBUTIONS (deposits/withdrawals + an opening baseline) from PERFORMANCE, so
"growth" means money the market made you — not money you deposited.

A flow row is {flow_date, flow_type, amount, note}:
  - 'baseline'   : the contributed-capital anchor at the start of tracking (+).
  - 'deposit'    : external cash IN (+).
  - 'withdrawal' : external cash OUT (-).
`amount` is always stored POSITIVE; the type carries the sign.
"""

from __future__ import annotations

from datetime import date as _date, datetime as _datetime

_CONTRIB_TYPES = ("baseline", "deposit")
_WITHDRAW_TYPES = ("withdrawal",)


def _parse_date(d):
    """Coerce a date / datetime / 'YYYY-MM-DD' string to a date; None if unparseable."""
    if isinstance(d, _datetime):
        return d.date()
    if isinstance(d, _date):
        return d
    try:
        return _date.fromisoformat(str(d)[:10])
    except Exception:
        return None


def net_contributed_capital(flows: list[dict]) -> float:
    """Net contributed capital = baseline + Σ deposits − Σ withdrawals.

    The amount the user has *put in* (net of what they took out). Growth is
    measured against THIS, not against a naive prior value — so a deposit can
    never masquerade as a gain. Unknown/blank types are ignored (never guessed)."""
    ncc = 0.0
    for f in flows or []:
        try:
            amt = float(f.get("amount") or 0.0)
        except (TypeError, ValueError):
            continue
        t = str(f.get("flow_type") or "").strip().lower()
        if t in _CONTRIB_TYPES:
            ncc += amt
        elif t in _WITHDRAW_TYPES:
            ncc -= amt
    return round(ncc, 2)


def account_growth(total_value: float | None, ncc: float) -> dict:
    """Growth of the account vs net contributed capital.

    Returns {"ncc", "growth", "growth_pct"}. growth = total_value − ncc (the
    performance dollars). growth_pct is None when ncc <= 0 (can't take a return
    on zero/negative contributed capital — surfaced as "—" rather than a bogus
    number) or when total_value is unknown (portfolio not loaded)."""
    ncc = round(float(ncc), 2)
    if total_value is None:
        return {"ncc": ncc, "growth": None, "growth_pct": None}
    growth = round(float(total_value) - ncc, 2)
    growth_pct = round(growth / ncc * 100, 2) if ncc > 0 else None
    return {"ncc": ncc, "growth": growth, "growth_pct": growth_pct}


def has_baseline(flows: list[dict]) -> bool:
    """True once a 'baseline' anchor exists — growth is meaningless before it
    (there's no contributed-capital reference to measure against)."""
    return any(str(f.get("flow_type") or "").strip().lower() == "baseline"
               for f in (flows or []))


def baseline_anchor(flows: list[dict]) -> dict | None:
    """The opening baseline as {"value", "date"} (the contributed-capital anchor),
    or None if not set. The EARLIEST baseline row wins if several exist."""
    cands = [f for f in (flows or [])
             if str(f.get("flow_type") or "").strip().lower() == "baseline"]
    if not cands:
        return None
    cands.sort(key=lambda f: str(f.get("flow_date") or ""))
    b = cands[0]
    return {"value": float(b.get("amount") or 0.0), "date": b.get("flow_date")}


def money_weighted_return(baseline_value, baseline_date, ending_value, ending_date,
                          flows: list[dict], *, annualize_min_days: int = 30) -> dict | None:
    """Modified Dietz money-weighted return over [baseline_date, ending_date].

    Why money-weighted (Modified Dietz), not daily TWR: a true time-weighted return
    needs the TOTAL account value at each sub-period boundary; we have daily EQUITY
    (snapshots) but no daily CASH history, so TWR isn't reliably computable. Modified
    Dietz needs only endpoints + dated flows — exactly what we have — and still
    corrects for deposit/withdrawal TIMING (the distortion v2's naive gain/NCC ignores).

      R = (EMV − BMV − F) / (BMV + Σ Fᵢ·wᵢ),  wᵢ = (end − flowᵢ) / (end − start)

    `flows` are deposit/withdrawal rows ONLY (baseline is BMV, not a flow — exclude it;
    this fn ignores any 'baseline' row defensively). Returns {days, net_flow, gain,
    period_return_pct, annualized_pct (None until the period ≥ annualize_min_days —
    annualizing a few days is meaningless)} or None when not computable (missing
    inputs, bad dates, end before start, or a non-positive denominator)."""
    d0 = _parse_date(baseline_date)
    d1 = _parse_date(ending_date)
    if d0 is None or d1 is None or baseline_value is None or ending_value is None:
        return None
    days = (d1 - d0).days
    if days < 0:
        return None
    net_flow = 0.0
    weighted = 0.0
    for f in flows or []:
        t = str(f.get("flow_type") or "").strip().lower()
        if t not in ("deposit", "withdrawal"):
            continue  # baseline excluded; unknowns ignored, never guessed
        fd = _parse_date(f.get("flow_date"))
        # Outside the tracking window → not part of THIS return. NB: a flow dated
        # before the baseline still counts in NCC (v2) but not here — a definitional
        # difference between net-contributions and period-return, not a bug.
        if fd is None or fd < d0 or fd > d1:
            continue
        try:
            amt = float(f.get("amount") or 0.0)
        except (TypeError, ValueError):
            continue
        signed = amt if t == "deposit" else -amt
        net_flow += signed
        w = ((d1 - fd).days / days) if days > 0 else 0.0
        weighted += signed * w
    denom = float(baseline_value) + weighted
    if denom <= 0:
        return None
    gain = float(ending_value) - float(baseline_value) - net_flow
    period_return = gain / denom
    annualized = None
    if days >= annualize_min_days and (1.0 + period_return) > 0:
        annualized = (1.0 + period_return) ** (365.0 / days) - 1.0
    return {
        "days":              days,
        "net_flow":          round(net_flow, 2),
        "gain":              round(gain, 2),
        "period_return_pct": round(period_return * 100, 2),
        "annualized_pct":    (round(annualized * 100, 2) if annualized is not None else None),
    }


# Display-caveat floor for annualized MWR, NOT an investment-policy threshold
# (Account growth and My Edge / Benchmark Mirror are both display-only, never
# gate a recommendation) — kept local rather than in constants.py, same
# rationale as risk.py's _ZERO_VOL_EPS: it can only add explanatory context to
# a number, never move a decision.
_ANNUALIZE_CAVEAT_MAX_DAYS = 90


def annualization_caveat(days: int | None, is_levered: bool = False) -> str | None:
    """Explanatory caption for when an annualized MWR is likely to look far more
    dramatic than the underlying period return warrants. A short tracking window
    (< _ANNUALIZE_CAVEAT_MAX_DAYS) and/or a levered (margin) account both amplify
    a real but modest dollar move into a much larger annualized percentage.
    Returns None when neither condition applies — nothing to caveat."""
    if days is None:
        return None
    short = days < _ANNUALIZE_CAVEAT_MAX_DAYS
    if short and is_levered:
        return (
            f"⚠️ Annualized over just {days} days on a leveraged account — short "
            "windows and margin both amplify a real but modest move into a much "
            "larger-looking yearly rate. Treat the period return above as the more "
            "grounded number right now."
        )
    if short:
        return (
            f"Annualized over just {days} days — short windows amplify swings into "
            "a larger-looking yearly rate. Treat this as directional, not literal, "
            "until the tracking period is longer."
        )
    if is_levered:
        return (
            "⚠️ This account carries a margin debit — leverage amplifies both gains "
            "and losses relative to your own capital, so the annualized rate moves "
            "more than an unlevered account's would."
        )
    return None


def build_equity_timeseries(snapshots_df, flows: list[dict]) -> dict | None:
    """Pair daily equity totals (from daily_snapshots) with a forward-filled NCC
    step function (from account_flows) for a capital-trend chart.

    Returns {"dates": [...], "equity_values": [...], "ncc_values": [...]} with
    all three lists aligned by ISO date string, sorted ascending, starting on or
    after the earliest baseline/flow event. Returns None when data is too thin.
    """
    if snapshots_df is None or getattr(snapshots_df, "empty", True):
        return None
    if not has_baseline(flows):
        return None

    snap = snapshots_df.copy()
    snap["snapshot_date"] = snap["snapshot_date"].astype(str).str[:10]
    snap["_mv"] = snap["shares"].astype(float) * snap["close_price"].astype(float)
    eq_series = snap.groupby("snapshot_date")["_mv"].sum().sort_index()

    # Sorted list of (date, signed_delta) for every contribution/withdrawal event.
    events: list[dict] = sorted(
        [
            {
                "date":  str(f.get("flow_date") or "")[:10],
                "delta": float(f.get("amount") or 0.0)
                         * (1.0 if str(f.get("flow_type") or "").strip().lower()
                            in _CONTRIB_TYPES else -1.0),
            }
            for f in (flows or [])
            if str(f.get("flow_type") or "").strip().lower()
               in (*_CONTRIB_TYPES, *_WITHDRAW_TYPES)
            and str(f.get("flow_date") or "")[:10]
        ],
        key=lambda x: x["date"],
    )
    if not events:
        return None

    # Forward-fill NCC: for each snapshot date, accumulate all events ≤ that date.
    running_ncc = 0.0
    ei = 0
    ncc_by_date: dict[str, float] = {}
    for d in eq_series.index:
        while ei < len(events) and events[ei]["date"] <= d:
            running_ncc += events[ei]["delta"]
            ei += 1
        ncc_by_date[d] = running_ncc

    baseline_date = events[0]["date"]
    rows = [
        (d, float(v), ncc_by_date[d])
        for d, v in eq_series.items()
        if d >= baseline_date
    ]
    if len(rows) < 2:
        return None

    dates, equity_values, ncc_values = zip(*rows)
    return {
        "dates":         list(dates),
        "equity_values": list(equity_values),
        "ncc_values":    list(ncc_values),
    }
