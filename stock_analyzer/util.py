"""Small cross-cutting helpers that make the safe idiom the default path.

Each closes a bug-class the 2026-07-29 / 2026-08-04 audits kept re-finding
because the correct idiom was opt-in and hand-remembered. Additive: this ships
the helpers; call sites migrate onto them incrementally (a rewire that touches a
gate/decision path gets its own review).
"""
from __future__ import annotations

import html as _html
import numbers
import re as _re
from typing import Any


def get_or_offline(container: dict | None, key: str) -> Any:
    """Offline-preserving cache read — the safe replacement for
    ``container.get(key) or []`` / ``or {}``.

    The project's offline convention: a producer stores ``None`` (not an empty
    ``[]``/``{}``) when it *could not compute* a value ("offline"), versus an
    empty container when it computed and legitimately found nothing ("checked,
    empty"). The idiom ``container.get(key) or []`` destroys that distinction —
    an offline ``None`` is silently rewritten to a checked-empty default, which
    disables the downstream gate without any offline banner. That was the single
    most-repeated finding in the 2026-08-04 audit (hit by 3 of 9 review passes).

    This returns ``None`` (the offline sentinel) when the container is absent,
    the key is missing, or the stored value is ``None`` — and passes any real
    value through unchanged, *including* a legitimately-empty ``[]``/``{}``.
    Branch on ``is None`` at the call site to show an offline banner / keep the
    gate active::

        recs = get_or_offline(st.session_state, "_risk_advisor_recs_cache")
        if recs is None:
            _render_offline_banner()      # couldn't compute — do NOT clear gates
        else:
            for rec in recs:              # [] here means genuinely no risk
                ...
    """
    if container is None:
        return None
    return container.get(key)


def numeric_or(value: Any, default: float) -> float:
    """Numeric read that preserves a legitimate ``0.0`` — the safe replacement
    for ``x or 50`` / ``float(x or 0)``.

    Sibling of :func:`get_or_offline`: same falsy-collapse bug-class, different
    type. ``x or default`` cannot distinguish "absent" from "measured, and the
    measurement was zero" — and for a 0-100 pillar score those are opposite
    claims. Zero is the most bearish reading there is; rewriting it to a neutral
    50 inverts it, and does so precisely on the names that most deserve the
    warning.

    Found 2026-09-13 at ``app.py``'s "What would change this signal?" block,
    where the collapse was not cosmetic: that code subtracts
    ``pillar_score * weight`` from the REAL composite to derive what the other
    pillars contribute, so a fabricated 50 understated that term by
    ``50 * weight`` and overstated the required target by 50 — printing
    "Technical: 50 -> 70" when the truth was "Technical: 0 -> 20". An inflated
    target can also exceed the block's own ``<= 100`` guard, silently dropping
    the one actionable pillar from the advice entirely.

    ``NaN`` and infinities are rejected too, which ``or`` cannot do:
    ``float('nan')`` is truthy, so it passes straight through and renders as
    "nan". ``bool`` is excluded deliberately — it is an ``int`` subclass, so
    ``True`` would otherwise become ``1.0``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):   # NaN / ±inf
        return float(default)
    return v


def finite_or_none(value: Any) -> float | None:
    """``float(value)`` when it is a finite real number, else ``None``.

    Pandas turns a stored ``None`` into ``NaN`` when rows pass through a
    DataFrame, so ``is not None`` checks pass and ``float(nan)`` prints "nan".
    This rejects NaN/±inf (also parsed from strings), ``bool``, empty or
    unparseable strings and any non-numeric type. Zero is preserved. Never raises.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            v = float(value.strip())
        except ValueError:
            return None
    elif isinstance(value, numbers.Real):
        try:
            v = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
    else:
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def bq_score_or_none(value: Any, bundle: dict | None) -> Any:
    """``value`` unless the bundle says Business Quality was unmeasurable —
    in which case ``value`` is `fundamentals.py`'s fabricated neutral 50, and
    persisting it would record a reading that never happened (finding D24).

    Companion to :func:`pillar_tile`, which withholds the same fabrication
    from a RENDER; this withholds it from a WRITE. Honours the same
    fail-open flag lookup as the five existing consumers of these flags
    (``.get(key, True)``, checking the legacy ``fundamentals_available``
    alias), so a legacy-shaped bundle is treated as trustworthy, not gated.
    """
    b = bundle or {}
    available = b.get("bq_available", b.get("fundamentals_available", True))
    return value if available else None


def val_score_or_none(value: Any, bundle: dict | None) -> Any:
    """Same contract as :func:`bq_score_or_none`, for the Valuation pillar."""
    b = bundle or {}
    return value if b.get("val_available", True) else None


def t_score_or_none(value: Any, bundle: dict | None) -> Any:
    """Same contract as :func:`bq_score_or_none`, for the Technical pillar.

    ``value`` unless the bundle says the technical pillar was unmeasured
    (``t_available`` False — too few bars or a required leg missing; see
    ``technicals.technical_coverage``). Fail-open: a legacy/narrowed bundle
    without the key is treated as measured.
    """
    b = bundle or {}
    return value if b.get("t_available", True) else None


def sentiment_value_or_none(value: Any, bundle: dict | None) -> Any:
    """Same contract as :func:`bq_score_or_none`, for ``s_score``/``avg_sent``.

    No ``s_available`` flag exists in the bundle yet (that gap is a separate,
    larger finding — D3) — so availability here is derived from whether any
    headline was actually scored: ``analyze_news([])`` returns avg ``0.0``,
    and ``sentiment_score_0_100(0.0)`` is *exactly* 50.0 — a fabricated
    neutral indistinguishable from a real one without this check.

    Priority order, checked in this sequence:
      (a) ``sentiment_available`` if present — a caller that has already
          narrowed a bundle down to a smaller footprint (to avoid retaining a
          full headline list) may carry this precomputed bool instead; it
          wins over ``headlines`` so narrowing never has to re-carry the list
          just to answer this question.
      (b) ``headlines`` if present — derives availability from whether any
          headline was actually scored.
      (c) neither present — fails OPEN (treats the value as measured),
          matching :func:`bq_score_or_none`/:func:`val_score_or_none`'s own
          ``.get(key, True)`` convention for a legacy/partial bundle shape. A
          real scored bundle always carries ``headlines`` (even as ``[]``,
          meaning "scored, zero found" — which correctly resolves to
          unavailable via (b)), so (c) only matters for a hand-built or
          future caller that omits the key entirely; failing open there
          avoids silently nulling a real score because of a shape it was
          never asked to carry.
    """
    b = bundle or {}
    if "sentiment_available" in b:
        available = bool(b["sentiment_available"])
    elif "headlines" in b:
        available = bool(b["headlines"])
    else:
        available = True
    return value if available else None


_DROPPED_HOLDING_REASON_LABELS = {
    "invalid_shares_or_cost": "invalid shares or cost basis — check the entry",
    "no_price_data": (
        "no price data from any provider — a data-provider issue, not your "
        "entry; signals & scores are unavailable for these too until it clears"
    ),
}


def dropped_holdings_banner_text(dropped: list[dict] | None) -> str | None:
    """User-facing message for `portfolio.build_portfolio_df`'s `dropped`
    list (finding D4), or ``None`` when there is nothing to report.

    A holding is dropped from `port_df` for one of two genuinely different
    reasons, which must not be conflated: ``invalid_shares_or_cost`` (garbage
    entered in Shares/Avg Cost) is a DATA-ENTRY problem the user can fix by
    editing the holding; ``no_price_data`` (every provider failed to price an
    otherwise-valid entry) is a DATA-PROVIDER problem with nothing wrong in
    the entry at all. Telling the user to "check the entry" for a name whose
    entry is fine would send them looking for a bug that isn't there — which
    is exactly what both `app.py` call sites did before this existed, since
    they hardcoded "invalid shares or cost basis" unconditionally for every
    row regardless of why it was actually dropped.

    A row missing the ``reason`` key (an older cache built before this field
    existed) is treated as ``invalid_shares_or_cost`` — the ONLY reason a
    drop could have meant before D4, so this is a correct backward-compat
    default, not a guess.

    Single source of truth for both `app.py` consumers, which previously
    duplicated the same hardcoded string — a second drop reason could easily
    have been added to one site and not the other.
    """
    d = dropped or []
    if not d:
        return None
    by_reason: dict[str, list[str]] = {}
    for row in d:
        reason = row.get("reason") or "invalid_shares_or_cost"
        by_reason.setdefault(reason, []).append(str(row.get("ticker", "?")))
    parts = [
        f"{', '.join(tickers)} ({_DROPPED_HOLDING_REASON_LABELS.get(reason, reason)})"
        for reason, tickers in by_reason.items()
    ]
    n = len(d)
    return (
        f"⚠️ {n} holding{'s' if n != 1 else ''} skipped from the portfolio view: "
        + "; ".join(parts) + "."
    )


