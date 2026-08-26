"""Small cross-cutting helpers that make the safe idiom the default path.

Each closes a bug-class the 2026-07-29 / 2026-08-04 audits kept re-finding
because the correct idiom was opt-in and hand-remembered. Additive: this ships
the helpers; call sites migrate onto them incrementally (a rewire that touches a
gate/decision path gets its own review).
"""
from __future__ import annotations

import html as _html
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
