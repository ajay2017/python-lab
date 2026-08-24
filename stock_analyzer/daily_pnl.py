"""
Positions-scope day-over-day P&L (Tier B).

Computes a TRUE single-day P&L for the tracked stock positions using the
broker-style equity-delta identity, anchored to a persisted prior-close
snapshot baseline (db.daily_snapshots) plus the day's trades:

    day_pnl = Σ(current_price × current_shares)              # today's marked held value
            − Σ(baseline_close × baseline_shares)            # prior-close snapshot value
            + (Σ today's sell proceeds − Σ today's buy cost) # cash from today's trades

Because the baseline is the PRIOR CLOSE (not cost basis), every term measures
only the DAY's move — so this is what the broker's account "Today" reflects for
the equity sleeve:
  • a name SOLD today contributes (sell − prior_close) × qty   (realized day-move only —
    NOT the full holding-period gain that trades.realized_pnl would wrongly add)
  • a name BOUGHT today contributes (current − fill) × qty     (same-day fill basis)
  • a name HELD through contributes (current − prior_close) × qty

Exact for tracked positions, IGNORING fees / dividends / corporate actions and
EXCLUDING cash + external deposits/withdrawals (positions scope). The caller
labels it accordingly and never claims penny-parity with the broker. Pure /
UI-free / no I/O.
"""

from __future__ import annotations

from stock_analyzer.constants import BROKER_DRIFT_SHARE_TOL, QTY_DRIFT_TRUNCATION_AMBIGUITY_SHARES


def _num(v, default: float = 0.0) -> float:
    """Float coercion that maps None / NaN / junk to `default` (never raises)."""
    try:
        f = float(v)
        return default if f != f else f  # NaN check
    except (TypeError, ValueError):
        return default


def today_trade_cash_delta(today_trades: list[dict]) -> float:
    """
    Net cash from today's trades = Σ sell proceeds − Σ buy cost.

    Only BUY / SELL move cash; SPLIT (and any other synthetic action) is ignored
    — a split changes share count, not cash, and must never enter the delta
    (stays SPLIT-aware per the action-field contract).
    """
    delta = 0.0
    for t in today_trades:
        action = str(t.get("action", "")).strip().upper()
        cash = _num(t.get("price")) * _num(t.get("shares"))
        if action == "SELL":
            delta += cash
        elif action == "BUY":
            delta -= cash
    return delta


def _is_split(action) -> bool:
    """SPLIT rows carry `shares` = the adjusted TOTAL, not a delta (db.py's
    SPLIT convention), so they can never be summed into a share delta. Matched
    by substring to stay consistent with every other SPLIT check in the codebase
    (`"SPLIT" in action`), which tolerates decorated actions like "SPLIT 4:1".
    """
    return "SPLIT" in str(action or "").strip().upper()


def today_trade_share_delta(today_trades: list[dict]) -> dict[str, float]:
    """Net share change per ticker from today's BUY/SELL rows (BUY +, SELL −).

    SPLIT is excluded — see `_is_split`. A ticker whose only row today is a
    split is therefore ABSENT from this map, which is not the same as "no
    change"; `reconcile_baseline` handles that by skipping such tickers
    entirely rather than reporting a drift it cannot compute.
    """
    delta: dict[str, float] = {}
    for t in today_trades:
        action = str(t.get("action", "")).strip().upper()
        ticker = str(t.get("ticker", "")).strip().upper()
        if not ticker or _is_split(action):
            continue
        if action == "BUY":
            delta[ticker] = delta.get(ticker, 0.0) + _num(t.get("shares"))
        elif action == "SELL":
            delta[ticker] = delta.get(ticker, 0.0) - _num(t.get("shares"))
    return delta


