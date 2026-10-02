"""Leverage-vs-equity giveback disclosure (Summary / Book Safety, 2026-10-02).

Pure logic — no Streamlit, no DB calls. All I/O (the account_daily_snapshots
rows, the flow ledger) is read by the caller and injected here. Mirrors the
pattern of `coord_freshness.py`/`outage_gate.py`: a NEW module rather than
growing `portfolio.py`/`risk.py` (CLAUDE.md's "prefer a new module" ratchet
guidance), and never imported by any `_GATE_FILES` module — this stays
awareness-only, the same invariant `margin.py` already carries (see
`tests/test_margin.py`'s gate-isolation sweep, mirrored here in
`tests/test_leverage_giveback.py`).

Design (owner-approved 2026-10-02, see docs/mockups/2026-10-02-leverage-
giveback-mockup.html for the exact copy this module transcribes): states,
in render priority order, are `offline` / `insufficient_history` /
`unmeasured` / `equity_nonpositive` / `unlevered` / `not_elevated` /
`elevated` / `giveback`. The feature states a MEASURED SPLIT between how
much of a leverage rise came from equity shrinking vs. the margin loan
itself growing — it never asserts a single cause, since real data showed
both legs can move at once (see `leverage_change_split`).

No gate, no recommendation, no Act Today item. `disclosure_lines()`'s own
banned-word test (`tests/test_leverage_giveback.py`) is the enforcement
mechanism for that: no line may contain "sell"/"reduce"/"trim"/"should"/
"consider" in any state's output.
"""

from __future__ import annotations

from datetime import date as _date, datetime as _datetime

from . import margin as _margin

_MONTH_ABBR = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

_FLOWS_CAVEAT = (
    "Deposits/withdrawals couldn't be checked, so part of this change may be "
    "money moved in or out rather than market movement."
)


