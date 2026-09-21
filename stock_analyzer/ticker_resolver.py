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

    Returns ``{"symbol": str, "name": str, "score": float}`` for the
    top-scored EQUITY-type match, or ``None`` on no match, no equity-type
    result, or any exception (network, library, malformed response).
    """
    if not query or not query.strip():
        return None
    try:
        import yfinance as yf

        result = yf.Search(query.strip(), max_results=max_results)
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
        return {
            "symbol": str(top["symbol"]).upper(),
            "name": str(name),
            "score": float(top.get("score") or 0),
        }
    except Exception:
        return None
