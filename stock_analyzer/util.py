"""Small cross-cutting helpers that make the safe idiom the default path.

Each closes a bug-class the 2026-07-29 / 2026-08-04 audits kept re-finding
because the correct idiom was opt-in and hand-remembered. Additive: this ships
the helpers; call sites migrate onto them incrementally (a rewire that touches a
gate/decision path gets its own review).
"""
from __future__ import annotations

import html as _html
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
    `technicals.py` does it with no flag at all. Rendering that as
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
