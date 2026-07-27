"""
Signal reconciliation — central authority for resolving conflicts between
the momentum/technical scanner score, the full composite (Tech + Fundamental +
Sentiment) score, and adjacent context (held status, earnings proximity,
news sentiment).

Every surface that displays a buy/skip recommendation calls reconcile_signals()
so the resolution is consistent. The function returns a verdict tier plus a
human-readable one-liner that says what to do and why.

Tiers (worst to best):
  skip    : composite contradicts momentum (e.g. momentum 90 but composite Sell)
            or strong negative news, or signals + earnings imminent
  caution : earnings within 7 days regardless of other signals
  verify  : composite isn't loaded yet — momentum looks good but full analysis
            hasn't run. User should open Analysis page to confirm before acting.
  go      : composite confirms momentum, no earnings risk, no negative news

The one_liner is intentionally surface-agnostic so it reads the same in Daily
Briefing, Grow Today, Market Scanner, and Watchlist.
"""

from stock_analyzer.constants import (
    COMPOSITE_BUY,
    COMPOSITE_HOLD,
    NEWS_SENTIMENT_NEGATIVE,
    EARNINGS_IMMINENT_DAYS,
)


# Composite signal categorisation — string-match because the producer
# (scoring.recommendation) returns labels not enum values.
_BUY_WORDS  = ("Strong Buy", "Buy")
_SELL_WORDS = ("Sell", "Strong Sell", "Avoid", "Weak")
_HOLD_WORDS = ("Hold",)


def _composite_class(composite_signal: str | None, composite_score: float | None) -> str:
    """
    Return 'buy' | 'hold' | 'sell' | 'unknown'.

    Prefers label match (Sell beats numeric ambiguity), falls back to score
    boundary when label is empty.
    """
    if composite_signal:
        s = str(composite_signal).strip()
        if any(w in s for w in _BUY_WORDS):
            return "buy"
        if any(w in s for w in _SELL_WORDS):
            return "sell"
        if any(w in s for w in _HOLD_WORDS):
            return "hold"
    if composite_score is not None:
        if composite_score >= COMPOSITE_BUY:
            return "buy"
        if composite_score < COMPOSITE_HOLD:
            return "sell"
        return "hold"
    return "unknown"


def reconcile_signals(
    ticker: str,
    momentum_score: float,
    momentum_signal: str | None = None,
    composite_score: float | None = None,
    composite_signal: str | None = None,
    is_held: bool = False,
    is_mover: bool = False,
    earnings_days: int | None = None,
    news_sentiment: float | None = None,
) -> dict:
    """
    Central conflict resolver. Returns:
      verdict   : 'go' | 'verify' | 'caution' | 'skip'
      label     : short emoji + tag for badges
      one_liner : explicit resolution sentence
      color     : hex
      icon      : emoji
      composite_available : bool
    """
    comp_class = _composite_class(composite_signal, composite_score)
    composite_available = comp_class != "unknown"

    # Display strings used inside the one-liner.
    # Movers qualify via day-change breakout, not scanner momentum score —
    # "Breakout today" is the honest context phrase for that entry trigger.
    mom_str  = "Breakout today" if is_mover else f"Momentum {momentum_score:.0f}"
    comp_str = (
        f"Score: {composite_signal or 'n/a'}"
        + (f" ({composite_score:.0f}/100)" if composite_score is not None else "")
    )

    earnings_imminent = earnings_days is not None and 0 <= earnings_days <= EARNINGS_IMMINENT_DAYS
    earnings_label    = (
        "today" if earnings_days == 0 else
        f"in {earnings_days}d" if earnings_imminent else None
    )

    negative_news = news_sentiment is not None and news_sentiment <= NEWS_SENTIMENT_NEGATIVE

    # ── SKIP: composite contradicts momentum ────────────────────────────────
    if composite_available and comp_class in ("sell", "hold") and momentum_score >= COMPOSITE_BUY:
        verb = "Sell" if comp_class == "sell" else "Hold"
        return {
            "verdict":   "skip",
            "label":     "❌ Skip — Signals Disagree",
            "one_liner": (
                f"{mom_str} but {comp_str} — full multi-factor analysis says {verb}. "
                "Technical momentum is a breakout signal; the full score also weighs fundamentals and sentiment. "
                "Skip until scores align."
            ),
            "color": "#ef4444",
            "icon":  "❌",
            "composite_available": True,
        }

    # ── SKIP: strong negative news regardless of other signals ──────────────
    if negative_news and momentum_score >= COMPOSITE_BUY:
        return {
            "verdict":   "skip",
            "label":     "❌ Skip — Negative News",
            "one_liner": (
                f"{mom_str} but news sentiment is negative ({news_sentiment:+.2f}). "
                "Wait for the news catalyst to clear before entering."
            ),
            "color": "#ef4444",
            "icon":  "❌",
            "composite_available": composite_available,
        }

    # ── CAUTION: earnings imminent regardless of signal strength ────────────
    if earnings_imminent:
        return {
            "verdict":   "caution",
            "label":     f"⚠️ Caution — Earnings {earnings_label}",
            "one_liner": (
                f"{mom_str}"
                + (f" · {comp_str}" if composite_available else "")
                + f" — but earnings {earnings_label} make entry a binary event. "
                "Wait for the post-print setup."
            ),
            "color": "#f59e0b",
            "icon":  "⚠️",
            "composite_available": composite_available,
        }

    # ── VERIFY: composite not loaded — can't resolve conflict ───────────────
    if not composite_available:
        return {
            "verdict":   "verify",
            "label":     "🔍 Verify — Run Analysis First",
            "one_liner": (
                f"{mom_str} suggests a breakout, but the full score hasn't loaded yet. "
                "Open Analysis to confirm before acting."
            ),
            "color": "#f59e0b",
            "icon":  "🔍",
            "composite_available": False,
        }

    # ── GO: composite confirms momentum ─────────────────────────────────────
    if comp_class == "buy":
        return {
            "verdict":   "go",
            "label":     "✅ Go — All Signals Agree",
            "one_liner": (
                f"{mom_str} · {comp_str} — technical momentum and full-score analysis agree. "
                "Cleared to act within position-sizing rules."
            ),
            "color": "#22c55e",
            "icon":  "✅",
            "composite_available": True,
        }

    # ── FALLBACK: composite is Hold and momentum is moderate ────────────────
    return {
        "verdict":   "verify",
        "label":     "🔍 Verify — Mixed Conviction",
        "one_liner": (
            f"{mom_str} · {comp_str} — momentum is positive but the full score is neutral. "
            "Review the Analysis page before acting."
        ),
        "color": "#f59e0b",
        "icon":  "🔍",
        "composite_available": True,
    }