def _row_missing(row: dict) -> list:
    """`row["missing"]` as a list; an absent/None/non-list value (legacy
    entry) reads as "no specific label", never an error."""
    m = row.get("missing")
    return m if isinstance(m, (list, tuple)) else []


def score_withheld_banner_text(withheld: list[dict] | None) -> str | None:
    """User-facing message for `portfolio.build_portfolio_df`'s `score_withheld`
    list, or ``None`` when there is nothing to report.

    Companion to `dropped_holdings_banner_text` above, but a genuinely
    different situation: a `score_withheld` holding is NOT missing from the
    portfolio view (price/weight/stops/risk metrics all still apply) — only
    its composite Score/Signal are withheld, because `fundamentals_available`/
    `val_available` were False for it (any provider, e.g. a fund/ETF with no
    fundamentals to score, or a stock mid-data-outage — never claim it's
    EXCLUSIVELY funds, since a stock outage hits this same path). Calm,
    informational tone (not the dropped-holdings warning tone) since nothing
    is actually broken or missing here — matches the withhold already shown
    on the Analysis page (quick_research.py) for the same reason.

    Entries may carry `"missing"` (list of pillar labels from
    `pillar_availability.unmeasured_pillars`) and `"t_bars"` (price-history
    sessions seen). Tickers are grouped by CAUSE so each sentence is true for
    every ticker it names:
      * fundamentals/valuation (or a legacy entry with no `"missing"`): the
        original wording; when nothing else is withheld the whole message is
        the original sentence exactly, including "Price, weight, stops and
        risk metrics still apply".
      * Technical missing with too little history (`t_bars` below
        `TECHNICAL_MIN_BARS`, or unknown): the price history is too short, so
        the trend-break exit ladder (which needs a 50-bar SMA) cannot assess
        it either and only its stop and concentration limits protect it. The
        generic "stops and risk metrics still apply" reassurance is NOT used.
        With `"stop_state"` on the entry (`young_holding_stop.stop_state`) the
        stop claim follows the real state: "auto" (and a legacy entry with no
        `stop_state`) keeps this wording exactly; "manual" names the owner's
        stop (`"manual_stop"`, dollar signs escaped for markdown: the text renders as markdown);
        "none" says no stop exists (a new listing under `HELD_STOP_MIN_BARS`
        sessions) and points to the stop warning on Home.
      * Technical missing with `t_bars` at/above the floor: a required reading
        could not be computed from recent prices (e.g. a flat or halted
        quote). No "too short" claim and no ladder caveat — the ladder CAN run
        there.
    Singular/plural grammar follows the ticker count. Rendered through
    `st.info` (markdown): no bold or links, but a manual-stop price carries
    `\\$` so it never renders as LaTeX.
    """
    d = withheld or []
    if not d:
        return None
    tickers = [str(row.get("ticker")) for row in d if row.get("ticker")]
    if not tickers:
        return None
    n = len(tickers)
    tech_rows = [
        row for row in d
        if row.get("ticker") and "Technical" in _row_missing(row)
    ]
    if tech_rows:
        from stock_analyzer.constants import TECHNICAL_MIN_BARS
        from stock_analyzer.pillar_availability import technical_history_short
        # Anything not purely technical (incl. legacy/empty `missing`).
        other_rows = [
            row for row in d
            if row.get("ticker")
            and (not _row_missing(row)
                 or any(m != "Technical" for m in _row_missing(row)))
        ]
        # Group technical rows by CAUSE so each sentence is true for every
        # ticker it names: too little history (the exit ladder, which needs a
        # 50-bar SMA, truly cannot assess it) vs a >= floor history where a
        # required reading was not computable (the ladder CAN run).
        short_rows = [r for r in tech_rows if technical_history_short(r.get("t_bars"))]
        uncomp_rows = [r for r in tech_rows if not technical_history_short(r.get("t_bars"))]
        sentences: list[str] = []
        if other_rows:
            sentences.append(
                f"{', '.join(str(r['ticker']) for r in other_rows)}: company "
                "fundamentals aren't available from any provider (e.g. "
                "funds/ETFs), so a composite score would be a guess rather than "
                "a measurement — it's withheld here too, matching the Analysis page."
            )
        if short_rows:
            # Group by stop state so each sentence is true for every ticker it
            # names: "auto" (a stop exists; today's wording, also a legacy entry
            # with no `stop_state`), "manual" (only the owner's stop), "none" (no
            # stop at all; a too-few-bars reason or another cause).
            def _short_span(rows_: list[dict]) -> str:
                bars: dict[str, int] = {}
                for row in rows_:
                    try:
                        b = int(row.get("t_bars"))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if b >= 0:
                        bars[str(row["ticker"])] = b
                names_ = [str(r["ticker"]) for r in rows_]
                if not bars:
                    return f"{TECHNICAL_MIN_BARS} sessions needed"
                if len(set(bars.values())) == 1 and len(bars) == len(names_):
                    return f"{next(iter(bars.values()))} sessions; {TECHNICAL_MIN_BARS} needed"
                per = ", ".join(f"{t} {b} sessions" for t, b in bars.items())
                return f"{per}; {TECHNICAL_MIN_BARS} needed"

            def _short_lead(rows_: list[dict]) -> str:
                names_ = [str(r["ticker"]) for r in rows_]
                one_ = len(names_) == 1
                return (
                    f"{', '.join(names_)}: price history too short ({_short_span(rows_)}) to "
                    "measure the technical trend, so the "
                    + ("composite is" if one_ else "composites are")
                    + " withheld, and the trend-break exit ladder can't assess "
                    + ("it" if one_ else "them") + " either")

            def _state(r: dict) -> str:
                return r.get("stop_state") if r.get("stop_state") in ("manual", "none") else "auto"

            auto_rows = [r for r in short_rows if _state(r) == "auto"]
            manual_rows = [r for r in short_rows if _state(r) == "manual"]
            none_rows = [r for r in short_rows if _state(r) == "none"]
            if auto_rows:
                one = len(auto_rows) == 1
                sentences.append(
                    _short_lead(auto_rows) + " — only "
                    + ("its" if one else "their")
                    + " stop and concentration limits protect "
                    + ("it." if one else "them.")
                )
            if manual_rows:
                one = len(manual_rows) == 1
                prices = []
                for r in manual_rows:
                    try:
                        prices.append((str(r["ticker"]), "\\$" + f"{float(r.get('manual_stop')):.2f}"))
                    except (TypeError, ValueError):
                        prices.append((str(r["ticker"]), None))
                if one:
                    mine = ("your manual stop (" + prices[0][1] + ")") if prices[0][1] else "your manual stop"
                else:
                    mine = "your manual stops (" + ", ".join(
                        f"{t} {p}" if p else t for t, p in prices) + ")"
                sentences.append(
                    _short_lead(manual_rows) + f" — only {mine} and concentration limits protect "
                    + ("it." if one else "them.")
                )
            if none_rows:
                from stock_analyzer.constants import HELD_STOP_MIN_BARS
                # a too-few-bars cause says "yet" and the floor; any other cause
                # (a failed build, no price history) does not claim a floor
                for _short_cause in (True, False):
                    grp = [r for r in none_rows
                           if (r.get("stop_reason") == "short_history") == _short_cause]
                    if not grp:
                        continue
                    one = len(grp) == 1
                    no_stop = (f"No stop can be built yet ({HELD_STOP_MIN_BARS} sessions needed)"
                               if _short_cause else
                               "No stop could be built for " + ("it" if one else "them"))
                    sentences.append(
                        _short_lead(grp) + f". {no_stop}, so only concentration limits protect "
                        + ("it" if one else "them")
                        + (": set your own stop under the stop warning on 🏠 Home, or place one at your broker."
                           if one else
                           ": set your own stops under the stop warning on 🏠 Home, or place them at your broker.")
                    )
        if uncomp_rows:
            names = [str(r["ticker"]) for r in uncomp_rows]
            sentences.append(
                f"{', '.join(names)}: a required technical reading couldn't be "
                "computed from recent prices (e.g. a flat or halted quote), so the "
                + ("composite is" if len(names) == 1 else "composites are")
                + " withheld."
            )
        head = f"ℹ️ {n} holding{'s' if n != 1 else ''} shown without a composite score. "
        return head + " ".join(sentences)
    stopless = [str(r["ticker"]) for r in d
                if r.get("ticker") and r.get("stop_state") == "none"]
    if not stopless:
        applies = "Price, weight, stops and risk metrics still apply."
    elif len(stopless) == len(tickers):
        applies = ("Price, weight and risk metrics still apply; no stop could be built for "
                   + ("it" if n == 1 else "them") + " yet (see the stop warning).")
    else:
        applies = ("Price, weight, stops and risk metrics still apply, except that no stop "
                   f"could be built yet for {', '.join(stopless)} (see the stop warning).")
    return (
        f"ℹ️ {n} holding{'s' if n != 1 else ''} shown without a composite score: "
        f"{', '.join(tickers)}. Company fundamentals aren't available for these "
        "from any provider (e.g. funds/ETFs), so a composite score would be a "
        "guess rather than a measurement — it's withheld here too, matching the "
        "Analysis page. " + applies
    )


