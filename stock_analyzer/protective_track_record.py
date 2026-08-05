"""
Protective Track Record — 🛡️ Defense facet of the 🎯 Engine Track Record card
(🧾 Summary page, F-229 Phase 2).

Mirrors `recommendations_history.py`'s BUY-side ("⚔️ Offense") measurement
pattern but over a different substrate (`exit_signals`, not `recommendations`)
and with a sign-flipped alpha: a protective EXIT/TRIM call is "right" when the
flagged name UNDERPERFORMS SPY after the warning.

    protect_alpha_pct = spy_return_pct(signal_date → today)
                       − name_return_pct(signal_date → today)

Positive ⇒ the flagged name lagged the benchmark after the warning (the
caution was right). Negative ⇒ the name recovered faster than SPY (the call
ran early). Never dressed as a failure — see `protective_headline`'s honesty
rules.

Scope: EXIT + TRIM only (locked decision, `docs/plans/engine-track-record-meter.md`
Phase 2). WATCH is awareness, not a call to act; RISK_OFF is a portfolio-wide
macro call, not per-ticker — neither belongs in a per-ticker-vs-SPY grade.

Critical dedup invariant: cron writes one `exit_signals` row per day a name
stays flagged, so a 15-day EXIT episode is 15 rows for one ticker. Averaging
per-row double-counts and biases toward whichever name stayed flagged longest.
`collapse_by_ticker()` collapses to exactly one row per distinct ticker
(earliest signal_date = longest/most-mature window, highest-severity type
reached) before any aggregate is computed.

Pure logic — no Streamlit, no DB or API calls. Caller supplies:
  - signals_df       exit_signals rows (DataFrame or list of dicts), already
                      filtered — or filtered internally here — to EXIT/TRIM.
  - current_prices    {ticker: latest_price}
  - today             date
  - spy_close_by_date {date: close} — same series `recommendations_history`
                      uses; reuse `_spy_return_pct` verbatim, no reimplementation.
"""

from datetime import date

from stock_analyzer.recommendations_history import _f, _spy_return_pct, _to_date

# Locked scope (see module docstring) — WATCH and RISK_OFF are OUT OF SCOPE.
_PROTECTIVE_SCOPE = ("EXIT", "TRIM")
# Severity order for collapse_by_ticker's escalation rule: EXIT outranks TRIM.
_SEVERITY_RANK = {"TRIM": 0, "EXIT": 1}


def _rows(signals_df):
    """Yield row dict-likes from either a DataFrame or a list of dicts."""
    if signals_df is None:
        return []
    if hasattr(signals_df, "iterrows"):
        return [r for _, r in signals_df.iterrows()]
    return list(signals_df)


def compute_protective_outcomes(
    signals_df,
    current_prices: dict | None,
    today: date,
    spy_close_by_date: dict | None,
    min_days: int,
) -> list[dict]:
    """
    Add outcome fields to each protective (EXIT/TRIM) signal row.

    Rows whose `signal_type` is not EXIT/TRIM are dropped here as a safety
    net even if the caller already filtered — WATCH/RISK_OFF must never
    reach this scope (locked decision).

    Returns a list of dicts:
      ticker             str
      signal_date        date | None
      signal_type        str  ("EXIT" | "TRIM")
      price_at_signal    float | None
      name_return_pct    float | None — (current − price_at_signal) / price_at_signal * 100
      spy_return_pct     float | None — SPY % over signal_date → today
      protect_alpha_pct  float | None — spy_return_pct − name_return_pct (sign-flipped
                         vs BUY-side: positive = the flagged name underperformed,
                         i.e. the caution was right). None if either input is None.
      days_since         int | None
      maturing           bool — True when younger than `min_days` (or when
                         signal_date is unknown) — excluded from headline aggregates.
    """
    current_prices = current_prices or {}
    out: list[dict] = []
    for r in _rows(signals_df):
        signal_type = str(r.get("signal_type", "") or "").strip().upper()
        if signal_type not in _PROTECTIVE_SCOPE:
            continue

        ticker = str(r.get("ticker", "") or "").strip().upper()
        signal_date = _to_date(r.get("signal_date"))

        # _f(..., default=None) treats both None AND NaN as "missing" — a
        # legacy exit_signals row with a NULL price_at_signal reads back from
        # the DataFrame as float('nan'), not None (pandas convention), and
        # NaN is truthy in Python so a bare `if pas else None` guard let it
        # through; _f's `x != x` NaN check closes that gap.
        price_at_signal = _f(r.get("price_at_signal"), default=None)

        cur = _f(current_prices.get(ticker), default=None)
        if cur is not None and cur <= 0:
            cur = None

        if cur is not None and price_at_signal:
            name_return_pct = (cur - price_at_signal) / price_at_signal * 100.0
        else:
            name_return_pct = None

        spy_return_pct = _spy_return_pct(spy_close_by_date, signal_date, today)

        if name_return_pct is not None and spy_return_pct is not None:
            protect_alpha_pct = spy_return_pct - name_return_pct
        else:
            protect_alpha_pct = None

        days_since = (today - signal_date).days if signal_date is not None else None
        maturing = (days_since is None) or (days_since < min_days)

        out.append({
            "ticker":            ticker,
            "signal_date":       signal_date,
            "signal_type":       signal_type,
            "price_at_signal":   price_at_signal,
            "name_return_pct":   name_return_pct,
            "spy_return_pct":    spy_return_pct,
            "protect_alpha_pct": protect_alpha_pct,
            "days_since":        days_since,
            "maturing":          maturing,
        })
    return out


