"""
Company-name -> ticker resolution for free-text stock lookups (e.g. Home's
"Research a Stock" box).

Single-source: yfinance's own ``Search`` (yfinance is already the pinned
primary provider — no new dependency, no new API key). AWARENESS-ADJACENT
ONLY, not a gate/scoring/recommendation path — this resolves WHICH ticker to
analyze; it never influences the analysis itself, and it is never consulted
for input that already looks like a real ticker (the fast path is
unchanged, to avoid extra latency/quota cost on the common case).

Never raises — matches every other provider-calling function in this
codebase. A resolution failure (no match, no EQUITY-type result, network
error) returns ``None``; the caller falls back to today's existing
behaviour (attempt the raw input as-is).
"""
from __future__ import annotations

import re

# A real US ticker is short (<=5 core letters), optionally with a
# class-share suffix (BRK.B, BRK-B). Anything longer / shaped differently is
# almost certainly a company name, not a ticker -- route it to search FIRST
# rather than wasting a doomed load_all() call and surfacing a raw provider
# error to the user.
_TICKER_SHAPE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")

# UI disambiguation heuristic only -- NOT an investment-policy threshold
# (Hard Rule #1 doesn't apply; this never influences which ticker is
# analyzed, only whether the confirmation caption discloses that other
# equities scored close to the one picked). Deliberately local to this
# module rather than constants.py, same rationale system_health.py's own
# observability windows use. A candidate within this fraction of the top
# score is disclosed as an alternate (2026-09-21 UX audit I3).
_AMBIGUOUS_MATCH_SCORE_MARGIN = 0.15


def looks_like_ticker(raw: str) -> bool:
    """True if `raw` is shaped like a real ticker symbol.

    Case-insensitive on the input; the shape check itself is upper-case.
    """
    if not raw:
        return False
    return bool(_TICKER_SHAPE.match(raw.strip().upper()))


def resolve_company_name(query: str, max_results: int = 8) -> dict | None:
    """Resolve free-text (a company name) to its most likely ticker via
    yfinance's Search.

    Returns ``{"symbol": str, "name": str, "score": float, "alternates":
    list[dict]}`` for the top-scored EQUITY-type match, or ``None`` on no
    match, no equity-type result, or any exception (network, library,
    malformed response). ``alternates`` (2026-09-21 UX audit I3) is a list
    of ``{"symbol", "name"}`` for any OTHER equity match that scored within
    ``_AMBIGUOUS_MATCH_SCORE_MARGIN`` of the top pick — e.g. a query like
    "Alphabet" that could plausibly mean GOOGL or GOOG — so the caller can
    disclose that a choice was made among real alternatives, not just what
    was picked. Always a list, never ``None``; empty when the top match was
    clearly ahead of the field.
    """
    if not query or not query.strip():
        return None
    try:
        import yfinance as yf

        # yfinance's own default is timeout=30 (confirmed 2026-09-21 audit
        # follow-up), which is too long for an interactive lookup on this
        # page — pass an explicit shorter bound, mirroring earnings_intel's
        # timeout=8 for the same "quick interactive call" shape.
        result = yf.Search(query.strip(), max_results=max_results, timeout=8)
        # 2026-09-24 app review, Q2 (antipattern-baseline decision): this
        # whole function is one try/except returning None on ANY exception
        # (a genuine yfinance failure never reaches this line at all) --
        # `or []` here only collapses "no `quotes` attribute" / "quotes is
        # already empty" into the SAME empty-list shape, both of which fall
        # through to the `if not equities: return None` below regardless.
        # No path exists where a real failure gets misread as "searched,
        # zero results."
        quotes = getattr(result, "quotes", None) or []
        equities = [
            q for q in quotes
            if q.get("quoteType") == "EQUITY" and q.get("symbol")
        ]
        if not equities:
            return None
        # yfinance already returns quotes sorted by score descending; sort
        # explicitly anyway as defense-in-depth in case that ordering
        # guarantee ever changes upstream.
        equities.sort(key=lambda q: q.get("score", 0) or 0, reverse=True)
        top = equities[0]
        name = top.get("longname") or top.get("shortname") or top["symbol"]
        top_score = float(top.get("score") or 0)
        top_symbol = str(top["symbol"]).upper()
        alternates = []
        if top_score > 0:
            for q in equities[1:]:
                q_score = float(q.get("score", 0) or 0)
                q_symbol = str(q.get("symbol", "")).upper()
                if q_symbol and q_symbol != top_symbol \
                        and q_score >= top_score * (1 - _AMBIGUOUS_MATCH_SCORE_MARGIN):
                    alternates.append({
                        "symbol": q_symbol,
                        "name": str(q.get("longname") or q.get("shortname") or q_symbol),
                    })
        return {
            "symbol": top_symbol,
            "name": str(name),
            "score": top_score,
            "alternates": alternates,
        }
    except Exception:
        return None