def holdings_write_failed_message(ticker: str) -> str:
    """User-facing message for when a post-trade `db.save_holdings()` call
    returns False (finding D2).

    By the point this fires, the underlying trade itself has ALREADY been
    logged successfully (the BUY/SELL confirm flow checks `db.save_trade`'s
    own return before ever reaching the holdings update) — only the derived
    holdings AGGREGATE failed to persist. That is recoverable ("Rebuild from
    trades" replays the trade log from scratch), so this is a real but
    bounded failure, not data loss.

    Callers must pair this with two things this function does not do itself:
    (1) render it via `st.error`, never `st.success` — a write that did not
    happen must never be told to the user as having happened, which is the
    defect this closes; and (2) leave `st.session_state.holdings_df`
    UNCHANGED on this path. Updating it to the unsaved value anyway would run
    the rest of THIS session on a book the DB never received — a worse,
    quieter version of the same defect, since every gate/stop/sizing
    computation reads session_state, not the DB, until the next reload
    silently reverts it.
    """
    return (
        f"⚠️ Your trade for **{ticker}** was logged, but the holdings table "
        f"failed to update — its share count may be out of sync until this is "
        f"corrected. Use **'🔄 Rebuild from trades'** (Trade Journal) to fix it."
    )


def journal_read_failed_msg(context: str) -> str:
    """User-facing message for a trade-journal write that succeeded but whose
    follow-up re-read of the journal failed (HoldWipe). Holdings are NOT
    rebuilt from an unreadable journal — an empty replay would sweep every
    holdings row and every manual stop — so both are left unchanged.
    `context` is the lead clause, e.g. "Trade(s) deleted". No `$`, no `**`.
    """
    return (
        f"⚠️ {context}, but the trade journal couldn't be re-read, so holdings "
        "were NOT rebuilt (your holdings and manual stops are unchanged). "
        "Open 📒 Trade Journal → ⚠️ Fix drift between trades and holdings → "
        "Run rebuild now once the database is reachable."
    )


def xcheck_is_alarm_worthy(result: dict, market_is_open: bool) -> bool:
    """Whether one price cross-check result should trigger the loud "sources
    disagree" banner, vs staying quiet (finding D23).

    A settled prev-close disagreement is ALWAYS alarm-worthy — a real,
    settled value diverging across independent sources is a genuine
    data-integrity fault (missed split, wrong-symbol mapping, a poisoned
    feed) regardless of the hour.

    A LIVE-price-only disagreement is different. Outside regular trading
    hours the two sources may legitimately be quoting DIFFERENT THINGS — a
    real-time pre/post-market tick from one venue vs a delayed or stale
    last-regular-session read from the other — so a gap there is an
    artifact of comparing non-comparable reads, not a fault. Measured live
    (`docs/plans/data-integrity.md` D23): of the first 5 live-leg breaches
    recorded, 4 fired outside 09:30-16:00 ET (three at ~08:41 ET premarket,
    one at ~17:35 ET after-hours); the one that fired inside regular hours
    (09:42 ET) is exactly the case this still alarms on below, since a
    stale/wrong intraday price during the session is precisely what
    `DATA_XCHECK_LIVE_TOL_PCT` exists to catch.

    Display decision only — never changes what gets WRITTEN.
    `price_xcheck_history.ok` is persisted exactly as measured regardless of
    market hours, so the audit trail and any future health-check statistics
    stay interpretable on the real, un-suppressed signal.
    """
    if result.get("prev_ok") is False:
        return True
    if result.get("live_ok") is False:
        return bool(market_is_open)
    return False


def pillar_tile(
    name: str,
    score: Any,
    available: bool,
    inputs: str,
    unavailable_reason: str,
) -> tuple[str, str]:
    """``(header_markdown, caption)`` for one composite-pillar tile.

    A pillar whose inputs were unavailable returns a fabricated neutral 50 —
    `valuation.py` and `fundamentals.py` both do this by design, and
    `technicals.py` does it too (its flag is the bundle's ``t_available``, pass
    that as ``available`` for the Technical tile). Rendering that as
    "**Valuation — 50/100**" under a caption asserting "P/E · FCF Yield · PT
    Upside · Consensus" states two things that are not true: that a
    measurement happened, and that those inputs produced it. A measured 50 and
    a fabricated 50 must not render identically.

    So when ``available`` is false the number is WITHHELD rather than shown
    with a hedge, matching `quick_research.py`'s "❔ Verdict withheld"
    precedent — the house position is that a score nobody measured is guessing,
    not measuring, and the honest move is to decline to print it.

    The caller owns the fail-open default (``r.get("val_available", True)``),
    matching the five existing consumers of these flags; this function simply
    honours the boolean it is handed, so a falsy value always withholds.

    ``inputs`` is only returned when the tile is real, for the same reason: it
    is a claim about what was scored. The per-signal detail list rendered
    BELOW the tile is deliberately not this function's business — those are a
    record of what was captured, which stays true either way (the same call
    Phase B made for `val_signals` on a withheld valuation).
    """
    if available:
        return f"**{name} — {numeric_or(score, 50):.0f}/100**", inputs
    return f"**{name} — ❔ not measured**", unavailable_reason


# Distinct from "❔ Withheld"/"❔ not measured" (a transient provider-side
# data gap). This state means the pillar is STRUCTURALLY not part of this
# asset type's composite by design (a fund has no business-quality/
# valuation/sentiment leg -- see etf_scoring.py). Never render the two
# states with the same glyph or wording.
ETF_PILLAR_NA_SHORT = "➖ n/a for funds"
ETF_PILLAR_NA_REASON = (
    "Not part of a fund's score. This pillar measures an individual "
    "company; an ETF's verdict is built from its technical trend and "
    "expense ratio instead (see the Cost tile)."
)


def stop_recovery_state(
    live_gap_to_stop: float | None,
    margin_pct: float = 0.0,
) -> str:
    """Classify a stop_breach card's current live status.

    ``live_gap_to_stop`` is the Gap-to-Stop (%) from ``_port_df_enriched``,
    positive when price is above the stop, negative when below.

    Returns:
    - ``"recovered"``: live price is above stop by more than *margin_pct* —
      the breach has resolved and the card should be demoted to Review.
    - ``"active"``: live price is at or below stop + margin — still breached.
    - ``"unavailable"``: live price data is missing or non-finite. Must NEVER
      be treated as ``"active"`` at the render layer; show a neutral offline
      note instead.
    """
    if live_gap_to_stop is None:
        return "unavailable"
    try:
        g = float(live_gap_to_stop)
    except (TypeError, ValueError):
        return "unavailable"
    import math
    if not math.isfinite(g):
        return "unavailable"
    return "recovered" if g > margin_pct else "active"


