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


def _parse_date(v):
    """A `datetime.date` (not datetime) or None."""
    import datetime as _dt
    if isinstance(v, _dt.datetime):
        return v.date()
    return v if isinstance(v, _dt.date) else None


def _journal_shares_at(rows: list[tuple], b_date) -> float | None:
    """Shares of one ticker implied by journal rows dated <= `b_date`.

    rows: [(date, ACTION, shares, price)]. BUY +shares, SELL -shares, SPLIT sets
    the running total to its `shares` (the adjusted TOTAL, see `_is_split`).
    None when the order is ambiguous (a SPLIT shares a date with a BUY/SELL) or
    the running total after any date's net falls below -BROKER_DRIFT_SHARE_TOL
    (BUY/SELL are netted per date, so same-day row order doesn't matter). Snapped to 6 dp (as
    trim_sizing.exact_shares) so float noise can't flip an int comparison.
    """
    sel = [r for r in rows if r[0] <= b_date]
    split_dates = {r[0] for r in sel if _is_split(r[1])}
    if any(r[0] in split_dates and not _is_split(r[1]) and r[1] in ("BUY", "SELL")
           for r in sel):
        return None
    # Net BUY/SELL per ET date BEFORE applying: db.load_trades returns rows
    # newest-first, so same-date rows arrive in arbitrary order and a same-day
    # round trip would otherwise replay SELL-first and read as oversold.
    net_by_date: dict = {}
    split_total: dict = {}
    for d, action, sh, _px in sel:
        if _is_split(action):
            split_total[d] = sh
        elif action == "BUY":
            net_by_date[d] = net_by_date.get(d, 0.0) + sh
        elif action == "SELL":
            net_by_date[d] = net_by_date.get(d, 0.0) - sh
    total = 0.0
    for d in sorted(set(net_by_date) | set(split_total)):
        if d in split_total:
            total = split_total[d]
        else:
            total += net_by_date[d]
        total = round(total, 6)
        if total < -BROKER_DRIFT_SHARE_TOL:
            return None
    return max(total, 0.0)


def close_on_date(df, d) -> float | None:
    """Finite, positive "Close" of the bar whose ET calendar date == `d` (the
    last such bar), else None. A tz-aware index is converted to America/New_York
    first (so a UTC-stamped 00:00 bar maps to the PREVIOUS ET day); a tz-naive
    index is read as exchange-local dates. Never raises."""
    import math
    try:
        from stock_analyzer.held_stop import _et_dates
        cl = df["Close"].dropna()
        if cl.empty:
            return None
        dates = _et_dates(cl.index)
        target = _parse_date(d)
        if target is None:
            return None
        hit = [i for i, x in enumerate(dates) if x.date() == target]
        if not hit:
            return None
        v = float(cl.iloc[hit[-1]])
        return v if math.isfinite(v) and v > 0 else None
    except Exception:
        return None