def reconcile_baseline(
    held: list[dict],
    baseline: dict,
    today_trades: list[dict],
) -> dict:
    """
    Every way the prior-close baseline and the current holdings can disagree
    without a trade explaining it. Each one silently distorts the day-P&L
    identity, so each is surfaced rather than absorbed.

    Four shapes, all the same defect class — "the baseline describes a book
    that is not the one being marked":

      • `orphans`          — in the baseline, not held, no trade today. Its
                        baseline value is subtracted with nothing offsetting,
                        so the figure is UNDERSTATED by that position's FULL
                        prior-close value — not by a day's move, which is one
                        to two orders of magnitude smaller and in the
                        reassuring direction.
      • `qty_drift`        — held, but at a share count that today's trades do
                        not explain. Only the drifted portion is wrong, so the
                        error is `drift × price` — the case that produced a
                        silent $1,091.62 error on 2026-08-23 while the orphan
                        check (which fires only on a fully-vanished ticker)
                        stayed quiet.
      • `unbaselined`      — held, absent from the baseline, no trade today.
                        The full position value is added with nothing
                        subtracting it, so the figure is OVERSTATED by roughly
                        `shares × price`, not by a day's move. Reachable via a
                        partial `daily_snapshots` write, and routinely via a
                        stale baseline (anything opened between the baseline
                        date and today lands here).
      • `unbaselined_sells` — traded today (a SELL), absent from BOTH baseline
                        AND held. The sell-side twin of `unbaselined`: bought
                        on some day after the last baseline snapshot (the same
                        stale-baseline gap), held without ever being captured,
                        then sold today. Only the sell appears in
                        `today_trades`; the earlier buy doesn't. Its proceeds
                        enter `cash_delta` with nothing subtracting the
                        acquisition cost, OVERSTATING the figure by roughly the
                        position's value. A same-day round trip (bought >=
                        sold) is NOT flagged — both legs enter `cash_delta` and
                        the identity is exact, since `current_val` and
                        `baseline_val` both contribute zero for a ticker never
                        held at prior close and not held now. Only the
                        EXCESS sold beyond what was bought today came from a
                        source this function cannot see.

    NOT COVERED, and a different defect class (found in review 2026-08-24):
    a ticker with a net BUY today (bought > sold) that ends up neither held
    nor baselined is a data inconsistency this function cannot arise from in
    a correctly-journalled book (a real net-buy should show up in `held`) —
    so it is deliberately NOT flagged here. If it ever occurs, it is the
    OPPOSITE sign of `unbaselined_sells`: the buy cost enters `cash_delta`
    with nothing offsetting it, UNDERSTATING the figure, not overstating it.
    A future symptom of this shape is a data-integrity bug in the holdings
    computation upstream, not a gap in this reconciliation.

    Share comparisons use `BROKER_DRIFT_SHARE_TOL` — the same tolerance the
    broker-vs-app reconciliation uses, because it is the same question ("is this
    share difference real or float noise"), not a second policy number.

    Pure. Returns lists that are always present (possibly empty) — never None,
    since "no disagreement" and "not checked" are both knowable here.
    """
    # ACCUMULATE duplicate rows rather than last-wins: `current_val` sums every
    # row, so an auditor that overwrote would disagree with the very identity it
    # audits. Unreachable today (build_portfolio_df emits one row per ticker),
    # but mirroring the arithmetic costs nothing and removes the trap.
    _held_by_ticker: dict[str, dict] = {}
    for h in held:
        tk = str(h.get("ticker", "")).strip().upper()
        if not tk:
            continue
        if tk in _held_by_ticker:
            _held_by_ticker[tk] = {
                "ticker": tk,
                "shares": _num(_held_by_ticker[tk].get("shares")) + _num(h.get("shares")),
                "price":  _num(h.get("price")),      # last price wins
            }
        else:
            _held_by_ticker[tk] = h
    _base_by_ticker = {str(k).strip().upper(): v for k, v in baseline.items()}
    _traded  = {str(t.get("ticker", "")).strip().upper() for t in today_trades}
    _split   = {str(t.get("ticker", "")).strip().upper()
                for t in today_trades if _is_split(t.get("action"))}
    _deltas  = today_trade_share_delta(today_trades)

    # A ticker fully absent from today's book AND today's trades is the classic
    # orphan — reported under its own key for continuity. Everything else is
    # judged on the RESIDUAL below, because "was there a trade today" is not the
    # question: a trade that explains only PART of a change leaves the rest just
    # as unexplained, and an early version of this function exempted any traded
    # ticker outright, which hid exactly that case.
    # Orphans carry a dollar figure like the other two shapes. An orphan's
    # baseline value is subtracted with NOTHING offsetting it, so the error is
    # the position's full prior-close value — NOT "a day's move", which is one
    # to two orders of magnitude smaller and in the reassuring direction.
    # Negative = day-P&L UNDERSTATED by this much.
    _orphan_tickers = {
        tk for tk in _base_by_ticker
        if tk not in _held_by_ticker and tk not in _traded
    }
    orphans = [
        {
            "ticker":          tk,
            "baseline_shares": _num(_base_by_ticker[tk].get("shares")),
            "value_impact":    round(
                -_num(_base_by_ticker[tk].get("shares"))
                * _num(_base_by_ticker[tk].get("close")), 2),
        }
        for tk in sorted(_orphan_tickers)
    ]

    qty_drift = []
    unbaselined = []
    for tk in sorted(set(_base_by_ticker) | set(_held_by_ticker)):
        if tk in _orphan_tickers:
            continue                      # already reported, don't double-count
        # A split today rewrites the share count by a ratio this function cannot
        # recover from the row (it stores the new TOTAL). It is also the one case
        # with no error to report: post-split shares × post-split price
        # reconciles against the pre-split baseline, and the cash leg is
        # correctly zero. Silence here is the right answer, not a concession.
        if tk in _split:
            continue

        _h          = _held_by_ticker.get(tk)
        _b          = _base_by_ticker.get(tk)
        cur_shares  = _num(_h.get("shares")) if _h else 0.0
        base_shares = _num(_b.get("shares")) if _b else 0.0
        # An unheld ticker has no live price, so value its residual at the
        # baseline close — the same basis the day-P&L subtracted it at.
        price       = _num(_h.get("price")) if _h else _num(_b.get("close")) if _b else 0.0

        expected   = base_shares + _deltas.get(tk, 0.0)
        unexplained = cur_shares - expected

        # UNIT GUARD. The held/baseline sides are display-truncated integers
        # (`portfolio.build_portfolio_df` stores `int(shares)`, and both
        # `daily_snapshots` writers write from that same frame) while the trade
        # deltas are raw to 4dp (`broker_sync` rounds fills, it does not
        # truncate). Comparing the two reports a fractional "drift" that is
        # really just truncation — it would fire on every fractional-quantity
        # day, which is how a banner gets trained into noise and costs you the
        # next real one. Since floor(x + n) == floor(x) + n for integer n, an
        # INTEGRAL net delta compares exactly; only a fractional one is unsafe.
        # Note the `or` rather than `and`: a fractional value on EITHER side is
        # enough to make the comparison unsafe, and today only the delta can be
        # fractional. If a future change stops truncating either side, this
        # still fails closed instead of newly crying wolf.
        #
        # 2026-08-24: this used to `continue` unconditionally once the shape
        # above was detected, regardless of the residual's size — an EARLIER
        # version of this comment documented that as a deliberate false
        # negative (a genuine whole-share drift missed on a fractional-fill
        # day), pinned by a test showing a 9.5-share drift silently reported as
        # zero. That was an unbounded blind spot absorbing a bounded error:
        # truncation ambiguity is provably capped at just under 1.0 share (see
        # QTY_DRIFT_TRUNCATION_AMBIGUITY_SHARES's definition), so any residual
        # AT OR ABOVE that bound cannot be truncation noise and must be
        # reported regardless of what fill happened today. Only a
        # genuinely-ambiguous sub-bound residual stays suppressed now — that
        # narrower blind spot is the mathematical floor, not a policy choice.
        if ((float(cur_shares).is_integer() or float(base_shares).is_integer())
                and not float(expected).is_integer()
                and abs(unexplained) < QTY_DRIFT_TRUNCATION_AMBIGUITY_SHARES):
            continue

        if abs(unexplained) <= BROKER_DRIFT_SHARE_TOL:
            continue

        if _b is None:
            unbaselined.append({
                "ticker":            tk,
                "current_shares":    cur_shares,
                "unexplained_shares": round(unexplained, 6),
                # Positive = day-P&L overstated. Approximate: the true error is
                # the shares' COST BASIS, which this function does not have.
                "value_impact":      round(unexplained * price, 2),
            })
        else:
            qty_drift.append({
                "ticker":          tk,
                "baseline_shares": base_shares,
                "expected_shares": expected,
                "current_shares":  cur_shares,
                "drift_shares":    round(unexplained, 6),
                # Signed: positive = day-P&L overstated by this much.
                "value_impact":    round(unexplained * price, 2),
            })

    # `unbaselined_sells` — the fourth shape. Distinct from the loop above: it
    # iterates tickers keyed off TODAY'S TRADES, not `_base_by_ticker |
    # _held_by_ticker`, because this shape is precisely the case where a
    # ticker is absent from both. today_trades already carries `price` per
    # row (verified 2026-08-24 against app.py's caller) — no signature change
    # needed despite an earlier note here claiming otherwise.
    _bought_today: dict[str, float] = {}
    _sold_today: dict[str, float] = {}
    _sell_fills: dict[str, list[tuple[float, float]]] = {}  # tk -> [(shares, price), ...]
    for t in today_trades:
        action = str(t.get("action", "")).strip().upper()
        tk = str(t.get("ticker", "")).strip().upper()
        if not tk or _is_split(action):
            continue
        shares = _num(t.get("shares"))
        if action == "BUY":
            _bought_today[tk] = _bought_today.get(tk, 0.0) + shares
        elif action == "SELL":
            _sold_today[tk] = _sold_today.get(tk, 0.0) + shares
            _sell_fills.setdefault(tk, []).append((shares, _num(t.get("price"))))

    unbaselined_sells = []
    for tk in sorted(_sell_fills):
        if tk in _base_by_ticker or tk in _held_by_ticker or tk in _split:
            continue    # already handled above, or a split with no error to report
        excess = _sold_today.get(tk, 0.0) - _bought_today.get(tk, 0.0)
        if excess <= BROKER_DRIFT_SHARE_TOL:
            continue    # a same-day round trip (bought >= sold) is self-correcting
        _fills = _sell_fills[tk]
        _sh_sum = sum(sh for sh, _ in _fills)
        # Volume-weighted average of today's sell fills — neither a live price
        # (not held) nor a baseline close (no baseline row) is available for
        # this shape, so the sell fills are the only price signal there is.
        _vwap = (sum(sh * px for sh, px in _fills) / _sh_sum) if _sh_sum else 0.0
        unbaselined_sells.append({
            "ticker":          tk,
            "unbacked_shares": round(excess, 6),
            # Positive = day-P&L overstated. Approximate, like `unbaselined`'s:
            # the true error is the missing prior-close value, which this
            # function does not have for a ticker with no baseline row.
            "value_impact":    round(excess * _vwap, 2),
        })

    return {
        "orphans": orphans, "qty_drift": qty_drift, "unbaselined": unbaselined,
        "unbaselined_sells": unbaselined_sells,
    }