def safe_html(value: Any) -> str:
    """HTML-escape a value for safe interpolation into an ``unsafe_allow_html``
    string.

    Wrap every externally-sourced field — news headlines, notes/thesis text,
    company names, analyst-coverage fields, debate transcripts, anything a user
    or a feed can influence — before it goes into an f-string rendered with
    ``unsafe_allow_html=True``. This project has patched the XSS class on at
    least seven separate surfaces (news 2026-05-27; notes/thesis 2026-06-28;
    Pre-Market Stance / debate transcripts / "Your thesis" / Analyst Coverage
    2026-07-29); escaping at the interpolation point is the durable, per-field
    fix rather than re-finding the next unescaped surface in an audit.

    ``quote=True`` also escapes quotes, so the result is safe inside an HTML
    attribute (e.g. ``title='{safe_html(x)}'``), not only in element text.
    """
    return _html.escape(str(value), quote=True)


def safe_text(value: Any) -> str:
    """Escape an untrusted feed/LLM TEXT field for ELEMENT CONTENT inside an
    ``unsafe_allow_html`` string — ``safe_html`` plus the markdown/LaTeX
    hazards ``safe_html`` leaves alone (the same rules ``safe_link`` applies to
    a title): whitespace runs collapse to one space, and ``$ [ ] \\ ```` are
    emitted as character references, so the text can neither render as LaTeX
    (``$300 ... $143``) nor form a markdown link at an inline site.

    Never interpolate inside an HTML attribute. ``scripts/check_antipatterns.py``
    credits a direct call to this function as fully escaped.
    """
    out = _html.escape(" ".join(str(value).split()), quote=True)
    for ch, ref in (("$", "&#36;"), ("[", "&#91;"), ("]", "&#93;"),
                    ("\\", "&#92;"), ("`", "&#96;")):
        out = out.replace(ch, ref)
    return out


def safe_link(url: Any, title: Any, max_len: int = 90,
              style: str = "color:#ddd;text-decoration:none") -> str:
    """Render an untrusted news headline as a safe anchor.

    Why: titles + URLs come from yfinance / RSS feeds and are interpolated into
    strings rendered with ``unsafe_allow_html=True``. Without escaping, a title
    can break out of the anchor (``</a><script>...``) and a quote in a URL can
    break out of the ``href`` attribute.

    Scheme rule: only ``http://`` / ``https://`` (case-insensitive, after
    ``.strip()``) becomes a link; anything else (``javascript:``, ``data:``,
    relative, empty, None) falls back to a plain ``<span>``.

    ``$`` rule: Streamlit's markdown treats ``$...$`` as LaTeX even with
    ``unsafe_allow_html=True`` (seen live 2026-10-19), so ``$`` in the title is
    emitted as ``&#36;``, which works inside an HTML block and inline.

    Markdown rule: Streamlit 1.57 renders markdown with an identity
    ``urlTransform``, so any text that reaches markdown parsing could become a
    live ``[x](javascript:...)`` link. The title therefore has every whitespace
    run collapsed to one space (no blank line can end the HTML block) and
    square brackets, backslash and backtick are emitted as character references, which never act as
    markdown syntax. A URL containing any whitespace (Unicode included) or an
    ASCII control character (C0 or DEL) is not linked at all.

    ELEMENT CONTENT ONLY: never interpolate safe_link output inside an HTML
    attribute.

    ``scripts/check_antipatterns.py`` credits any call to this function as fully
    escaped, so EVERY argument that reaches the output (title, url, style) must
    be escaped here — do not add an argument without escaping it.
    """
    try:
        n = int(max_len)
    except (TypeError, ValueError):
        n = 90
    n = max(0, n)
    raw = "" if title is None else str(title)
    raw = " ".join(raw.split())
    # Truncate the RAW text first, THEN escape: escaping first could cut an
    # entity such as ``&amp;`` in half.
    truncated = raw[:n] + ("…" if len(raw) > n else "")
    safe_title = _html.escape(truncated, quote=True)
    # Character references AFTER html.escape so they are not double-escaped.
    for ch, ref in (("$", "&#36;"), ("[", "&#91;"), ("]", "&#93;"),
                    ("\\", "&#92;"), ("`", "&#96;")):
        safe_title = safe_title.replace(ch, ref)
    # Collapse whitespace so a newline/blank line in a style can never end the
    # HTML block (same reason as the title).
    safe_style = _html.escape(" ".join(str(style).split()), quote=True)
    u = "" if url is None else str(url).strip()
    bad_char = any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in u)
    if not bad_char and u.lower().startswith(("http://", "https://")):
        # A backtick in the href could pair with a stray backtick in an earlier
        # inline field and open a code span closing inside this href, so it is
        # percent-encoded. [ ] stay as-is: inside an HTML tag they are not
        # markdown syntax, and IPv6 hosts need them.
        safe_url = _html.escape(u.replace("`", "%60"), quote=True)
        return (f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer" '
                f'style="{safe_style}">{safe_title}</a>')
    return f'<span style="{safe_style}">{safe_title}</span>'


def _factor_tilt_state_and_values(factor_tilt: Any) -> tuple:
    """(state, valid_correlations) for a `_pi_factor_tilt_cache` value.

    state is one of "not_measured" | "unusable" | "measured". This is the SINGLE
    classifier read by both the LLM evidence line and the on-screen disclosure
    in app.py, so the two can never disagree about which state the app is in —
    the divergence risk is the whole reason this fix exists.
    """
    if factor_tilt is None:
        return "not_measured", {}

    # Explicit, not `.get("portfolio_tilt") or {}` — the recurring-defect gate
    # flags that shape and is right to: `or {}` would also swallow a truthy
    # non-dict and then raise on .items() below.
    try:
        portfolio_tilt = factor_tilt.get("portfolio_tilt")
    except AttributeError:
        portfolio_tilt = None
    if not isinstance(portfolio_tilt, dict):
        portfolio_tilt = {}

    valid = {k: v for k, v in portfolio_tilt.items() if v is not None}
    return ("measured", valid) if valid else ("unusable", {})


def factor_tilt_state(factor_tilt: Any) -> str:
    """"not_measured" | "unusable" | "measured" — for UI disclosure decisions."""
    return _factor_tilt_state_and_values(factor_tilt)[0]


def factor_tilt_evidence_line(factor_tilt: Any) -> str:
    """One evidence line describing style-factor exposure, for an LLM prompt.

    ALWAYS returns a non-empty line. That is the whole point: both LLM
    narrative surfaces that consume `_pi_factor_tilt_cache` used to *silently
    omit* this line whenever factor data was absent, which handed the model an
    evidence block indistinguishable from one where factor concentration had
    been measured and found unremarkable. The model then wrote — and the app
    persisted — an adversarial-scenario narrative as though its evidence were
    complete. (F-260, 2026-08-28.)

    The absence is the COMMON case, not an edge case: `_pi_factor_tilt_cache`
    is produced only when the user clicks "📡 Load factor exposure" on 🧩
    Intelligence, while both consumers live on OTHER pages (🔗 Risk Analysis,
    and Intelligence's own 🧬 Structural Scan tab). A session that never
    clicked that button reaches both consumers with `None`.

    Three states, deliberately kept distinct — collapsing any two of them is
    the defect this function exists to prevent:

      None            -> never measured this session (no data was loaded)
      measured, empty -> measured, but no usable per-factor correlation
                         (e.g. too little overlapping history)
      measured, valid -> the real reading

    The first two previously produced the SAME output: nothing.
    """
    state, valid = _factor_tilt_state_and_values(factor_tilt)

    if state == "not_measured":
        return (
            "Factor tilt: NOT MEASURED — factor-exposure data was not loaded "
            "for this portfolio. The absence of a factor reading is NOT evidence "
            "of low or balanced factor concentration. Do not state, imply, or "
            "reason about factor exposure in any direction."
        )
    if state == "unusable":
        # Deliberately does NOT name a cause. `portfolio_intelligence.factor_tilt`
        # returns its empty shape from four different exits (no held data / no
        # factor returns, <2 usable return series, all positions dropped, and a
        # bare except), plus a genuine success path where every weight sums to
        # zero. An earlier draft said "(insufficient overlapping return history)"
        # — one of those five — which would have handed the model a SPECIFIC
        # fabricated cause to restate as fact inside a persisted narrative. That
        # is the same fabrication class this whole function exists to close, one
        # clause further down. Caught in review, 2026-08-28.
        return (
            "Factor tilt: measured, but no usable per-factor correlation was "
            "available (cause not distinguished). Treat factor concentration as "
            "unknown — this is not a reading of 'no tilt'."
        )

    dom = max(valid, key=lambda k: abs(valid[k]))
    return (
        f"Factor tilt: portfolio leans {dom}-tilted "
        f"(weighted correlation {valid[dom]:+.2f})"
    )


