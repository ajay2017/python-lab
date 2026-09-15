"""
Beta-repair lever arithmetic.

Pure functions only — no Streamlit, no DB calls, no side effects. Answers the
question the beta card's old fixed 8-10% "add a defensive sector" advice never
actually solved: which LEVER (trim to cash / swap into a lower-beta name / add
new defensive dollars) removes the most portfolio beta per dollar moved, and
how many dollars does each lever actually need to reach
`PORTFOLIO_BETA_ELEVATED`.

Found 2026-09-15 while auditing the "Find Defensive Recommendations" panel:
the shipped rec claimed an 8-10% cash-funded add reaches target beta 1.3. On
a representative $24,500 book at beta 1.88, a 10% cash add at beta 0.60 only
reaches 1.7636 -- reaching 1.3 that way needs ~$20,300 (83% of the book), not
8-10%. Meanwhile a SWAP (sell the highest-beta contributor, buy a low-beta
name with the proceeds) needs only ~$4,000 (16% of the book) for the same
target, because it doesn't grow the denominator the way a cash-funded add
does. No swap model existed anywhere in the codebase before this module.

Every "dollars_to_target_*" function returns None -- never inf, never a huge
number -- when the lever is structurally incapable of reaching the target at
ANY size (e.g. a candidate/position beta that is already on the wrong side of
the target). None here means "unreachable by this lever", not "unknown" --
callers distinguish the two by checking the OTHER inputs (current_beta,
current_value, etc.) for None first, per the house None-vs-empty sentinel
contract (see CLAUDE.md's coordination-pattern section).

`expected_beta_after_trim` generalizes the inline 50%-of-top-contributor trim
formula that lived in risk_advisor.py's beta rec (the "(beta - w*b*f)/(1-w*f)"
comment) -- that call site now imports this function rather than forking the
arithmetic a second time. `expected_beta_after_add` (the additive counterpart)
already lives in portfolio.py and is deliberately NOT imported here -- this
module's own `dollars_to_target_add` is an independent closed-form INVERSE of
that formula (solved for dollars rather than for beta), kept out of
portfolio.py's import graph on purpose (portfolio.py is a _GATE_FILES
member -- see tests/test_beta_repair.py::test_module_does_not_import_gate_files).
The two are proven to agree by tests/test_beta_repair.py's composition test
(swap == trim-then-add, cross-checked against portfolio.expected_beta_after_add),
which is what actually keeps them in sync -- not a shared function call.

`leverage_side_effect` (added for the margin/leverage disclosure) answers a
DIFFERENT question the audit also found missing entirely: does a lever's
absolute market-exposure-to-equity ratio get better or worse, independent of
whether its beta number improves. On the real book (beta-dollars
1.88x$24,500=$46,060 against ~$7,802 equity, ratio ~5.90x), a margin-funded
ADD improves the beta RATIO (1.88 -> 1.76) while the exposure/equity ratio
gets WORSE (5.90x -> ~6.08x) -- a trade is equity-neutral at execution (basic
double-entry: buying/selling moves value between stock and cash/debit
without changing equity itself), so ANY add grows gross book against a fixed
equity, and ANY trim/swap shrinks or holds it -- see the function's own
"_LEVER_GROSS_BOOK_DELTA_SIGN" comment for the accounting reasoning. Never
computes a ratio when the account's leverage state is unmeasured (basis
"unlevered"/"stale") or in a margin call ("called") -- those three
non-`measured` states carry no fabricated `direction`.
"""
from __future__ import annotations

import pandas as pd