def _parse_date(x):
    """Best-effort -> datetime.date, or None. Accepts date/datetime/str/
    pandas Timestamp without requiring pandas (imported lazily, defensively)."""
    if x is None:
        return None
    if isinstance(x, _datetime):
        return x.date()
    if isinstance(x, _date):
        return x
    try:
        import pandas as _pd  # lazy — keeps a bare-python import path usable
        ts = _pd.Timestamp(x)
        if _pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        try:
            return _datetime.strptime(str(x)[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def _fmt_date(d) -> str:
    """"2026-09-30" -> "Sep 30" — no platform-dependent strftime padding."""
    d = _parse_date(d)
    if d is None:
        return "—"
    return f"{_MONTH_ABBR[d.month - 1]} {d.day}"


def _num(x):
    """Coerce a row field to float, treating None/NaN/inf/non-numeric as
    missing (-> None).

    `db.load_account_daily_snapshots()` returns a pandas DataFrame, and a
    DataFrame stores a NULL numeric column as `float('nan')`, not `None` —
    `x is not None` alone silently passes a NaN through as if it were a real
    measurement (`feedback_none_sentinel_meets_pandas`). Every row-field read
    in this module (net_equity, cash_balance, leverage, gross_book,
    call_distance_pct) MUST go through this, never a bare `.get()`."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):  # v != v <=> NaN
        return None
    return v


def _parsed_flow_events(flows) -> list:
    """`flows` (account_flows-shaped list, or None/[]) -> a sorted list of
    (date, signed_amount) tuples — 'deposit' positive, 'withdrawal' negative.
    Shared by `flow_adjusted_equity_series` and the net-flow-since-peak
    figure in `assess()` so the two can never compute a different amount
    for the same window."""
    flow_events = []
    if flows:
        for f in flows:
            t = str(f.get("flow_type") or "").strip().lower()
            if t not in ("deposit", "withdrawal"):
                continue  # baseline/unknown — excluded, never guessed
            fd = _parse_date(f.get("flow_date"))
            if fd is None:
                continue
            amt = _num(f.get("amount"))
            if amt is None:
                continue
            signed = amt if t == "deposit" else -amt
            flow_events.append((fd, signed))
    flow_events.sort(key=lambda x: x[0])
    return flow_events


# ── Flow-adjusted equity series ───────────────────────────────────────────

def flow_adjusted_equity_series(rows: list, flows) -> tuple:
    """Return (adjusted_equity_series, flows_checked).

    `rows` is a list of daily_snapshots-shaped dicts (ascending by date) —
    the same shape `account.compute_account_snapshot` produces, i.e. each
    row carries at least {snapshot_date, net_equity}. `flows` is the
    account_flows-shaped list `db.load_account_flows_for_dedup_check()`
    returns, or None (the offline sentinel — "couldn't check", distinct
    from an empty list, "checked, none found").

    Each day's RAW net_equity is adjusted by subtracting the net signed
    deposit/withdrawal flow since the window's first row (exclusive) through
    that day (inclusive) — 'deposit' is positive, 'withdrawal' negative,
    the SAME sign convention `account.money_weighted_return` already uses.
    Subtracting a withdrawal's negative contribution ADDS it back (so a
    real cash-out doesn't masquerade as a market loss); subtracting a
    deposit's positive contribution REMOVES its boost (so a real deposit
    doesn't mask a real loss underneath it).

    `flows=None` naturally falls through to zero adjustment for every row
    (no flow events built), so the series still returns correctly on
    UNADJUSTED equity rather than refusing — `flows_checked=False` is the
    caller's signal to disclose that, never a reason to skip evaluation.
    A null `net_equity` row is carried through as `None` (never coerced to
    0), consistent with "skip at the peak search", never "treat as zero".
    """
    flows_checked = flows is not None
    flow_events = _parsed_flow_events(flows)

    if not rows:
        return [], flows_checked

    window_start = _parse_date(rows[0].get("snapshot_date"))

    adjusted = []
    for r in rows:
        raw = _num(r.get("net_equity"))
        d = _parse_date(r.get("snapshot_date"))
        if raw is None or d is None or window_start is None:
            adjusted.append(None)
            continue
        net_flow = sum(amt for (fd, amt) in flow_events if window_start < fd <= d)
        adjusted.append(raw - net_flow)
    return adjusted, flows_checked


# ── Leverage-change decomposition ─────────────────────────────────────────

def leverage_change_split(E_pk: float, D_pk: float, E_now: float, D_now: float) -> dict:
    """Split a leverage rise into an equity-shrink leg and a loan-growth leg.

    `E_pk`/`E_now` are RAW net_equity on the peak day / today; `D_pk`/`D_now`
    are the margin DEBIT (the loan, i.e. -cash_balance when levered) on
    those same two days — not gross_book.

        from_equity = D_pk/E_now − D_pk/E_pk   (leverage rise holding the
                                                  loan flat at its peak-day
                                                  level — isolates what
                                                  equity shrinking alone did)
        from_debt   = (D_now − D_pk) / E_now     (the loan's own growth,
                                                  at today's equity)

    Identity (asserted in tests): from_equity + from_debt equals the actual
    leverage change, (1 + D_now/E_now) − (1 + D_pk/E_pk), since both terms
    telescope against the shared D_pk/E_now cross term.
    """
    from_equity = D_pk / E_now - D_pk / E_pk
    from_debt = (D_now - D_pk) / E_now
    return {"from_equity": from_equity, "from_debt": from_debt}


# ── Reference-target figures ──────────────────────────────────────────────

def book_reduction_to_target(gross_book: float, net_equity: float, target: float):
    """Dollar book shrink (holding equity fixed) that would put leverage at
    `target`. None when equity <= 0 or current leverage is already <= target
    (nothing to reduce)."""
    if net_equity is None or net_equity <= 0 or target is None or target <= 0:
        return None
    if gross_book / net_equity <= target:
        return None
    return gross_book - target * net_equity


def cash_to_target(gross_book: float, net_equity: float, target: float):
    """Dollar cash addition (holding the book fixed) that would put leverage
    at `target`. Same None-guard as `book_reduction_to_target`."""
    if net_equity is None or net_equity <= 0 or target is None or target <= 0:
        return None
    if gross_book / net_equity <= target:
        return None
    return gross_book / target - net_equity


def _call_distance_at_leverage(net_equity: float, leverage_x: float, rate: float):
    """`margin.call_distance()` evaluated at a HYPOTHETICAL leverage, holding
    `net_equity` fixed. Used for "a margin call would need roughly an X%
    book decline" at the reference target — note this is leverage-ratio-only
    (call_distance_pct depends on stock_value/owner_equity and rate alone,
    not on the absolute book size), so it is identical whether reached via
    book_reduction_to_target's path or cash_to_target's path."""
    if net_equity is None or net_equity <= 0 or leverage_x is None or leverage_x <= 1.0:
        return None
    stock_value = leverage_x * net_equity
    margin_debit = net_equity * (leverage_x - 1.0)
    cd = _margin.call_distance(stock_value, net_equity, margin_debit, rate)
    return cd["call_distance_pct"] if cd else None


# ── Main entry point ──────────────────────────────────────────────────────

def assess(
    snapshots_df, flows, *,
    target: float, drawdown_pct: float, lookback: int, min_history: int,
    confirm_days: int, stale_days: int, rate: float, today,
) -> dict:
    """The main entry point. Always returns a dict with an explicit "state"
    key — never raises on bad/missing data (a caller still wraps the call in
    its own try/except as defense-in-depth, per CLAUDE.md's "this must never
    appear anywhere except inside one card" requirement, but this function's
    own contract is to degrade to a state, not throw).

    `snapshots_df` is whatever `db.load_account_daily_snapshots()` returns —
    None (offline), an empty DataFrame, or a populated one, oldest-first.
    `flows` is `db.load_account_flows_for_dedup_check()`'s return — None or
    a list. `today` is a date/datetime (the caller's own now_et() reading;
    this function does no clock read).
    """
    flows_checked = flows is not None

    if snapshots_df is None:
        return {"state": "offline", "flows_checked": flows_checked}

    today_d = _parse_date(today)

    try:
        if hasattr(snapshots_df, "to_dict"):
            all_rows = snapshots_df.to_dict("records")
        else:
            all_rows = list(snapshots_df)
    except Exception:
        all_rows = []

    def _sort_key(r):
        return _parse_date(r.get("snapshot_date")) or _date.min

    all_rows = sorted(all_rows, key=_sort_key)
    window_rows = all_rows[-lookback:] if lookback and lookback > 0 else all_rows

    valid_rows = [
        r for r in window_rows
        if _num(r.get("net_equity")) is not None and _parse_date(r.get("snapshot_date")) is not None
    ]

    if len(valid_rows) < min_history:
        since_date = (
            _fmt_date(valid_rows[0]["snapshot_date"]) if valid_rows else _fmt_date(today_d)
        )
        return {
            "state": "insufficient_history",
            "flows_checked": flows_checked,
            "settled_days_available": len(valid_rows),
            "min_history": min_history,
            "since_date": since_date,
        }

    latest = window_rows[-1]
    latest_date = _parse_date(latest.get("snapshot_date"))
    latest_equity_raw = _num(latest.get("net_equity"))

    if (
        latest_equity_raw is None or latest_date is None or today_d is None
        or (today_d - latest_date).days > stale_days
    ):
        return {
            "state": "unmeasured",
            "flows_checked": flows_checked,
            "today_label": _fmt_date(today_d),
            "stale_days": stale_days,
        }

    latest_equity = latest_equity_raw
    gross_book_now = _num(latest.get("gross_book"))
    gross_book_now = gross_book_now if gross_book_now is not None else 0.0
    cash_balance_now = _num(latest.get("cash_balance"))

    if latest_equity <= 0:
        # The WORST state, not a calm one — never collapse into not_elevated
        # or unlevered just because a ratio isn't meaningful here.
        return {
            "state": "equity_nonpositive",
            "flows_checked": flows_checked,
            "date": _fmt_date(latest_date),
        }

    if cash_balance_now is not None and cash_balance_now >= 0:
        return {"state": "unlevered", "flows_checked": flows_checked}

    leverage_now = _num(latest.get("leverage"))
    if leverage_now is None:
        leverage_now = gross_book_now / latest_equity if latest_equity > 0 else None
    if leverage_now is None:
        return {
            "state": "unmeasured",
            "flows_checked": flows_checked,
            "today_label": _fmt_date(today_d),
            "stale_days": stale_days,
        }

    margin_debit_for_cd = -cash_balance_now if (cash_balance_now is not None and cash_balance_now < 0) else None
    cd_raw = None
    if margin_debit_for_cd is not None:
        cd_raw = _margin.call_distance(gross_book_now, latest_equity, margin_debit_for_cd, rate)
    # Prefer the LIVE recompute (`cd_raw`) over the row's stored
    # `call_distance_pct` for the DISPLAYED figure too, not just for
    # `in_call_now` below — otherwise the two could disagree if
    # MARGIN_MAINTENANCE_RATE changed since the row was written (frozen
    # `maintenance_rate` vs today's `rate`), which would let a stale stored
    # value render as if it were room remaining while the live flag says
    # the account is already in a call (Opus review, 2026-10-02 confirmation
    # pass). Stored value is only a fallback when no debit exists to
    # recompute against.
    cd_now = cd_raw["call_distance_pct"] if cd_raw is not None else _num(latest.get("call_distance_pct"))
    # `in_call` ground truth: prefer margin.call_distance()'s own flag (it
    # reflects the LIVE-recomputed cushion, not a possibly-stale stored
    # value); fall back to the sign of cd_now only when no debit was
    # available to recompute against (the formula guarantees
    # call_distance_pct >= 0 <=> cushion <= 0 <=> in_call, so this fallback
    # is an equivalence, not a guess — see margin.call_distance's docstring).
    if cd_raw is not None:
        in_call_now = cd_raw["in_call"]
    elif cd_now is not None:
        in_call_now = cd_now >= 0
    else:
        in_call_now = False

    if leverage_now <= target:
        return {
            "state": "not_elevated",
            "flows_checked": flows_checked,
            "leverage": leverage_now,
            "target": target,
            "date": _fmt_date(latest_date),
        }

    # ── Elevated branch: rolling-peak / drawdown machinery ────────────────
    adjusted, _ = flow_adjusted_equity_series(valid_rows, flows)
    triples = []  # (date, raw_equity, adjusted_equity), valid rows only
    for r, adj in zip(valid_rows, adjusted):
        d = _parse_date(r.get("snapshot_date"))
        raw = _num(r.get("net_equity"))
        if d is None or raw is None or adj is None:
            continue
        triples.append((d, raw, adj))
    triple_by_date = {d: (raw, adj) for (d, raw, adj) in triples}

    def _row_leverage(row, raw_equity):
        """A row's own leverage, for the confirm-days walk — read the
        stored `leverage` field first (NaN-safe via `_num`), falling back to
        gross_book/raw_equity recomputed consistently with the rest of this
        module. None when neither resolves (never guessed as 0 or target)."""
        lev = _num(row.get("leverage"))
        if lev is not None:
            return lev
        gb = _num(row.get("gross_book"))
        if gb is not None and raw_equity:
            return gb / raw_equity
        return None

    def _peak_as_of(cutoff_date):
        best_adj = best_raw = best_date = None
        n = 0
        for (d, raw, adj) in triples:
            if d > cutoff_date:
                break
            n += 1
            if best_adj is None or adj > best_adj:
                best_adj, best_raw, best_date = adj, raw, d
        return best_adj, best_raw, best_date, n

    def _drawdown_at(cutoff_date, adj_now):
        peak_adj, peak_raw, peak_date, n = _peak_as_of(cutoff_date)
        if peak_adj is None or not peak_adj:
            return None, None, None, None
        # Numerator and denominator both flow-adjusted (same basis) — mixing
        # adj_now/peak_adj against a RAW peak_raw denominator would silently
        # misstate the % whenever a flow landed inside the window.
        dd = (adj_now - peak_adj) / peak_adj * 100.0
        return dd, peak_raw, peak_date, n

    # Walk backward through window_rows in TABLE order (nulls included) so a
    # null row genuinely breaks the consecutive-day chain, per spec. Each
    # confirming row must meet BOTH conditions — drawdown past the mark AND
    # that row's own leverage above target — per LEVERAGE_GIVEBACK_CONFIRM_DAYS'
    # own constants.py comment; checking drawdown alone let a prior
    # non-elevated day's drawdown count toward confirming today's giveback.
    qualifying_count = 0
    cursor_idx = len(window_rows) - 1
    while cursor_idx >= 0 and qualifying_count < confirm_days:
        row = window_rows[cursor_idx]
        d = _parse_date(row.get("snapshot_date"))
        if d is None or d not in triple_by_date:
            break
        row_raw, adj = triple_by_date[d]
        dd, _peak_raw, _peak_date, _n = _drawdown_at(d, adj)
        row_leverage = _row_leverage(row, row_raw)
        if dd is None or dd > drawdown_pct or row_leverage is None or row_leverage <= target:
            break
        qualifying_count += 1
        cursor_idx -= 1

    latest_adj = triple_by_date.get(latest_date, (None, None))[1]
    latest_dd, latest_peak_raw, latest_peak_date, latest_n = _drawdown_at(latest_date, latest_adj)

    margin_debit_now = -cash_balance_now if (cash_balance_now is not None and cash_balance_now < 0) else None

    book_reduction = book_reduction_to_target(gross_book_now, latest_equity, target)
    cash_eq = cash_to_target(gross_book_now, latest_equity, target)
    cd_target = _call_distance_at_leverage(latest_equity, target, rate)

    # Net deposit/withdrawal between the peak date (exclusive) and today
    # (inclusive) — lets disclosure_lines() explicitly flag when the raw
    # dollar figures it shows and the flow-adjusted % it shows are on
    # different bases, rather than silently presenting both as if they
    # agreed (the "$10,000 vs $10,000, down 25%" bug).
    net_flow_since_peak = None
    if flows_checked and latest_peak_date is not None:
        _flow_events = _parsed_flow_events(flows)
        net_flow_since_peak = sum(
            amt for (fd, amt) in _flow_events if latest_peak_date < fd <= latest_date
        )

    base = {
        "flows_checked": flows_checked,
        "leverage": leverage_now,
        "target": target,
        "date": _fmt_date(latest_date),
        "drawdown_pct_now": latest_dd,
        "peak_date": _fmt_date(latest_peak_date) if latest_peak_date else None,
        "peak_raw_equity": latest_peak_raw,
        "n_day": latest_n,
        "net_equity_now": latest_equity,
        "gross_book_now": gross_book_now,
        "book_reduction": book_reduction,
        "cash_to_target": cash_eq,
        "call_distance_at_target": cd_target,
        "call_distance_now": cd_now,
        "in_call_now": in_call_now,
        "net_flow_since_peak": net_flow_since_peak,
    }

    if qualifying_count >= confirm_days:
        peak_row = next(
            (r for r in window_rows if _parse_date(r.get("snapshot_date")) == latest_peak_date),
            None,
        )
        D_pk = None
        leverage_pk = None
        if peak_row is not None:
            pk_cash = _num(peak_row.get("cash_balance"))
            if pk_cash is not None and pk_cash < 0:
                D_pk = -pk_cash
            pk_gross = _num(peak_row.get("gross_book"))
            if pk_gross is not None and latest_peak_raw:
                leverage_pk = pk_gross / latest_peak_raw
        # Only claim "giveback" (leverage rose while equity fell) when
        # leverage actually rose versus the peak day — the drawdown
        # condition alone says nothing about leverage's own direction (the
        # owner could have DE-levered while equity still fell, in which case
        # asserting "leverage rose... up from X" would be backwards and
        # false). leverage_pk is None only when the peak row itself can't be
        # resolved — also falls through rather than guess.
        leverage_rose = leverage_pk is not None and leverage_now > leverage_pk
        if leverage_rose:
            split = None
            if D_pk is not None and margin_debit_now is not None and latest_peak_raw and latest_equity:
                split = leverage_change_split(latest_peak_raw, D_pk, latest_equity, margin_debit_now)
            base.update({
                "state": "giveback",
                "leverage_pk": leverage_pk,
                "debt_pk": D_pk,
                "debt_now": margin_debit_now,
                "equity_pk": latest_peak_raw,
                "equity_now": latest_equity,
                "from_equity": split["from_equity"] if split else None,
                "from_debt": split["from_debt"] if split else None,
            })
            return base
        # Leverage did NOT rise — fall through to the plain "elevated" state
        # below (leverage_now > target still holds, since we're already past
        # the not_elevated check above; it just isn't a "giveback").

    base["drawdown_pct_threshold"] = drawdown_pct
    if qualifying_count == 1:
        base.update({"state": "elevated", "first_day_past_mark": True})
    else:
        base.update({"state": "elevated", "first_day_past_mark": False})
    return base


# ── Copy ───────────────────────────────────────────────────────────────────

def disclosure_lines(result: dict, money_fmt) -> list:
    """result -> [(kind, text), ...], kind in {"headline","caption","caveat"}.

    Transcribes the owner-approved copy in docs/mockups/2026-10-02-leverage-
    giveback-mockup.html verbatim (placeholders filled from `result`).
    `money_fmt` is called for every dollar figure — this function never
    builds its own "$" string, so the caller controls escaping/privacy
    masking (app.py passes `_m()` wrapped around a "\\$"-escaping formatter).

    No line emitted here may contain "sell"/"reduce"/"trim"/"should"/
    "consider" (case-insensitive) — this is awareness-only and must never
    read as a recommendation. Asserted directly in
    tests/test_leverage_giveback.py across every state.
    """
    state = result.get("state")
    flows_checked = result.get("flows_checked", True)

    if state == "offline":
        return [("caption",
            "📐 Settled account history couldn't be loaded this session, so the "
            "leverage-vs-equity read was not evaluated.")]

    if state == "insufficient_history":
        return [("caption",
            f"📐 Leverage-vs-equity read: {result.get('settled_days_available', 0)} settled "
            f"day(s) on record since {result.get('since_date', '—')}; it starts once "
            f"{result.get('min_history')} are available. Nothing is inferred before then.")]

    if state == "unmeasured":
        return [("caption",
            f"📐 Leverage-vs-equity read unavailable for {result.get('today_label', '—')}: "
            f"the settled cash figure is missing or older than {result.get('stale_days')} "
            "days. This is not the same as 'leverage is fine.'")]

    if state == "equity_nonpositive":
        return [("caption",
            f"📐 At {result.get('date', '—')} close the margin loan was at or above the "
            "stock book, so a leverage ratio isn't meaningful. See Book Safety above.")]

    if state == "unlevered":
        return []

    if state == "not_elevated":
        return [("caption",
            f"📐 Settled leverage {result.get('leverage', 0):.2f}× at {result.get('date', '—')} "
            f"close, at or below your {result.get('target', 0):.1f}× reference.")]

    if state == "elevated":
        dd = result.get("drawdown_pct_now")
        net_flow = result.get("net_flow_since_peak")
        flow_adjusted_note = bool(
            flows_checked is not False and net_flow is not None and abs(net_flow) > 0.005
        )
        dd_bit = ""
        if dd is not None and result.get("peak_date") and result.get("n_day"):
            if round(dd, 0) == 0:
                # A double-negative "0% below its high" reads as confusing;
                # this IS the high.
                dd_bit = f" Equity is at its {result['n_day']}-day high ({result['peak_date']})."
            else:
                dd_bit = (
                    f" Equity is {abs(dd):.0f}% below its {result['n_day']}-day high "
                    f"({result['peak_date']})"
                )
                dd_bit += (
                    ", after adjusting for deposits/withdrawals." if flow_adjusted_note else "."
                )
        br, ce = result.get("book_reduction"), result.get("cash_to_target")
        ref_bit = ""
        if br is not None and ce is not None:
            ref_bit = (
                f" For reference, a book about {money_fmt(br)} smaller at the same equity "
                f"(or about {money_fmt(ce)} more cash) would put leverage at "
                f"{result.get('target', 0):.1f}×."
            )
        lines = [("headline",
            f"📐 Settled leverage {result.get('leverage', 0):.2f}× at {result.get('date', '—')} "
            f"close, above your {result.get('target', 0):.1f}× reference.{dd_bit}{ref_bit}")]
        if flows_checked is False:
            lines.append(("caveat", _FLOWS_CAVEAT))
        if result.get("first_day_past_mark"):
            thr = result.get("drawdown_pct_threshold")
            thr_txt = f"{thr:.0f}%" if thr is not None else ""
            lines.append(("caveat",
                f"First settled close past the {thr_txt} giveback mark; the full read "
                "appears if it holds at the next close."))
        return lines

    if state == "giveback":
        dd = result.get("drawdown_pct_now")
        dd_abs = abs(dd) if dd is not None else 0.0
        leverage_pk = result.get("leverage_pk")
        delta = (
            (result.get("leverage") - leverage_pk)
            if (leverage_pk is not None and result.get("leverage") is not None)
            else None
        )
        net_flow = result.get("net_flow_since_peak")
        flow_adjusted_note = bool(
            flows_checked is not False and net_flow is not None and abs(net_flow) > 0.005
        )
        # `assess()` only ever enters this state when leverage_pk is not None
        # and leverage actually rose versus it — delta is therefore always
        # non-negative here, so "up from.../rise" is never a backwards claim.
        if flow_adjusted_note:
            equity_sentence = (
                f"Equity is {dd_abs:.0f}% below that high after adjusting for "
                f"deposits/withdrawals (raw, unadjusted figures: "
                f"{money_fmt(result.get('equity_now'))} vs "
                f"{money_fmt(result.get('equity_pk'))})."
            )
        else:
            equity_sentence = (
                f"Equity is {dd_abs:.0f}% below that high "
                f"({money_fmt(result.get('equity_now'))} vs {money_fmt(result.get('equity_pk'))})."
            )
        headline = (
            f"📐 Leverage rose while equity fell. At {result.get('date', '—')} close, settled "
            f"leverage was {result.get('leverage', 0):.2f}×, up from "
            f"{(leverage_pk if leverage_pk is not None else 0):.2f}× on "
            f"{result.get('peak_date', '—')}, when equity was at its "
            f"{result.get('n_day', '—')}-day high. {equity_sentence}"
        )
        if (
            delta is not None
            and delta >= 0
            and result.get("from_equity") is not None
            and result.get("from_debt") is not None
            and result["from_equity"] >= 0
            and result["from_debt"] >= 0
        ):
            headline += (
                f" Of the {delta:.2f}× rise, {result['from_equity']:.2f}× came from equity "
                "shrinking against a loan that was roughly flat, and "
                f"{result['from_debt']:.2f}× from the loan itself growing "
                f"({money_fmt(result.get('debt_pk'))} → {money_fmt(result.get('debt_now'))})."
            )
        lines = [("headline", headline)]

        br, ce = result.get("book_reduction"), result.get("cash_to_target")
        cdt, cdn = result.get("call_distance_at_target"), result.get("call_distance_now")
        in_call_now = result.get("in_call_now")
        if br is not None and ce is not None and cdt is not None and cdn is not None:
            ref_lead = (
                f"For reference: at the same equity, a stock book about {money_fmt(br)} "
                f"smaller (or about {money_fmt(ce)} more cash in the account) would put "
                f"leverage at your {result.get('target', 0):.1f}× reference, where a margin "
                f"call would need roughly a {abs(cdt):.0f}% book decline."
            )
            if in_call_now:
                # cdn is signed (>= 0 means the account is ALREADY at or past
                # the maintenance floor) -- abs()-ing it would silently read
                # as "room remaining" when there is none. Never collapse
                # that distinction away into a plain percentage.
                now_bit = (
                    " At this close, the account was already at or past the "
                    "estimated maintenance floor -- see Book Safety above."
                )
            else:
                now_bit = f" Against {abs(cdn):.1f}% at this close."
            lines.append(("caption",
                ref_lead + now_bit + " Awareness only; this changes no recommendation."))
        if flows_checked is False:
            lines.append(("caveat", _FLOWS_CAVEAT))
        return lines

    return []