def md_bold_to_html(value: Any) -> str:
    """Escape for HTML, then render ``**bold**`` as ``<b>bold</b>``.

    For strings authored as markdown but rendered inside a raw
    ``unsafe_allow_html`` block. **Streamlit does not process markdown inside
    raw HTML**, so the asterisks print literally — verified live on 📋 Watchlist
    2026-08-27, where `watchlist_advisor` bolds the IMPERATIVE ("Open the
    position", "Do not open the position at full size"), meaning the phrases
    designed to stand out were exactly the ones rendering broken, on a surface
    whose job is issuing a call.

    Same root cause family as the `$…$`-as-LaTeX bug fixed in 4fa9edf: a string
    authored for one renderer, emitted into another. The conversion belongs at
    the RENDER boundary — the advisor must not know what renders it.

    Order is load-bearing: escape FIRST, then convert. ``**`` contains no HTML
    metacharacters so it survives escaping intact, whereas converting first
    would let the escape mangle the tags it just produced. This also closes an
    escaping gap, since the Watchlist call sites interpolated these strings raw.

    Markers pair left-to-right, non-greedily. A LONE trailing marker stays
    literal, but an odd count of three or more pairs the first two and strands
    the rest — ``"x ** y **Open the position** z"`` bolds `` y `` and leaves
    ``**`` mid-imperative. Never unsafe (tag balance is structural: every
    substitution emits exactly one open and one close), and not currently
    reachable, since all advisor bold spans are correctly paired. Stated
    plainly rather than claimed away, because the failure would be silent and
    would land on the phrase the author chose to emphasise.

    No ``re.DOTALL``: a span containing a newline is left unconverted. Verified
    2026-08-28 that no advisor bold span spans lines — re-check before applying
    this to a new producer.
    """
    escaped = safe_html(value)
    return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def sizing_cap_lines(ps: Any, net_capital_cap_pct: Any) -> list[str]:
    """Disclosure lines for which cap actually determined a position size.

    `risk.position_sizing` applies its two caps SEQUENTIALLY — the gross-book
    single-name ceiling first, then the net-capital cap, which can only reduce
    further. So whichever fired LAST is the one that bound, and when both fire
    the first is an intermediate step, not the answer.

    That is the defect this exists to fix (production screenshot 2026-08-28).
    📋 Watchlist rendered "capped to 15% single-name ceiling (risk-based would
    be 159 sh / ~19%)" directly above a result of **63 shares = 7.6% of
    portfolio**. Every number was individually true, but the line named a cap
    that did not produce the figure shown — the 15% ceiling would have allowed
    124 shares. The binding constraint was the net-capital cap on the next
    line, mentioned only as "also". A reader had to reconstruct the chain to
    see which one governed.

    Note 📈 Analysis did NOT have this problem: its longer warnings each end by
    restating the resulting share count. This lifts that property into one
    tested place. `notify._sizing_cap_note` is a third implementation of the
    same disclosure, deliberately left alone here (it is email copy with its
    own format) — but it is why this belongs in a function rather than in a
    third inline copy.

    Policy-free by construction: the cap percentage is PASSED IN, never
    imported, so no threshold lives in this module.

    Returns [] when neither cap bound — the ordinary case, where the risk-based
    size stood and there is nothing to disclose.
    """
    if not isinstance(ps, dict):
        return []
    ceiling = bool(ps.get("ceiling_capped"))
    capital = bool(ps.get("capital_capped"))
    if not (ceiling or capital):
        return []

    def _n(key, default=0):
        val = ps.get(key, default)
        return val if isinstance(val, (int, float)) else default

    shares = _n("shares")
    out: list[str] = []
    if ceiling:
        line = (f"↳ risk-based size {_n('uncapped_shares'):,} sh "
                f"(~{_n('uncapped_pct'):.0f}%) trimmed by the "
                f"{_n('ceiling_pct'):.0f}% single-name ceiling")
        # Only claim this produced the final number when nothing tightened it
        # further. With the capital cap also firing, saying so here is the
        # misattribution above.
        if not capital:
            line += f" → {shares:,} sh ({_n('portfolio_pct'):.1f}% of book)"
        out.append(line)
    if capital:
        out.append(
            f"↳ {'then ' if ceiling else ''}bound by the "
            f"{_n_pct(net_capital_cap_pct):.0f}% net-capital cap → {shares:,} sh "
            f"({_n('portfolio_pct'):.1f}% of book, "
            f"~{_n('capital_pct'):.0f}% of net capital)"
        )
    return out


def _n_pct(value: Any) -> float:
    """Numeric coercion for a passed-in percentage; 0.0 rather than a crash."""
    return float(value) if isinstance(value, (int, float)) else 0.0


def sizing_unavailable_caption(
    reason: str | None, *, price: float | None, portfolio_value: float | None,
    net_capital: float | None, single_ceiling_pct: Any, capital_cap_pct: Any,
) -> str:
    """The fallback "why can't I size this?" caption for a
    `risk.sizing_unavailable_reason()` verdict.

    Extracted from two byte-identical inline copies (📈 Analysis, 📋
    Watchlist) — Part 2 #3 of the 2026-08-26 app review, "F-255 capital-cap
    wiring." Input-driven, not calling `sizing_unavailable_reason` itself, so
    this stays a leaf function with no project-internal dependency and no
    import-cycle risk — the caller has already computed `reason` and passes
    the same price/portfolio_value/net_capital it computed it from.

    Every numeric input is coerced defensively (non-numeric → treated as
    absent) so a malformed caller degrades to the generic caption instead of
    raising. The two current call sites pass genuine floats for `price` and
    `portfolio_value`, and a float-OR-`None` `net_capital` — `None` is a
    normal, expected value there (`margin.resolve_net_capital` returns it for
    an unlevered/stale/no-record account), not malformed input; this
    function's `net_capital` guards already treat it the same as any other
    falsy value, so the coercion changes nothing for that legitimate case
    either.

    KNOWN, DELIBERATELY UNCHANGED latent gap: when the account is
    margin-called, `resolve_net_capital` can return a non-positive
    `net_capital`, so `reason == "capital"` falls through to the generic
    "stop price too close" caption below rather than a capital-specific one
    — the same misattribution class F-249/F-255 were built to fight,
    reproduced here because this is a byte-identical refactor, not a fix.
    Pinned by a test; fixing it is a separate, reviewed follow-up because it
    would change rendered text.
    """
    price = price if isinstance(price, (int, float)) else None
    portfolio_value = portfolio_value if isinstance(portfolio_value, (int, float)) else None
    net_capital = net_capital if isinstance(net_capital, (int, float)) else None

    if reason == "ceiling" and price and portfolio_value:
        return (
            f"Position sizing unavailable — one share is "
            f"~{price / portfolio_value * 100:.0f}% of your portfolio, above the "
            f"{int(_n_pct(single_ceiling_pct))}% single-name ceiling. The stop is fine; "
            f"this name is too large for this account at the current cap."
        )
    if reason == "capital" and price and net_capital and net_capital > 0:
        return (
            f"Position sizing unavailable — one share is "
            f"~{price / net_capital * 100:.0f}% of your net capital, above the "
            f"{int(_n_pct(capital_cap_pct))}% net-capital cap. This is separate from "
            "the single-name book cap."
        )
    if reason == "portfolio":
        return (
            "Position sizing unavailable — your portfolio value isn't loaded in this "
            "session, so any share count would be a guess. Open 🏠 Home to "
            "load it, then come back."
        )
    return "Position sizing unavailable — stop price too close to entry or not set."


# ── Nav-badge / mini-card tri-state classification (2026-09-24 app review) ──
# A2/A3/A4: these three sites collapsed "the check failed" into the SAME
# render as "checked, found nothing" — a fabricated all-clear, on both a
# Summary mini-card and a sidebar nav badge. Extracted into pure functions
# per the "extract the DECISION, not just the helper" convention (app.py has
# no test coverage of its own): each is a tri-state classification, not a
# render, and each is now boundary-tested here instead of only verifiable by
# screenshot.