def effective_verdict_bucket(xref: dict) -> str:
    """Resolve a buy-candidate's display bucket ('confirmed' | 'unverified' |
    'conflicted') the SAME way its own card renders — preferring
    verdict_reconciled (reconcile_signals' output) over the legacy `verdict`
    field on daily_briefing._cross_reference()'s xref dict, falling back to
    the legacy field only when reconciled isn't present.

    Both fields ship on every xref, and app.py's candidate cards already
    prefer verdict_reconciled for their own color/label/one-liner. A summary
    count computed from the legacy field alone can disagree with what the
    cards right below it show (e.g. a held position whose composite agrees
    with momentum shows "reconciled: go" on its own card, but an analyst-
    revisions downgrade — a signal reconcile_signals never sees — trips the
    legacy verdict to "mixed"). Route every bucket TALLY through this
    function so it can never drift from what the cards actually display.
    See memory `project_verdict_divergence`.
    """
    reconciled = (xref or {}).get("verdict_reconciled") or {}
    rv = reconciled.get("verdict")
    if rv == "go":
        return "confirmed"
    if rv == "verify":
        return "unverified"
    if rv in ("caution", "skip"):
        return "conflicted"

    # No reconciled verdict on this xref — fall back to the legacy field.
    lv = (xref or {}).get("verdict")
    if lv == "confirmed":
        return "confirmed"
    if lv in ("conflicted", "caution", "mixed"):
        return "conflicted"
    return "unverified"


def classify_composite_direction(composite_signal: str | None, composite_score: float | None) -> str:
    """
    Public wrapper around _composite_class() for callers outside this module that
    need the composite's own directional class ('buy'|'hold'|'sell'|'unknown') without
    reconciling it against a separate momentum score (e.g. D3 Signal Coherence
    Auditor, which reads held positions where Score/Signal already IS the composite).
    """
    return _composite_class(composite_signal, composite_score)


def classify_signal_change(from_sig: str, to_sig: str) -> dict:
    """
    Given a previous and current signal label, return a dict with:
      degraded : bool — moved from buy-words to sell-words
      improved : bool — moved from sell-words to buy-words
    """
    _degraded = (
        any(w in to_sig for w in _SELL_WORDS)
        and any(w in from_sig for w in _BUY_WORDS)
    )
    _improved = (
        any(w in to_sig for w in _BUY_WORDS)
        and any(w in from_sig for w in _SELL_WORDS)
    )
    return {"degraded": _degraded, "improved": _improved}


def lookup_composite(ticker: str, port_df, composites: dict | None) -> tuple[str | None, float | None]:
    """
    Resolve composite (signal, score) for a ticker from any available source:
      1. port_df row (Signal + Score columns) — held positions
      2. composites dict (load_all() bundle) — pre-fetched scanner picks

    Returns (signal, score) or (None, None) if neither source has data.
    """
    if port_df is not None and not port_df.empty:
        match = port_df[port_df["Ticker"] == ticker]
        if not match.empty:
            sig = match.iloc[0].get("Signal")
            scr = match.iloc[0].get("Score")
            try:
                scr_val = float(scr) if scr is not None else None
            except (TypeError, ValueError):
                scr_val = None
            sig_val = str(sig).strip() if sig else None
            if sig_val or scr_val is not None:
                return sig_val, scr_val

    if composites and ticker in composites:
        bundle = composites[ticker] or {}
        rec = bundle.get("rec") or {}
        sig_val = str(rec.get("label", "")).strip() or None
        scr_val = bundle.get("total")
        try:
            scr_val = float(scr_val) if scr_val is not None else None
        except (TypeError, ValueError):
            scr_val = None
        if sig_val or scr_val is not None:
            return sig_val, scr_val

    return None, None
