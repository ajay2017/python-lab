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