def catalyst_watch_mini_state(n_earnings_soon: int, check_failed: bool) -> tuple[str, str]:
    """(line_text, hex_color) for 🧾 Summary's Catalyst Watch mini-card.

    `check_failed` takes priority over `n_earnings_soon`: a failed lookup
    must never render as the SAME green "None soon" a genuine clean check
    produces (2026-09-24 app review, A2). Grey is a third, honest state,
    distinct from both the amber "reporting" and green "none soon" outcomes.
    """
    if check_failed:
        return "Unknown", "#9ca3af"
    if n_earnings_soon:
        return f"{n_earnings_soon} reporting", "#f59e0b"
    return "None soon", "#22c55e"


def earnings_posture_alert_count(playbook: list[dict] | None, check_failed: bool) -> int | None:
    """Count of EXIT/REDUCE Pre-Earnings Playbook items, or None if the
    lookup that would have built `playbook` failed.

    Publishing `None` on failure (instead of the fabricated `0` a genuine
    zero-EXIT/REDUCE day also produces) is the fix: the nav badge reading
    `_earnings_posture_alerts_cache` can only recover the offline state if
    the producer never collapses it in the first place (2026-09-24 app
    review, A3). `playbook` is ignored (and may be `None`/anything) when
    `check_failed` is True.
    """
    if check_failed:
        return None
    return sum(1 for p in (playbook or []) if p.get("action") in ("EXIT", "REDUCE"))


def catalyst_watch_nav_badge(
    risk_alerts: int, risk_offline: bool, earnings_alerts: int, earnings_offline: bool,
) -> list[str]:
    """Markdown badge-part strings (possibly empty) for the 🔔 Catalyst Watch
    sidebar nav entry.

    Two INDEPENDENT sources share one badge slot — risk-high-alerts and
    earnings-posture-alerts — so each gets its own grey/colored token rather
    than collapsing either failure into a combined generic "?" (2026-09-24
    app review, A4). A real risk-alert count must still show even if the
    earnings check failed, and vice versa; losing a known-good count to a
    sibling source's failure would be a NEW instance of the same bug class
    this fix closes.
    """
    parts: list[str] = []
    if risk_offline:
        parts.append(":grey-background[● ?]")
    elif risk_alerts > 0:
        parts.append(f":red-background[● {risk_alerts}]")
    if earnings_offline:
        parts.append(":grey-background[● ?]")
    elif earnings_alerts > 0:
        parts.append(f":orange-background[● {earnings_alerts}]")
    return parts


def signals_advice_nav_badge(n_danger: int, n_warning: int, offline: bool) -> list[str]:
    """Markdown badge-part strings (possibly empty) for the 📡 Signals &
    Advice sidebar nav entry.

    `n_danger`/`n_warning` are published together at the same call sites as
    `_alert_list_cache` — `offline` means Home hasn't run this session yet,
    the SAME state the Signals & Advice page's own body already renders as
    an explicit banner (`_sa_offline`). Previously the nav badge alone
    collapsed this via `or 0`, showing no dot at all where its Catalyst
    Watch sibling already showed a grey "?" for the equivalent state
    (2026-09-24 app review, A4). Unlike `catalyst_watch_nav_badge`'s two
    independent sources, danger/warning are one source and always agree on
    offline-ness, so one combined "?" replaces both color slots.
    """
    if offline:
        return [":grey-background[● ?]"]
    parts: list[str] = []
    if n_danger > 0:
        parts.append(f":red-background[● {n_danger}]")
    if n_warning > 0:
        parts.append(f":orange-background[● {n_warning}]")
    return parts


def defense_facet_badge(
    band: str, protect_alpha: float | None, n_mature: int, min_calls: int,
) -> dict:
    """Badge/color/value/basis for 🧾 Summary's Engine Track Record card,
    Defense facet (2026-09-24 app review, B1).

    Extracted from what was an untested inline `app.py` conditional, same
    "extract the decision" motivation as `catalyst_watch_mini_state` etc.
    Mirrors the shape of the Offense facet's own (untouched, not part of
    this fix) inline logic, with the SIGN READING INVERTED: unlike Offense,
    where positive alpha is unambiguously good, Defense's `protect_alpha` is
    "good" when NEGATIVE — see `protective_track_record.py`'s module
    docstring for why (a protective call is right when the flagged name
    underperforms SPY after the warning). `protect_alpha` uses the SAME
    formula as every other alpha in the app (`name_return − spy_return`,
    flipped 2026-09-24 from a uniquely-inverted local formula) — only the
    READING of the sign differs here, not the arithmetic.

    Returns `{badge, badge_color, value_text, value_color, basis}`. `band`
    is `protective_headline()`'s own building/early/firm classification
    (sample-size only, never sign-dependent — no flip needed there).
    """
    if band == "building" or protect_alpha is None:
        _need = max(0, min_calls - n_mature)
        return {
            "badge":       "BUILDING" if band == "building" else "NO DATA",
            "badge_color": "#8b94a7",
            "value_text":  "—",
            "value_color": "#6b7280",
            "basis":       (f"needs {_need} more matured EXIT/TRIM call(s)"
                             if _need else "maturing"),
        }
    if band == "firm" and protect_alpha < 0:
        badge, badge_color = "VALIDATED ✓", "#57d98a"
    elif band == "firm":
        badge, badge_color = "RAN EARLY", "#f0c24b"
    else:
        badge, badge_color = "EARLY READ", "#f0c24b"
    return {
        "badge":       badge,
        "badge_color": badge_color,
        "value_text":  f"{protect_alpha:+.1f}pp",
        "value_color": "#57d98a" if protect_alpha < 0 else "#fca5a5",
        "basis":       f"{n_mature} flagged · avg vs SPY after the warning",
    }


def is_duplicate_account_flow(
    candidate: dict, recent_rows: "list[dict]", window_sec: int,
) -> bool:
    """Pure decision function behind `db.add_account_flow`'s write-time dedup
    guard (F2a, docs/plans/data-maintenance-framework.md). Separates the
    DECISION (is this a duplicate) from the I/O (the caller does the DB read
    and the insert), per this repo's "extract the decision" convention.

    True when `recent_rows` (as returned by
    `db.load_account_flows_for_dedup_check()`) contains a MANUAL row
    (`snaptrade_txn_id` falsy/None) with the identical `(flow_date,
    flow_type, amount, note)` as `candidate`, whose own `created_at` is
    within `window_sec` seconds of right now. This targets the real risk — a
    double-clicked submit button inserting the same form twice, near-
    simultaneously — without blocking a genuinely repeated flow entered long
    afterward (e.g. two real $500 deposits made days apart). A row carrying
    a non-null `snaptrade_txn_id` is never treated as a match target — that
    is a different, legitimate write path (`db.save_account_flows`) with its
    own dedup guard (the partial unique index on `snaptrade_txn_id`).

    `candidate` is the about-to-be-inserted record shape (`flow_date`,
    `flow_type`, `amount`, `note` — no `created_at` yet, it hasn't been
    written). A row whose `created_at` can't be parsed is skipped rather
    than treated as a match — an unparseable timestamp can't establish
    recency, and per this repo's offline-sentinel convention, "can't tell"
    must never collapse to an affirmative "duplicate"/"safe" reading.
    """
    from stock_analyzer import market_time
    import pandas as pd

    cand_date = str(candidate.get("flow_date"))
    cand_type = str(candidate.get("flow_type"))
    cand_amount = float(candidate.get("amount") or 0.0)
    cand_note = candidate.get("note")

    now_utc = pd.Timestamp(market_time.now_et()).tz_convert("UTC")

    for row in recent_rows or []:
        if row.get("snaptrade_txn_id"):
            continue  # broker-sourced row — never a dedup match target
        if str(row.get("flow_date")) != cand_date:
            continue
        if str(row.get("flow_type")) != cand_type:
            continue
        if abs(float(row.get("amount") or 0.0) - cand_amount) > 1e-6:
            continue
        if row.get("note") != cand_note:
            continue
        created = pd.to_datetime(row.get("created_at"), utc=True, errors="coerce")
        if pd.isna(created):
            continue  # can't establish recency — never a silent match
        if abs((now_utc - created).total_seconds()) <= window_sec:
            return True
    return False


def _left_out_reasons(corr_coverage) -> "tuple[dict, list]":
    """({TICKER: n_returns} for the short_history left-out holdings, [TICKER] of
    every left-out holding) read from a correlation coverage dict (CorrYoung).
    ({}, []) for a non-dict or a pre-C1 coverage with no left_out. Never raises."""
    from stock_analyzer import corr_window as _cw   # function-local, as above
    try:
        items = _cw.left_out_items(corr_coverage)
        names = [str(x["ticker"]).strip().upper() for x in items]
        short = {
            str(x["ticker"]).strip().upper(): int(x["n_returns"])
            for x in items if x.get("reason") == "short_history"
        }
        return short, [n for n in names if n]
    except Exception:
        return {}, []