def collapse_by_ticker(enriched: list[dict]) -> list[dict]:
    """
    THE DEDUP INVARIANT. Cron writes one row per day a name stays flagged —
    a 15-day EXIT episode is 15 rows for the same ticker. Group by ticker and
    keep exactly ONE representative row per distinct ticker: the row with the
    EARLIEST `signal_date` (longest/most-mature window, avoids recency bias).

    The representative row's anchor fields (signal_date, price_at_signal, and
    all outcome fields derived from them) are kept AS-IS from that earliest
    row — but a `severity` field is added/overridden to the HIGHEST-severity
    `signal_type` seen across ALL of that ticker's rows in the input (EXIT
    outranks TRIM), so a ticker first flagged TRIM then later escalated to
    EXIT is labeled by the worse outcome even though the anchor date/price
    is still from the earlier TRIM row.

    Returns one dict per distinct ticker (order not guaranteed).
    """
    by_ticker: dict[str, list[dict]] = {}
    for r in enriched:
        tk = r.get("ticker")
        if not tk:
            continue
        by_ticker.setdefault(tk, []).append(r)

    collapsed: list[dict] = []
    for tk, rows in by_ticker.items():
        dated = [r for r in rows if r.get("signal_date") is not None]
        rep = min(dated, key=lambda r: r["signal_date"]) if dated else rows[0]

        severities = [
            r.get("signal_type") for r in rows
            if r.get("signal_type") in _SEVERITY_RANK
        ]
        severity = (
            max(severities, key=lambda s: _SEVERITY_RANK[s])
            if severities else rep.get("signal_type")
        )

        out_row = dict(rep)
        out_row["severity"] = severity
        collapsed.append(out_row)
    return collapsed


def protective_headline(
    enriched_collapsed: list[dict],
    min_calls: int,
    firm_calls: int,
) -> dict:
    """
    Compact trust-headline for the 🛡️ Defense facet, mirroring
    `recommendations_history.engine_trust_headline`'s exact honesty structure.

    Operates on *enriched_collapsed* — the output of `collapse_by_ticker()`;
    the caller is responsible for running the full
    `compute_protective_outcomes` → `collapse_by_ticker` chain first.

    Returns a dict:
        protect_alpha  float | None — mean protect_alpha_pct over the mature +
                                      priced population (None if that
                                      population is empty)
        n_mature       int          — count of collapsed rows that are both
                                      mature AND have a priced protect_alpha —
                                      the SAME population protect_alpha is
                                      averaged over, so band classification,
                                      caption count, and alpha all describe
                                      one consistent set of calls (the exact
                                      population-parity the BUY-side headline
                                      had to fix)
        since_date     date | None  — earliest signal_date across ALL
                                      collapsed rows (mirrors how the BUY-side
                                      since_date uses ALL new_picks, not just
                                      mature ones)
        band           str          — "building" | "early" | "firm"
    """
    _empty: dict = {
        "protect_alpha": None,
        "n_mature":       0,
        "since_date":     None,
        "band":           "building",
    }
    if not enriched_collapsed:
        return _empty

    # Belt-and-suspenders: `protect_alpha_pct` should never be NaN by the
    # time it reaches here (see compute_protective_outcomes' _f guards), but
    # this is a trust-surface number — a single NaN poisons the whole mean
    # and renders as the literal string "nan", so don't rely on a single
    # upstream fix point. `x != x` is True only for NaN.
    mature_priced = [
        r for r in enriched_collapsed
        if not r.get("maturing")
        and r.get("protect_alpha_pct") is not None
        and r["protect_alpha_pct"] == r["protect_alpha_pct"]
    ]
    n_mature = len(mature_priced)
    protect_alpha = (
        round(sum(r["protect_alpha_pct"] for r in mature_priced) / n_mature, 2)
        if n_mature else None
    )

    signal_dates = [
        r["signal_date"] for r in enriched_collapsed
        if r.get("signal_date") is not None
    ]
    since_date = min(signal_dates) if signal_dates else None

    if n_mature < min_calls:
        band = "building"
    elif n_mature < firm_calls:
        band = "early"
    else:
        band = "firm"

    return {
        "protect_alpha": protect_alpha,
        "n_mature":       n_mature,
        "since_date":     since_date,
        "band":           band,
    }