def fill_late_logged_baseline(
    held: list[dict],
    baseline: dict,
    today_trades: list[dict],
    journal_trades: list[dict],
    baseline_date,
    today,
    baseline_closes: dict,
) -> tuple[dict, list[dict]]:
    """
    Repair prior-close baseline rows that the journal shows were wrong or
    missing because trades were logged AFTER their trading day's last snapshot.

    Both snapshot writers date the snapshot with today's ET date and skip
    non-trading days, so a trade logged late (e.g. a Friday fill journalled on
    Saturday) never enters the baseline, and a weekend can't rewrite it. Three
    shapes, each rebuilt from the journal:

      "missing"  : a held name absent from the baseline (read as `unbaselined`,
                   its FULL value counted as a day gain). Row = the shares held
                   at the start of today, at an effective close that blends the
                   baseline-date close (lots bought on or before `baseline_date`)
                   with the fill price (lots bought strictly between
                   `baseline_date` and `today`).
      "adjusted" : a held name already in the baseline with a different share
                   count (a late BUY or partial SELL dated on or before
                   `baseline_date`; read as `qty_drift`). Row = shares at the
                   start of today; the pre-existing part is valued at the
                   baseline row's OWN stored close, any between-buys at fill.
      "removed"  : a baseline name no longer held, not traded today, whose
                   journal replay to `baseline_date` is zero (a full exit logged
                   after the snapshot; read as an orphan). Row dropped.

    A ticker is repaired only when the journal's own replay to `baseline_date`
    (BUY +, SELL -, SPLIT sets the total) plus the buys in between accounts for
    the held shares. Anything else (a SELL or split in between, a non-positive
    fill, a split today or sharing a date with a BUY/SELL, an oversold history,
    shares the journal doesn't explain such as a broker rebaseline or a
    fully-sold old position, no usable baseline-date close for a missing name's
    pre-existing part, an orphan with no journal rows or with rows after the
    baseline date) is left alone, so the unbaselined / qty_drift / orphan
    warning still fires for genuine drift. A sell dated between `baseline_date`
    and `today` is never repaired (its proceeds are not in today's cash leg).

    held / baseline / today_trades : as in `compute_positions_day_pnl`.
    journal_trades  : [{"ticker","action","shares","price","date"}], every
                      journal trade, `date` the ET trade date; rows without a
                      parseable date are ignored.
    baseline_closes : {TICKER: close ON baseline_date} for held names missing
                      from the baseline (the other kinds need no lookup).

    Pure; never mutates its inputs; idempotent on its own output. Returns
    (new_baseline, filled) with `filled` sorted by ticker, each
    {"ticker","kind","shares", ...}: "missing" and "adjusted" carry "close" and
    "basis" ("baseline_close", "fill" or "mixed"); "adjusted" also carries
    "from_shares"; "removed" has shares 0 and carries "from_shares" only.
    """
    import math
    new_baseline = dict(baseline)
    filled: list[dict] = []
    base_keys = {str(k).strip().upper() for k in baseline}
    b_date = _parse_date(baseline_date)
    t_date = _parse_date(today)
    if b_date is None or t_date is None:
        return new_baseline, filled

    held_sh: dict[str, float] = {}
    for h in held:
        tk = str(h.get("ticker", "")).strip().upper()
        if tk:
            held_sh[tk] = held_sh.get(tk, 0.0) + _num(h.get("shares"))

    split_today = {str(t.get("ticker", "")).strip().upper()
                   for t in today_trades if _is_split(t.get("action"))}
    deltas = today_trade_share_delta(today_trades)
    closes = {str(k).strip().upper(): v for k, v in (baseline_closes or {}).items()}

    j_by_tk: dict[str, list[tuple]] = {}
    for j in journal_trades:
        d = _parse_date(j.get("date"))
        tk = str(j.get("ticker", "")).strip().upper()
        if d is None or not tk:
            continue
        j_by_tk.setdefault(tk, []).append(
            (d, str(j.get("action", "")).strip().upper(),
             _num(j.get("shares")), _num(j.get("price"))))

    base_orig = {str(k).strip().upper(): k for k in baseline}
    traded_today = {str(t.get("ticker", "")).strip().upper() for t in today_trades}

    def _blend(tk, start, rows, base_close):
        """(close_eff, between_shares, pre) or None when the journal can't
        explain `start` held shares."""
        between = [r for r in rows if b_date < r[0] < t_date]
        if any(r[1] != "BUY" for r in between):
            return None
        if any(r[2] <= 0 or r[3] <= 0 for r in between):
            return None
        between_shares = sum(r[2] for r in between)
        between_cost = sum(r[2] * r[3] for r in between)
        # Replay the journal to the baseline date: the held count comes from the
        # holdings table, so only repair when the journal itself explains it.
        net_b = _journal_shares_at(rows, b_date)
        if net_b is None:
            return None                   # ambiguous / oversold history
        # `held` is int-truncated by build_portfolio_df, so compare truncated.
        if abs(int(round(net_b + between_shares, 6)) - start) > BROKER_DRIFT_SHARE_TOL:
            return None                   # journal doesn't account for the held shares
        pre = start - between_shares
        if pre < -BROKER_DRIFT_SHARE_TOL:
            return None
        if abs(pre) <= BROKER_DRIFT_SHARE_TOL:
            pre = 0.0
        if pre > 0 and (not math.isfinite(base_close) or base_close <= 0):
            return None
        return (pre * (base_close if pre > 0 else 0.0) + between_cost) / start, between_shares, pre

    for tk in sorted(held_sh):
        if tk in split_today:
            continue
        start = held_sh[tk] - deltas.get(tk, 0.0)
        if start <= BROKER_DRIFT_SHARE_TOL:
            continue                      # bought today; the cash leg handles it
        rows = j_by_tk.get(tk, [])
        if tk in base_keys:
            okey = base_orig[tk]
            base_sh = _num(baseline[okey].get("shares"))
            if abs(start - base_sh) <= BROKER_DRIFT_SHARE_TOL:
                continue                  # row is already right
            base_close = _num(baseline[okey].get("close"))
            kind = "adjusted"
        else:
            okey = tk
            base_sh = None
            base_close = _num(closes.get(tk))
            kind = "missing"
        got = _blend(tk, start, rows, base_close)
        if got is None:
            continue
        close_eff, between_shares, pre = got
        new_baseline[okey] = {"shares": start, "close": close_eff}
        basis = ("baseline_close" if between_shares == 0
                 else "fill" if pre == 0 else "mixed")
        entry = {"ticker": tk, "kind": kind, "shares": start,
                 "close": round(close_eff, 4), "basis": basis}
        if kind == "adjusted":
            entry["from_shares"] = base_sh
        filled.append(entry)

    # (B) a full exit logged after the snapshot: in the baseline, not held, not
    # traded today, journal replay to the baseline date is zero.
    for tk in sorted(base_keys):
        if tk in held_sh or tk in traded_today or tk in split_today:
            continue
        rows = j_by_tk.get(tk, [])
        if not rows or any(b_date < r[0] < t_date for r in rows):
            continue
        net_b = _journal_shares_at(rows, b_date)
        if net_b is None or abs(net_b) > BROKER_DRIFT_SHARE_TOL:
            continue
        okey = base_orig[tk]
        base_sh = _num(baseline[okey].get("shares"))
        new_baseline.pop(okey, None)
        filled.append({"ticker": tk, "kind": "removed", "shares": 0.0,
                       "from_shares": base_sh})
    filled.sort(key=lambda e: e["ticker"])
    return new_baseline, filled


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