def correlation_unchecked_note(
    corr_unchecked: "list | None",
    corr_coverage: "dict | None" = None,
) -> "str | None":
    """On-screen disclosure companion to `portfolio.correlation_unchecked()`
    (F-290 follow-on) — screens DISCLOSE here; `portfolio.correlation_claim_verified()`
    is the separate, stricter predicate records use to WITHHOLD.

    Three-state, matching `correlation_unchecked`'s own contract exactly:
      None -- couldn't check this session at all (Home is the only producer).
      []   -- checked, every held ticker entered the matrix -- nothing to say.
      list -- names which held tickers never entered the matrix.

    `corr_coverage` (optional, CorrYoung C2) gives the REASON per name: a holding
    in `corr_coverage["left_out"]` with reason short_history reads "T has only N
    daily returns of price history (20 needed)" ("it has only ..." when it is the
    one name); every other name keeps "price history couldn't be loaded this run".
    With `corr_unchecked is None` but a coverage that names left-out holdings
    (fewer than 2 were counted, so there is no matrix and no classification), a
    separate sentence names those holdings with their reasons AND says whether
    every other holding was included couldn't be confirmed; it has no
    "classification above" clause and no Home remedy. One argument = exactly the
    old behaviour.

    Returns
    -------
    None -- `corr_unchecked == []`, or any other falsy/empty input (nothing
            to disclose).
    str  -- a short, plain-text note. `None` input reads as "unknown this
            session, open Home"; a populated list names up to 5 tickers (then
            "+N more"), singular/plural handled, and always ends by making
            clear the classification it qualifies describes the OTHER
            holdings only -- this is NOT evidence the named ticker(s) are
            themselves uncorrelated.

    Never contains a literal `**` or `$` — feedback_streamlit_renderer_mismatch
    (this project has no automated render-mismatch test coverage, so the
    string itself must stay plain).
    """
    short, left_names = _left_out_reasons(corr_coverage)
    if corr_unchecked is None:
        if not left_names:
            return (
                "🔍 Whether every holding was included in this correlation "
                "reading is unknown this session — open 🏠 Home to check."
            )
        # Fewer than 2 holdings were counted (no matrix, no classification) and
        # the producer published no unchecked list: name the left-out holdings
        # AND keep the fact that the rest couldn't be confirmed. No "classification
        # above" clause, because there is none.
        from stock_analyzer import corr_window as _cw
        _lo = _cw.left_out_items(corr_coverage)
        _lo_names = ", ".join(left_names[:5]) + (
            f" +{len(left_names) - 5} more" if len(left_names) > 5 else "")
        return (
            f"🔍 {_lo_names} {'were' if len(_lo) > 1 else 'was'} left out of this reading "
            f"({_cw.unconfirmed_reasons(_lo, need=_cw.need_of(corr_coverage))}); whether "
            f"every other holding was included couldn't be confirmed this session."
        )
    if not corr_unchecked:
        return None
    try:
        tickers = sorted({
            str(t).strip().upper() for t in corr_unchecked
            if t is not None and str(t).strip()
        })
    except Exception:
        tickers = []
    if not tickers:
        return None

    _shown = tickers[:5]
    _more = len(tickers) - len(_shown)
    _names = ", ".join(_shown) + (f" +{_more} more" if _more > 0 else "")
    _plural = len(tickers) > 1
    _subject = "their" if _plural else "its"
    _object = "them" if _plural else "it"
    _was_were = "were" if _plural else "was"
    _final = "they are" if _plural else f"{_names} is"

    young = [t for t in tickers if t in short]
    if young:
        from stock_analyzer import corr_window as _cw
        rest = [t for t in tickers if t not in short]
        _need = _cw.need_of(corr_coverage)
        _why = _cw.short_history_clause(
            [{"ticker": t, "n_returns": short[t]} for t in young],
            need=_need, of_history=True, pronoun=not rest and len(young) == 1,
        )
        if not rest and len(young) == 1:
            _final = "it is"   # the sentence already named it
        if rest:
            _rest_txt = ", ".join(rest[:5]) + (f" +{len(rest) - 5} more" if len(rest) > 5 else "")
            _why += f" and price history couldn't be loaded this run for {_rest_txt}"
    else:
        _why = f"{_subject} price history couldn't be loaded this run"

    # "price HISTORY couldn't be loaded" — matching pair_add_gate's
    # unchecked_disclosure precedent (Opus review, 2026-10-09): a ticker
    # whose bundle loaded but whose history is empty/missing a Close column
    # can still carry a live current_price and show a price elsewhere on
    # screen, so "couldn't be priced" would contradict a number already on
    # screen. The correlation matrix needs the history, not the last price.
    return (
        f"🔍 {_names} {_was_were} left out of this reading — {_why}, so no "
        f"correlation involving {_object} could be measured. The classification "
        f"above describes your other holdings only; this is not a sign {_final} "
        f"uncorrelated."
    )


def correlation_flat_note(corr_flat: "list | None", *, scored: bool) -> "str | None":
    """On-screen disclosure for `portfolio.correlation_flat()` — held
    tickers whose daily returns showed no variation over the shared sample
    (in practice a price that never moves), so they carry no correlation and
    were left out of the diversification average.

    `scored` (keyword-only, no default) is whether a diversification score
    exists at all. When the flat names leave no finite pair there is no
    average and no figures above, so the note must say that instead of
    "the figures above describe your other holdings" (review 2026-10-13).

    `None` (couldn't tell) and `[]` (none flat) both return None: unlike
    `correlation_unchecked_note`, there is no "unknown" line here, because a
    flat holding never blocks verification (owner decision 2026-10-13) — it
    is extra context, not a gap in the claim. Plain text only — no `**`,
    no `$` (feedback_streamlit_renderer_mismatch).
    """
    if not corr_flat:
        return None
    try:
        tickers = sorted({
            str(t).strip().upper() for t in corr_flat
            if t is not None and str(t).strip()
        })
    except Exception:
        tickers = []
    if not tickers:
        return None
    _shown = tickers[:5]
    _more = len(tickers) - len(_shown)
    _names = ", ".join(_shown) + (f" +{_more} more" if _more > 0 else "")
    _plural = len(tickers) > 1
    _why = (f"{'their daily returns' if _plural else 'its daily returns'} showed no "
            f"variation over this sample, so no correlation can be measured.")
    if not scored:
        return (
            f"➖ {_names} {'were' if _plural else 'was'} left out — {_why} "
            f"With {'them' if _plural else 'it'} left out, no pair of holdings "
            f"remains to measure, so no diversification score is shown."
        )
    return (
        f"➖ {_names} {'were' if _plural else 'was'} left out of the "
        f"diversification average — {_why} "
        f"The figures above describe your other holdings."
    )