def compute_positions_day_pnl(
    held: list[dict],
    baseline: dict,
    today_trades: list[dict],
    total_value: float,
) -> dict | None:
    """
    held         : [{"ticker","shares","price"}]  CURRENTLY-held positions + current price
    baseline     : {ticker: {"shares","close"}}   prior-close snapshot (yesterday's EOD)
    today_trades : [{"action","shares","price"}]  today's trades (BUY/SELL used)
    total_value  : current positions value — the % denominator

    Returns the day-P&L dict, or None when no baseline exists (caller falls back
    to the held-only mark). The caller is responsible for only invoking this when
    every currently-held position is priced — a missing live price would make the
    equity delta unreliable, so the caller withholds Tier-B and shows the
    fail-loud held mark instead.
    """
    if not baseline:
        return None

    current_val  = sum(_num(h.get("price")) * _num(h.get("shares")) for h in held)
    baseline_val = sum(_num(b.get("close")) * _num(b.get("shares")) for b in baseline.values())
    cash_delta   = today_trade_cash_delta(today_trades)

    day_pnl     = current_val - baseline_val + cash_delta
    day_pnl_pct = (day_pnl / total_value * 100.0) if total_value else 0.0

    # Every way the baseline can describe a different book than the one being
    # marked — each silently distorts the delta above. See `reconcile_baseline`.
    _recon = reconcile_baseline(held, baseline, today_trades)

    return {
        "day_pnl":          round(day_pnl, 2),
        "day_pnl_pct":      round(day_pnl_pct, 2),
        "trade_cash_delta": round(cash_delta, 2),
        "current_value":    round(current_val, 2),
        "baseline_value":   round(baseline_val, 2),
        "n_baseline":       len(baseline),
        "orphans":            _recon["orphans"],            # baseline names with no current holding and no recorded exit today
        "qty_drift":          _recon["qty_drift"],          # held, but at a share count today's trades don't explain
        "unbaselined":        _recon["unbaselined"],        # held, absent from the baseline, no trade today
        "unbaselined_sells":  _recon["unbaselined_sells"],  # sold today, absent from BOTH baseline and held (the sell-side twin of unbaselined)
    }