def _pos_float(val) -> float | None:
    """Safe float coercion -- None for None/NaN/non-numeric; does NOT reject
    negative or zero (callers apply their own sign/positivity checks, since
    "zero" is a valid weight/beta in some callers but not others)."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN check


def expected_beta_after_trim(
    *,
    current_beta: float | None,
    book_fraction_sold: float | None,
    position_beta: float | None,
) -> float | None:
    """Portfolio beta after selling a fraction of the book from one position,
    with proceeds converted to cash (beta 0) rather than reinvested.

    `book_fraction_sold` is the fraction of the WHOLE portfolio's value being
    liquidated -- i.e. (this position's portfolio weight) x (fraction of the
    position itself being sold), matching risk_advisor.py's `_tw * _tf` term.
    `position_beta` is the beta of the position being trimmed.

    Formula: (B - f*b) / (1 - f), where f = book_fraction_sold, b = position_beta.
    This is risk_advisor.py's original inline trim math, generalized: the
    caller no longer hardcodes "sell 50% of the top contributor" -- it can
    ask "what if I sold X% of the book from this position instead."

    Returns None for any None/NaN input, `book_fraction_sold` outside [0, 1),
    or a fraction so close to 1 that the remaining book is functionally empty
    (mirrors risk_advisor.py's own `w_i * f > 0.999` guard). Never divides by
    a zero/negative remaining fraction.
    """
    b  = _pos_float(current_beta)
    f  = _pos_float(book_fraction_sold)
    pb = _pos_float(position_beta)
    if b is None or f is None or pb is None:
        return None
    if f < 0 or f > 0.999:
        return None
    return round((b - f * pb) / (1 - f), 2)


def expected_beta_after_swap(
    *,
    current_beta: float | None,
    current_value: float | None,
    swap_dollars: float | None,
    from_beta: float | None,
    to_beta: float | None,
) -> float | None:
    """Portfolio beta after selling `swap_dollars` of a `from_beta` position
    and buying the SAME dollar amount of a `to_beta` candidate -- a
    constant-notional swap, so the portfolio's total value (the denominator)
    never grows or shrinks, unlike a cash-funded add.

    Formula: B + (to_beta - from_beta) * swap_dollars / current_value.

    Returns None when any input is None/NaN, `current_value` <= 0,
    `swap_dollars` < 0, or `swap_dollars` > `current_value` (can't swap more
    than the book is worth).
    """
    b   = _pos_float(current_beta)
    v   = _pos_float(current_value)
    s   = _pos_float(swap_dollars)
    fb  = _pos_float(from_beta)
    tb  = _pos_float(to_beta)
    if b is None or v is None or s is None or fb is None or tb is None:
        return None
    if v <= 0 or s < 0 or s > v:
        return None
    return round(b + (tb - fb) * s / v, 2)


def dollars_to_target_add(
    *,
    current_beta: float | None,
    current_value: float | None,
    target: float | None,
    candidate_beta: float | None,
) -> float | None:
    """Dollars of a `candidate_beta` cash-funded ADD needed to dilute the book
    from `current_beta` to `target`.

    Derived from `portfolio.expected_beta_after_add`'s formula solved for the
    add amount: A = V*(B-T) / (T-bc). Requires `candidate_beta` < `target` --
    otherwise adding more of it can never pull a beta ABOVE target down to it
    at any size (the denominator T-bc is <= 0), and this returns None rather
    than a negative or infinite dollar figure.

    Returns None for any None/NaN input, `current_value` <= 0, or
    `current_beta` <= `target` (nothing to fix).
    """
    b  = _pos_float(current_beta)
    v  = _pos_float(current_value)
    t  = _pos_float(target)
    bc = _pos_float(candidate_beta)
    if b is None or v is None or t is None or bc is None:
        return None
    if v <= 0 or b <= t or bc >= t:
        return None
    return round(v * (b - t) / (t - bc), 2)


def dollars_to_target_swap(
    *,
    current_beta: float | None,
    current_value: float | None,
    target: float | None,
    from_beta: float | None,
    to_beta: float | None,
) -> float | None:
    """Dollars to swap from a `from_beta` position into a `to_beta` candidate
    (constant notional) needed to bring the book from `current_beta` to
    `target`.

    Derived from `expected_beta_after_swap` solved for swap_dollars:
    S = V*(B-T) / (from_beta-to_beta). Requires `from_beta` > `to_beta` --
    otherwise the swap moves beta the wrong way and can never reach a LOWER
    target, so this returns None rather than a negative dollar figure.

    Returns None for any None/NaN input, `current_value` <= 0, or
    `current_beta` <= `target` (nothing to fix).
    """
    b  = _pos_float(current_beta)
    v  = _pos_float(current_value)
    t  = _pos_float(target)
    fb = _pos_float(from_beta)
    tb = _pos_float(to_beta)
    if b is None or v is None or t is None or fb is None or tb is None:
        return None
    if v <= 0 or b <= t or fb <= tb:
        return None
    return round(v * (b - t) / (fb - tb), 2)


def dollars_to_target_trim(
    *,
    current_beta: float | None,
    current_value: float | None,
    target: float | None,
    position_beta: float | None,
) -> float | None:
    """Dollars to trim (sell to cash) from a `position_beta` holding needed to
    bring the book from `current_beta` to `target`.

    Derived from `expected_beta_after_trim` solved for the dollar amount sold:
    D = V*(B-T) / (position_beta-T). Requires `position_beta` > `target` --
    trimming a position whose OWN beta is already at or below the target can
    never pull the book's beta down to that target by selling more of it, so
    this returns None rather than a negative dollar figure.

    Returns None for any None/NaN input, `current_value` <= 0, or
    `current_beta` <= `target` (nothing to fix).
    """
    b  = _pos_float(current_beta)
    v  = _pos_float(current_value)
    t  = _pos_float(target)
    pb = _pos_float(position_beta)
    if b is None or v is None or t is None or pb is None:
        return None
    if v <= 0 or b <= t or pb <= t:
        return None
    return round(v * (b - t) / (pb - t), 2)


def _to_tz_naive(s: pd.Series) -> pd.Series:
    """Drop timezone from a datetime-indexed Series so two series built from
    different providers (tz-aware vs tz-naive) can align on their dates.
    Duplicated from portfolio._to_tz_naive (a private helper, not a public
    contract) rather than imported, to keep this module's import graph free
    of portfolio.py -- guarded by
    tests/test_beta_repair.py::test_module_does_not_import_gate_files."""
    out = s.dropna().copy()
    idx = out.index
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        out.index = idx.tz_localize(None)
    return out


def aligned_beta(
    *,
    candidate_close: "pd.Series | None",
    spy_close: "pd.Series | None",
    on_index: "pd.Index | pd.Series | None",
    min_obs: int = 20,
) -> tuple[float | None, int]:
    """Candidate beta vs SPY, regressed on the SAME date index the BOOK's own
    portfolio-return series was built on -- not just the candidate's own full
    overlap with SPY.

    Why this exists: portfolio beta (risk.compute_portfolio_risk_metrics)
    regresses a listwise intersection across every holding's history, so one
    short/degraded holding can truncate the window for the WHOLE book. A
    candidate's beta computed against its own full 6mo overlap with SPY can
    therefore share the same SPY window as the book's beta but NOT the same
    stock-side window -- comparing them directly mixes two different
    lookback periods. This function fixes that: it discards any date not in
    `on_index` (the book's own return-series index) before regressing, so
    every beta compared in one lever ranking shares one window.

    Parameters
    ----------
    candidate_close : the candidate's daily Close price Series.
    spy_close        : SPY's daily Close price Series over the same raw range.
    on_index         : the date index to restrict BOTH return series to
                       before regressing -- pass the book's own port_returns
                       index (e.g. from portfolio.portfolio_return_series).
    min_obs          : minimum overlapping observations required after
                       restricting to `on_index`, mirroring risk.beta_vs_market's
                       own floor. Fewer -> (None, n) rather than a noisy beta.

    Returns (beta, n_obs). `n_obs` is always the actual overlap count found,
    even when beta is None, so a caller/test can distinguish "no overlap at
    all" from "some overlap, still below the floor". Pure / no I/O.
    """
    if candidate_close is None or spy_close is None or on_index is None:
        return None, 0
    try:
        cand_ret = _to_tz_naive(pd.Series(candidate_close)).pct_change().dropna()
        spy_ret  = _to_tz_naive(pd.Series(spy_close)).pct_change().dropna()

        # Index-only tz normalization -- deliberately NOT routed through
        # _to_tz_naive, which calls .dropna() on its input Series; an
        # all-NaN-valued Series built purely to carry an index would have
        # every row dropped, silently emptying the index to nothing.
        idx = pd.Index(on_index)
        if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
            idx = idx.tz_localize(None)

        joined = pd.concat([cand_ret, spy_ret], axis=1, join="inner").dropna()
        joined = joined[joined.index.isin(idx)]
        n_obs  = len(joined)
        if n_obs < max(2, min_obs):
            return None, n_obs

        cand_r = joined.iloc[:, 0]
        spy_r  = joined.iloc[:, 1]
        mkt_var = float(spy_r.var())
        if mkt_var == 0 or mkt_var != mkt_var:
            return None, n_obs
        cov_val = float(cand_r.cov(spy_r))
        if cov_val != cov_val:
            return None, n_obs
        return round(cov_val / mkt_var, 2), n_obs
    except Exception:
        return None, 0


# Kinds recognized by leverage_side_effect -- whether a lever shrinks, holds
# constant, or grows the book's gross market value. A single BUY/SELL is
# equity-neutral at execution (basic double-entry accounting: selling $X of
# stock converts $X of exposure into $X of cash/paid-down debit, buying does
# the reverse -- net_capital is unchanged by the trade itself, only by
# subsequent price moves). So the direction leverage_side_effect reports is
# driven entirely by whether GROSS_BOOK grows relative to a constant equity,
# not by "how the purchase is funded" -- a cash-funded add and a margin-funded
# add have the identical mechanical effect on the exposure/equity ratio; the
# only thing "funding source" actually determines is whether cash_balance was
# already negative (levered) going in, which `basis` already captures.
_LEVER_GROSS_BOOK_DELTA_SIGN = {"trim": -1, "swap": 0, "add": +1}


def leverage_side_effect(
    *,
    kind: str,
    dollars: float | None,
    current_beta: float | None,
    new_beta: float | None,
    gross_book: float | None,
    net_capital: float | None,
    basis: str | None,
) -> dict:
    """Margin/leverage side-effect of a beta lever -- the awareness the old
    beta rec never carried at all (claim 7 of the original audit): a
    margin-funded ADD can make the beta RATIO look better while absolute
    market exposure against a fixed equity cushion actually gets WORSE.

    `net_capital` and `basis` are expected to come straight from
    `margin.resolve_net_capital(gross_book, account_cash_rec, stale_days_limit, now)`
    -- this function does not call it itself (no DB/session access; stays
    pure) and does not read `st.session_state["_leverage_cache"]`, whose
    "equity" key is actually the gross book, not equity (a documented
    footgun) -- the caller must resolve net_capital independently and pass
    it through explicitly.

    Returns a dict with an explicit "state", one of FOUR -- never collapsing
    "no debt" into "can't tell" or vice versa (the house None-vs-empty
    sentinel discipline, applied to a leverage read):
      "not_levered" (basis == "unlevered")  -- no debit on file. A true,
          complete answer: this lever carries no leverage concern to
          disclose. No numbers computed (resolve_net_capital deliberately
          withholds a net_capital figure in this case).
      "stale"       (basis == "stale")      -- a debit EXISTS but the figure
          on file is too old to trust. Genuinely different from
          "not_levered" -- there IS something to measure, it just can't be
          measured reliably right now. Distinct copy required at the caller.
      "called"      (basis == "called", net_capital <= 0) -- the worst state
          a levered book can be in. For kind="trim", this does NOT withhold
          the lever -- trimming reduces exposure, which is exactly the right
          move when called, so the state is disclosed as urgency, not
          suppressed. For kind="add"/"swap" (not yet wired -- Phase 3), a
          caller should treat "called" as a reason to withhold the ADD leg
          specifically, since adding new exposure while called is the wrong
          direction; that policy lives in the caller, not here.
      "measured"    (basis == "levered")    -- real numbers: beta-dollars and
          the exposure/equity ratio before and after, plus a "direction" in
          {"better","worse","flat"}. `direction` is returned ONLY in this
          state -- a direction attached to unmeasured data would be the
          fabricated-neutral bug this house has already shipped and fixed
          twice (feedback_none_sentinel_meets_pandas /
          feedback_sentinel_is_present).

    Any missing/NaN numeric input while basis == "levered" degrades to
    "stale" rather than raising or fabricating a ratio -- the basis claims
    measurable data exists, but if the specific numbers needed are absent,
    this function still refuses to compute rather than guess.
    """
    if kind not in _LEVER_GROSS_BOOK_DELTA_SIGN:
        return {"state": "stale", "kind": kind}
    if basis == "unlevered":
        return {"state": "not_levered", "kind": kind}
    if basis == "stale":
        return {"state": "stale", "kind": kind}
    if basis == "called":
        return {"state": "called", "kind": kind}
    if basis != "levered":
        return {"state": "stale", "kind": kind}

    _d  = _pos_float(dollars)
    _cb = _pos_float(current_beta)
    _nb = _pos_float(new_beta)
    _gb = _pos_float(gross_book)
    _nc = _pos_float(net_capital)
    if _d is None or _cb is None or _nb is None or _gb is None or _nc is None:
        return {"state": "stale", "kind": kind}
    if _gb <= 0 or _nc <= 0 or _d < 0:
        return {"state": "stale", "kind": kind}

    _sign = _LEVER_GROSS_BOOK_DELTA_SIGN[kind]
    _gb_after = _gb + _sign * _d
    if _gb_after < 0:
        return {"state": "stale", "kind": kind}

    _beta_dollars_before = round(_cb * _gb, 2)
    _beta_dollars_after  = round(_nb * _gb_after, 2)
    _ratio_before = round(_beta_dollars_before / _nc, 2)
    _ratio_after  = round(_beta_dollars_after / _nc, 2)
    if _ratio_after < _ratio_before:
        _direction = "better"
    elif _ratio_after > _ratio_before:
        _direction = "worse"
    else:
        _direction = "flat"

    return {
        "state":               "measured",
        "kind":                kind,
        "beta_dollars_before": _beta_dollars_before,
        "beta_dollars_after":  _beta_dollars_after,
        "ratio_before":        _ratio_before,
        "ratio_after":         _ratio_after,
        "direction":           _direction,
    }