def diversification_label_text(
    div_label: "str | None",
    corr_unchecked: "list | None",
    corr_coverage: "dict | None" = None,
) -> "str | None":
    """Qualify a diversification label when the classification could not
    actually evaluate the whole book — companion to
    `correlation_unchecked_note` above, sharing the same inputs so the two
    can never disagree about which gap they're describing.

    TWO gaps are disclosed here, and the second is why `corr_coverage` exists as
    a parameter at all:

      1. **A held ticker never entered the matrix** (`corr_unchecked`
         non-empty) → `"Well Diversified · 1 not checked"`.
      2. **The overlapping SAMPLE is too thin**
         (`portfolio.correlation_sample_state(corr_coverage)` is thin) → the
         label is `"Unavailable · sample too thin"` for EVERY input label,
         including `"Unavailable"` and `None`, with or without names not
         checked (CorrYoung C2: a thin sample has no diversification score, so a
         grade is never carried over it; the old "<label> · sample too thin"
         and "· N not checked" suffix are gone).

    **Gap 2 was a blocking review finding (2026-10-09), not a nicety.** An
    all-NaN or too-thin matrix leaves `corr_unchecked` empty (every ticker IS a
    column) while `n_obs` is tiny, so gap 1 alone left the label unqualified
    next to 🔗 Risk Analysis's own "not reliable" block: one matrix, two
    contradictory screens, exactly the double-surface class this shared
    function exists to prevent. The thin check runs first for that reason.

    Returns `div_label` UNCHANGED when the sample is not thin and there is
    nothing to qualify: no missing tickers, coverage absent/unknown/sufficient,
    or `div_label == "Unavailable"` (the withheld label already says nothing was
    computed). `corr_coverage is None` means "couldn't check" and leaves the
    label alone; the fuller `correlation_unchecked_note` caption says that.

    Deliberately does not repeat the fuller explanation — that belongs in
    `correlation_unchecked_note`'s caption; this is only the label-level
    flag that something sits behind the number. Reads both inputs directly
    rather than re-deriving anything, so a label qualifier and its
    accompanying caption can never name a different gap.
    """
    # Gap 2 — thin/empty sample. Evaluated FIRST because it can be true while
    # corr_unchecked is empty, which is the exact case that shipped
    # unqualified and got caught in review.
    # Imported here, not at module scope, to avoid a util <-> portfolio
    # import cycle. thin -> qualifier; unknown/trusted -> none.
    from stock_analyzer import portfolio as _portfolio
    _thin = (_portfolio.correlation_sample_state(corr_coverage)
             == _portfolio.CORR_SAMPLE_THIN)
    if _thin:
        # CorrYoung C2: a thin sample has no diversification score at all, so the
        # label never carries a grade over it (the Home producer publishes
        # "Unavailable" too; this keeps a stale label honest).
        return "Unavailable · sample too thin"

    if div_label == "Unavailable":
        return div_label

    if not corr_unchecked:
        return div_label
    try:
        n = len({
            str(t).strip().upper() for t in corr_unchecked
            if t is not None and str(t).strip()
        })
    except Exception:
        return div_label
    if n == 0:
        return div_label
    return f"{div_label} · {n} not checked"


def correlation_record_withheld_note(corr_unchecked, corr_coverage, *, subject: str) -> "str | None":
    """Why a correlation-derived RECORD is being withheld, or None when it isn't.

    Returns None EXACTLY when `portfolio.correlation_claim_verified` is True
    (it is called, not re-derived), so this note and the gate that withholds
    the write can never disagree. Otherwise one plain sentence naming WHY,
    with BOTH clauses when both gaps hold (F-293: never let the more severe
    gap hide the other). `subject` is the sentence's lead-in naming what is
    withheld and how, e.g. "This scan can't be run or saved right now" -- the
    reasons follow a colon. Each reason carries a remedy where one exists:
    Home's 🔄 Refresh Signals when the reading couldn't be confirmed (a plain
    Home revisit would re-serve the memoized failure). No dollar signs:
    Streamlit renders paired ones as LaTeX. A holding left out for a short
    history is named from `corr_coverage["left_out"]` ("ANT has only 4 daily
    returns (20 needed)"); a thin sample never names a "cap" (CorrYoung C2).
    """
    # Function-local import: util <-> portfolio import cycle.
    from stock_analyzer import portfolio as _portfolio
    if _portfolio.correlation_claim_verified(corr_unchecked, corr_coverage):
        return None

    reasons = []
    if not isinstance(corr_unchecked, (list, tuple)):
        reasons.append("couldn't confirm every holding was included")
    elif len(corr_unchecked) > 0:
        try:
            names = sorted({str(t).strip().upper() for t in corr_unchecked
                            if t is not None and str(t).strip()})
        except Exception:
            names = []
        if names:
            # CorrYoung C2: a young holding is named with its own reason; only the
            # rest keep the "missing from the correlation matrix" clause.
            _short, _ = _left_out_reasons(corr_coverage)
            _young = [n for n in names if n in _short]
            _rest = [n for n in names if n not in _short]
            if _young:
                from stock_analyzer import corr_window as _cw
                reasons.append(_cw.short_history_clause(
                    [{"ticker": n, "n_returns": _short[n]} for n in _young],
                    need=_cw.need_of(corr_coverage), of_history=False,
                ))
            if _rest:
                _rshown = ", ".join(_rest[:5])
                if len(_rest) > 5:
                    _rshown += f" +{len(_rest) - 5} more"
                _verb = "holding is" if len(_rest) == 1 else "holdings are"
                reasons.append(f"{len(_rest)} {_verb} missing from the correlation matrix ({_rshown})")
        else:
            reasons.append("some holdings are missing from the correlation matrix")

    state = _portfolio.correlation_sample_state(corr_coverage)
    if state == _portfolio.CORR_SAMPLE_THIN:
        _thin = (f"only {int(corr_coverage['n_obs'])} shared daily returns "
                 f"(needs {_portfolio.CORR_MIN_OBS_TRUSTED})")
        # No "capped by X" branch (CorrYoung C2): a young holding is left out of
        # the matrix, so it can no longer be what caps the sample; and when the
        # listwise cut is gone, thin always means the histories barely overlap.
        _thin += ", because the holdings' price histories only overlap on those days"
        reasons.append(_thin)
    elif state == _portfolio.CORR_SAMPLE_UNKNOWN:
        reasons.append("couldn't confirm the sample size")

    if not reasons:  # defensive: verifier said no but no clause matched
        reasons.append("the correlation reading could not be verified")
    note = f"{subject}: " + " and ".join(reasons) + "."
    if not isinstance(corr_unchecked, (list, tuple)) or state == _portfolio.CORR_SAMPLE_UNKNOWN:
        # NOT "revisit Home": Home memoizes the failed bundle and a revisit
        # takes the memo-hit path, republishing the same None. Refresh Signals
        # bumps _scanner_ver, which forces the miss that actually recomputes.
        note += " Open 🏠 Home and click 🔄 Refresh Signals to recompute it."
    return note


def pair_trim_withheld_note(rec) -> "str | None":
    """Render-ready note for ONE `PAIR_RISK_WITHHELD` rec (audit A-H2), or
    None for anything else (a PAIR_RISK rec, a non-dict, a malformed rec).

    Pure and plain-text: no `$` (Streamlit renders `$...$` as LaTeX) and no
    markdown bold. States plainly that NO trim is recommended and that the
    Daily Brief's add pause on both names stays in force (G-26 keys on the
    pair's own danger-tier correlation, not on a trim card).
    """
    if not isinstance(rec, dict) or rec.get("type") != "PAIR_RISK_WITHHELD":
        return None
    t1, t2 = rec.get("t1"), rec.get("t2")
    if not isinstance(t1, str) or not isinstance(t2, str) or not t1 or not t2:
        return None
    n_obs, min_obs = rec.get("n_obs"), rec.get("min_obs")
    if (rec.get("withheld_reason") == "thin_sample"
            and isinstance(n_obs, int) and not isinstance(n_obs, bool)
            and isinstance(min_obs, int) and not isinstance(min_obs, bool)):
        why = (f"the correlation rests on only {n_obs} shared daily returns, "
               f"needs {min_obs}")
    else:
        why = "the size of the correlation sample couldn't be confirmed"
    return (
        f"{t1} and {t2} look highly correlated, but {why}. "
        "No trim is recommended from a reading this noisy; the Daily Brief "
        "keeps add-to-winner suggestions on both names paused while the pair "
        "is flagged."
    )


def stop_distance_delta(price, stop) -> "tuple[str | None, str]":
    """`(delta, delta_color)` for a Stop Loss `st.metric`: how far the stop
    sits from the current price, signed (stop − price) / price.

    Replaces an inline f"-{(price - stop) / price * 100:.1f}%" with
    delta_color="inverse", which had two defects. "inverse" painted the
    always-negative normal case GREEN, i.e. "good news"; and once price fell
    below the stop the gap was already negative, so the string read "--3.2%".

    - stop below price -> ("-9.9%", "off"): a plain distance, grey; the
      arrow still points down from the leading "-".
    - price at/below the stop -> ("+3.2% · price at/below stop", "red").
    - missing / non-numeric / NaN / non-positive input -> (None, "off"), so
      the metric renders no delta rather than a fabricated one.
    """
    def _num(v):
        if v is None or isinstance(v, bool):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f == f and f > 0 else None   # f == f rejects NaN

    p, s = _num(price), _num(stop)
    if p is None or s is None:
        return None, "off"
    pct = (s - p) / p * 100
    if s < p:
        return f"{pct:.1f}%", "off"
    return f"{pct:+.1f}% · price at/below stop", "red"
