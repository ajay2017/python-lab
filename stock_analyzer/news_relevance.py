"""
Headline relevance -- does a headline's TITLE name the company (or ticker)?

Why this exists (docs/plans/headline-relevance.md): headlines come from `yf.Search`
filtered on Yahoo's `relatedTickers`, which tags every ticker a story mentions in
passing -- ABBV got "Is Amgen Stock Increasing Your Market Risk?", MRVL got "Dell Stock
Closes Its Worst Week in Years". Applied ONCE in `bundle_loader`, so every news
consumer (sentiment pillar, Act Today / Review, Rebalancer flags, the add block,
Grow Today's negative-news Skip, the shared entry check, News Intelligence) reads
only headlines that are about the holding. Deterministic, no LLM (production has
none).

Title-only by design (R5): a title that does not name the company is not counted,
even if the story is about it. No usable company name -> fail open (R6): every
tagged headline is kept, exactly as before this module existed.

Pure: no network, no clock, no Streamlit.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Trailing legal words stripped from a company name (compared lower-case, with any
# trailing "." removed).
_LEGAL = frozenset({
    "inc", "corp", "corporation", "co", "company", "plc", "ltd", "limited",
    "se", "nv", "n.v", "ag", "sa", "s.a", "holdings", "holding", "group", "new", "&",
    "and", "l", "lp", "l.p",
})
_CLASS_LETTERS = frozenset({"a", "b", "c"})
# Trailing industry words trimmed to get a shorter, headline-style core
# ("Meta Platforms" -> "Meta"). Never trimmed to empty.
_INDUSTRY = frozenset({
    "technology", "technologies", "platforms", "markets", "financial",
    "semiconductor", "semiconductors", "systems", "software", "devices",
    "communications", "pharmaceuticals", "therapeutics", "entertainment",
    "bancorp", "industries", "international",
})
# A final token that is a prefix (>= 5 chars) of one of these is a truncated word
# ("Capital One Financial Corporati").
_PARTIAL_WORDS = tuple(sorted(
    {w for w in (_LEGAL | _INDUSTRY) if len(w) > 5}))
_PARTIAL_MIN = 5
_KNOWN_WORDS = _LEGAL | _INDUSTRY    # a complete word is never a truncated one

# Common words that must never stand alone as a name term (first-token rule, and a
# whole core that is just one of these).
_STOPLIST = frozenset({
    "capital", "advanced", "general", "american", "first", "united", "national",
    "target", "bank", "digital", "applied", "main", "best", "block", "match",
    "gap", "ball", "dollar", "global", "western", "southern", "northern",
    "eastern", "space", "home", "international", "texas", "union", "boston",
    "super", "morgan", "walt", "charles", "philip", "u.s.",
    "realty", "illinois", "palo", "marathon", "unity", "sea", "intuitive", "warner",
})

# Curated company/brand aliases (R4: no CEO names, no product names). Share-class
# siblings carry the same aliases.
_ALIASES = {
    "XOM": ("Exxon", "ExxonMobil"),
    "GOOGL": ("Google", "Alphabet"),
    "GOOG": ("Google", "Alphabet"),
    "META": ("Facebook", "Instagram", "WhatsApp"),
    "ON": ("onsemi",),
    "JPM": ("JPMorgan", "JP Morgan", "JPMorganChase"),
    "BRK-B": ("Berkshire",),
    "BRK-A": ("Berkshire",),
    "SPCX": ("SpaceX",),
    "SO": ("Southern", "Southern Company", "Southern Co"),
    "DIS": ("Disney",),
    "SCHW": ("Schwab",),
    "LLY": ("Lilly", "Eli Lilly"),
    "BMY": ("Bristol Myers", "Bristol-Myers"),
    "SMCI": ("Supermicro", "Super Micro"),
    "OXY": ("Oxy", "Occidental"),
    "MMM": ("3M",),
    "JD": ("JD.com",),
    "ONON": ("On Holding", "On Running"),
    "SE": ("Sea",),
    "TGT": ("Target",),
    "XYZ": ("Block",),
    "U": ("Unity",),
    "PANW": ("Palo Alto",),
    "MPC": ("Marathon Petroleum", "Marathon"),
    "WBD": ("Warner Bros", "Warner Bros. Discovery"),
    "USB": ("U.S. Bancorp", "US Bancorp", "U.S. Bank"),
}

# Bare uppercase tickers that are also everyday acronyms/words: never count bare.
_ACRONYMS = frozenset({
    "AI", "IT", "EV", "ETF", "IPO", "CEO", "CFO", "GDP", "CPI", "PPI", "SEC",
    "FDA", "FED", "USA", "US", "UK", "EU", "ESG", "TV", "PC", "OR", "ARE", "ALL",
    "NOW", "ON", "KEY", "ONE", "NEW", "BIG", "CAN", "GO", "BE", "SO", "HAS",
})
_BARE_MIN_LEN = 3
_STOCK_FORM_MIN_LEN = 2
_MIN_TERM_LEN = 3            # terms of 2 chars or fewer are discarded
_CASE_SENSITIVE_MAX_LEN = 4  # single-token terms this short must match case

_EXCHANGES = r"(?:NYSE|NASDAQ|Nasdaq|NYSEARCA|AMEX)"


def item_title(item) -> str:
    """Title of a news item: flat `title`, else `content.title`, else ''."""
    if not isinstance(item, dict):
        return ""
    t = item.get("title")
    if not t:
        content = item.get("content")
        t = content.get("title") if isinstance(content, dict) else ""
    return str(t).strip() if t else ""


def _tokens(name) -> list[str]:
    s = re.sub(r"\(.*?\)", " ", str(name or ""))
    s = re.sub(r"\([^)]*$", " ", s)           # an unclosed (truncated) parenthetical
    s = s.split(" - ")[0]                      # "ASML Holding N.V. - New York Re"
    s = s.replace(",", " ")
    s = re.sub(r"\.com\b", " ", s, flags=re.I)
    toks = [w for w in s.split() if w]
    if len(toks) > 1 and toks[0].lower() == "the":
        toks.pop(0)
    # A truncated "Common Stock" suffix ("Booking Holdings Inc. Common St").
    if (len(toks) > 2 and toks[-2].lower() == "common" and len(toks[-1]) >= 2
            and "stock".startswith(toks[-1].lower().rstrip("."))):
        toks = toks[:-2]
    return toks


def _norm(tok: str) -> str:
    return tok.lower().rstrip(".")


def _strip_legal(toks: list[str]) -> list[str]:
    toks = list(toks)
    while len(toks) > 1:
        last = _norm(toks[-1])
        if last in _LEGAL:
            toks.pop()
            continue
        # "Class A" / "Class B" / "Class C"
        if last in _CLASS_LETTERS and len(toks) > 2 and _norm(toks[-2]) == "class":
            toks.pop()
            toks.pop()
            continue
        if last == "class":
            toks.pop()
            continue
        # A one-letter fragment after a legal word ("Group I" -> truncated "Group Inc").
        if len(last) == 1 and len(toks) > 2 and _norm(toks[-2]) in _LEGAL:
            toks.pop()
            continue
        # A truncated trailing legal/industry word ("Corporati").
        if (len(last) >= _PARTIAL_MIN and last not in _KNOWN_WORDS
                and any(w.startswith(last) for w in _PARTIAL_WORDS)):
            toks.pop()
            continue
        break
    return toks


def _trim_industry(toks: list[str]) -> list[str]:
    core = list(toks)
    while len(core) > 1 and _norm(core[-1]) in _INDUSTRY:
        core.pop()
    return core


def name_terms(ticker: str, names) -> tuple[tuple[str, bool], ...]:
    """Search terms for a company: ((term, case_sensitive), ...). From each name:
    the legal-stripped core, an industry-trimmed core, and the first token (>= 4
    chars, not a common word); plus curated aliases. Empty when nothing usable."""
    terms: list[str] = []
    for n in (names or ()):
        toks = _strip_legal(_tokens(n))
        if not toks:
            continue
        terms.append(" ".join(toks))
        terms.append(" ".join(_trim_industry(toks)))
        if len(toks[0]) >= 4 and toks[0].lower() not in _STOPLIST:
            terms.append(toks[0])
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()

    def _add(t: str, curated: bool = False) -> None:
        t = t.strip()
        if (len(t) < (1 if curated else _MIN_TERM_LEN)) or t in seen:
            return
        single = " " not in t
        if single and not curated and t.lower() in _STOPLIST:
            return                       # a common word is not a company name
        seen.add(t)
        # Case-sensitive only when a lower-case hit would be a common word (alias is
        # a stoplist word: "Target", "Block", "Unity") or the term is short ("Oxy",
        # "3M"); distinctive brand aliases ("onsemi", "SpaceX") match any case.
        out.append((t, single and (len(t) <= _CASE_SENSITIVE_MAX_LEN
                                   or (curated and t.lower() in _STOPLIST))))

    for t in terms:
        _add(t)
    for a in _ALIASES.get(str(ticker or "").upper(), ()):
        _add(a, curated=True)
    return tuple(out)


def _boundary(term: str) -> str:
    return r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"


@lru_cache(maxsize=2048)
def _term_re(term: str, case_sensitive: bool):
    return re.compile(_boundary(term), 0 if case_sensitive else re.I)


@lru_cache(maxsize=512)
def _ticker_res(ticker: str):
    """(always_count_patterns, bare_pattern_or_None, stock_pattern_or_None)."""
    tk = ticker.upper()
    forms = {tk}
    if "-" in tk:
        forms.add(tk.replace("-", "."))
        forms.add(tk.replace("-", "/"))
    always, bare, stock = [], [], []
    for f in sorted(forms):
        e = re.escape(f)
        always.append(re.compile(r"\$" + e + r"(?![A-Za-z0-9])"))
        always.append(re.compile(r"\(\s*(?:[A-Za-z]+\s*:\s*)?" + e + r"\s*\)"))
        always.append(re.compile(_EXCHANGES + r"\s*:\s*" + e + r"(?![A-Za-z0-9])"))
        if len(f) >= _BARE_MIN_LEN and f not in _ACRONYMS:
            bare.append(re.compile(_boundary(f)))
        if len(f) >= _STOCK_FORM_MIN_LEN:
            stock.append(re.compile(_boundary(f) + r"\s+(?:[Ss]tock|[Ss]hares)\b"))
    return tuple(always), tuple(bare), tuple(stock)


def is_relevant(title: str, ticker: str, terms) -> bool:
    """True when `title` names the company (any term, word-boundaried) or the
    ticker (`$T`, `(T)`, `NASDAQ: T`, bare uppercase T for len >= 3 non-acronyms in
    a title that is not all-caps, `T Stock` / `T Shares`)."""
    title = str(title or "")
    if not title.strip():
        return False
    for term, cs in (terms or ()):
        if _term_re(term, bool(cs)).search(title):
            return True
    tk = str(ticker or "").strip().upper()
    if not tk:
        return False
    always, bare, stock = _ticker_res(tk)
    if any(p.search(title) for p in always):
        return True
    if any(p.search(title) for p in stock):
        return True
    if re.search(r"[a-z]", title) and any(p.search(title) for p in bare):
        return True
    return False


def split_relevant(items, ticker: str, names) -> dict:
    """{"kept": [...], "unrelated": [...], "checked": bool}. Order preserved. Items
    with no title are dropped from both lists. No usable company name or alias ->
    checked False and every item is kept (fail open: today's behaviour)."""
    items = [i for i in (items or []) if isinstance(i, dict)]
    terms = name_terms(ticker, names)
    if not terms:
        return {"kept": list(items), "unrelated": [], "checked": False}
    kept, unrelated = [], []
    for it in items:
        title = item_title(it)
        if not title:
            continue
        (kept if is_relevant(title, ticker, terms) else unrelated).append(it)
    return {"kept": kept, "unrelated": unrelated, "checked": True}


def filter_scored(rows, ticker: str, names, key: str = "headline") -> list:
    """Relevance-filter already-scored headline rows (the LLM day-cache shape, where
    the title lives under `key`). Fails open (returns all rows) when no name."""
    rows = list(rows or [])
    terms = name_terms(ticker, names)
    if not terms:
        return rows
    return [r for r in rows
            if isinstance(r, dict) and is_relevant(str(r.get(key) or ""), ticker, terms)]
