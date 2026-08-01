"""
Portfolio Q&A — retrospective natural-language Q&A over trade history and
past recommendations, for the 💬 Ask tab on 🧠 AI Insights.

This is NOT a live session_state reader: the user's actual use cases are
retrospective ("how many trades did I make last week and what was the
gain/loss on each", "why did the stock I bought at composite 75 still lose
money over the next 5 days") — questions over history, not the current page's
caches. See docs/plans/portfolio-qa.md.

Two-step LLM pattern, not an agentic tool-loop (the query shapes are fixed
and few, so a tool-calling loop would add cost/failure surface for no
benefit): parse_question() turns free text into a structured query, a plain
Python function runs the deterministic lookup, narrate_answer() turns the
result into plain English. Every LLM call fails open (None on any error) and
every narration is instructed to use ONLY the facts it's given — same
"no invented specifics" discipline as thesis_red_team.py.

Pure logic — no Streamlit imports, no app.py imports.
"""

import json

import pandas as pd

from stock_analyzer.constants import (
    LLM_REQUEST_TIMEOUT_SEC,
    QA_REC_OUTCOME_DEFAULT_HORIZON_DAYS,
    QA_MAX_RANGE_DAYS,
)

_MODEL = "claude-haiku-4-5-20251001"

_VALID_INTENTS = ("trades_in_range", "rec_outcome", "trade_lookup", "unsupported")

_REASON_NO_DATE_RANGE = (
    "That sounds like a question about a range of trades, but I need both "
    "a start and an end date to answer it."
)
_REASON_NO_TICKER = (
    "I couldn't confidently identify a stock ticker in that question — try "
    "naming the exact symbol (e.g. \"HOOD\") or the company's full name."
)
_REASON_GENERIC = "That doesn't match a question I can answer yet."


def _f(val, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        v = float(val)
        return default if (v != v) else v  # NaN check
    except (TypeError, ValueError):
        return default


# ─── Step 1: parse the free-text question into a structured query ──────────

_PARSE_SYSTEM_PROMPT_TEMPLATE = (
    "You convert a portfolio question into a structured query. Today's date "
    "is {today} — resolve any relative phrase (\"last week\", \"today\", "
    "\"5 trading days ago\") against this date, never your own guess of the "
    "current date.\n\n"
    "Respond with ONLY a JSON object, no other text before or after:\n"
    '{"intent": "trades_in_range" | "rec_outcome" | "trade_lookup" | "unsupported", '
    '"ticker": "<UPPERCASE TICKER>" or null, '
    '"start_date": "YYYY-MM-DD" or null, '
    '"end_date": "YYYY-MM-DD" or null, '
    '"horizon_days": <int> or null, '
    '"reason": "<short explanation, ONLY when intent is unsupported>" or null}\n\n'
    "Rules:\n"
    "- \"trades_in_range\" = a question about trades/gain-loss over a date "
    "range. Requires start_date and end_date.\n"
    "- \"trade_lookup\" = a question about what happened on a specific trade "
    "or ticker, with NO date range given (e.g. \"what was my trade on X\", "
    "\"how did my AAPL trade go\"). Requires ticker; leave start_date/"
    "end_date/horizon_days null.\n"
    "- \"rec_outcome\" = a question about why a past recommendation did or "
    "didn't work out. Requires ticker; start_date is the date the "
    "recommendation was made if given, else null; horizon_days is the "
    "number of trading days after that the user is asking about, if given, "
    "else null.\n"
    "- Only extract a ticker if one is explicitly named as a stock symbol or "
    "an unambiguous company name — never guess one. If the name in the "
    "question doesn't map to a specific ticker with confidence, leave "
    "ticker null.\n"
    "- If the question doesn't fit any of the three shapes above, or needs "
    "a ticker/date that isn't given, return intent \"unsupported\" with "
    "every other field null EXCEPT reason: a short (under 20 words), "
    "specific, plain-English explanation of what's missing or unclear — "
    "e.g. \"I could not identify an unambiguous stock ticker in this "
    "question\" or \"this needs a start and end date, which weren't given.\"\n"
    "- Output the JSON object and NOTHING else — no markdown code fence, no "
    "explanation before it, and no explanation after it, even if the intent "
    "is \"unsupported\" (the \"reason\" field IS the explanation — it "
    "belongs inside the JSON, not alongside it). The response must contain "
    "exactly one JSON object and no other characters."
)


def build_parse_prompt(today_et) -> str:
    """today_et: a date/str already resolved by the caller (app.py's _today_et())."""
    today_str = today_et.isoformat() if hasattr(today_et, "isoformat") else str(today_et)[:10]
    # .replace(), not .format() — the template's JSON example is full of
    # literal { } that .format() would misparse as fields.
    return _PARSE_SYSTEM_PROMPT_TEMPLATE.replace("{today}", today_str)


def _extract_json_object(s: str) -> str | None:
    """Return the first balanced {...} substring in s, or None if there
    isn't one. Depth-counted (not a bare find/rfind) so trailing prose
    AFTER a well-formed JSON object — which Haiku sometimes emits even
    when told to respond with ONLY JSON, e.g. an explanation tacked on
    after a ```json fence with no closing fence — doesn't get swept into
    the "JSON" text and break json.loads."""
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def parse_parsed_query(text) -> dict | None:
    """Validate a raw Haiku response into the structured query dict, or None
    on any failure. Never raises."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].strip()
    extracted = _extract_json_object(cleaned)
    if extracted is not None:
        cleaned = extracted
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None

    intent = str(parsed.get("intent", "")).strip()
    if intent not in _VALID_INTENTS:
        return None

    ticker = parsed.get("ticker")
    ticker = str(ticker).strip().upper() if ticker else None

    def _valid_date(v):
        if v is None:
            return None
        s = str(v).strip()
        try:
            pd.Timestamp(s)
        except Exception:
            return None
        return s[:10]

    start_date = _valid_date(parsed.get("start_date"))
    end_date   = _valid_date(parsed.get("end_date"))

    horizon = parsed.get("horizon_days")
    try:
        horizon = int(horizon) if horizon is not None else None
        if horizon is not None and horizon <= 0:
            horizon = None
    except (TypeError, ValueError):
        horizon = None

    model_reason = parsed.get("reason")
    model_reason = str(model_reason).strip()[:200] if model_reason else None

    def _unsupported(reason: str) -> dict:
        return {"intent": "unsupported", "ticker": None, "start_date": None,
                "end_date": None, "range_clamped": False, "horizon_days": None,
                "reason": reason}

    if intent == "trades_in_range" and not (start_date and end_date):
        return _unsupported(_REASON_NO_DATE_RANGE)
    if intent in ("rec_outcome", "trade_lookup") and not ticker:
        return _unsupported(_REASON_NO_TICKER)
    if intent == "unsupported":
        return _unsupported(model_reason or _REASON_GENERIC)

    range_clamped = False
    if intent == "trades_in_range" and start_date and end_date:
        span_days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
        if span_days > QA_MAX_RANGE_DAYS:
            # Clamp, don't reject — visible truncation (surfaced via
            # range_clamped, shown to the user before answering), not a
            # silent "can't answer" or a silently-wrong wide fetch.
            start_date = (pd.Timestamp(end_date) - pd.Timedelta(days=QA_MAX_RANGE_DAYS)).date().isoformat()
            range_clamped = True

    return {
        "intent": intent,
        "ticker": ticker,
        "start_date": start_date,
        "end_date": end_date,
        "range_clamped": range_clamped,
        "horizon_days": horizon,
        "reason": None,
    }


LAST_PARSE_ERROR: str | None = None


def parse_question(question: str, api_key: str, today_et,
                    model: str = _MODEL, max_tokens: int = 300) -> dict | None:
    """Returns the validated structured query dict, or None on ANY failure
    (no key, API error, malformed response). Fail-open — never raises.
    On None, LAST_PARSE_ERROR carries the real reason (mirrors
    analyst_intel.LAST_EXTRACT_ERROR) so the caller can show a "Details:"
    caption instead of a mute failure."""
    global LAST_PARSE_ERROR
    LAST_PARSE_ERROR = None
    if not api_key or not question or not str(question).strip():
        LAST_PARSE_ERROR = "no API key configured or empty question"
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=build_parse_prompt(today_et),
            messages=[{"role": "user", "content": str(question).strip()}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text = response.content[0].text.strip() if response.content else ""
        result = parse_parsed_query(text)
        if result is None:
            LAST_PARSE_ERROR = f"model returned an unparseable response: {text[:200]!r}"
        return result
    except Exception as e:
        LAST_PARSE_ERROR = f"{type(e).__name__}: {e}"[:300]
        return None


# ─── Step 2: deterministic queries (zero LLM) ───────────────────────────────

def _row_outcome(r, current_prices: dict) -> dict:
    """Per-trade gain/loss for one trades_df row. Shared by trades_in_range
    and trades_for_ticker so both surfaces use identical P&L logic.

    SELLs report the already-stored realized_pnl directly (no recomputation).
    BUYs report unrealized P&L against current_prices (caller-supplied —
    e.g. from the session's _port_df_enriched, so this stays a pure function
    with no data-fetch of its own) when the ticker is still held; a BUY whose
    position has since been fully sold is labeled "position_closed" rather
    than attempting new FIFO-lot matching — its realized P&L lives on the
    matching SELL row instead.

    Returns {"ticker", "action", "shares", "price", "traded_at",
    "pnl": float|None, "pnl_label": "realized"|"unrealized"|"position_closed"|"unknown"}.
    """
    ticker = str(r.get("ticker", "")).upper()
    action = str(r.get("action", "")).upper()
    shares = _f(r.get("shares"))
    price  = _f(r.get("price"))
    row = {
        "ticker": ticker, "action": action, "shares": shares, "price": price,
        "traded_at": str(r.get("traded_at"))[:10],
        "pnl": None, "pnl_label": "unknown",
    }
    if action == "SELL":
        rp = r.get("realized_pnl")
        if rp is not None:
            try:
                rp_f = float(rp)
                if rp_f == rp_f:  # not NaN
                    row["pnl"] = round(rp_f, 2)
                    row["pnl_label"] = "realized"
            except (TypeError, ValueError):
                pass
    elif action == "BUY":
        cur = current_prices.get(ticker)
        if cur is not None and price > 0:
            row["pnl"] = round((_f(cur) - price) * shares, 2)
            row["pnl_label"] = "unrealized"
        else:
            row["pnl_label"] = "position_closed"
    return row


def _real_trades(trades_df):
    """trades_df with synthetic SPLIT rows excluded — shared filter.

    format="ISO8601" is required, not optional (see app.py's Trade History
    table, which documents the same fix): without it, pd.to_datetime infers
    a single format from the FIRST row and silently coerces every row that
    doesn't match — e.g. a bare-date "RH text import" row (no time/offset)
    sitting alongside full-timestamp rows — to NaT via errors="coerce",
    which then renders as the literal string "NaT" downstream. Confirmed
    live: a CRWD BUY imported this way showed "NaT" in the Ask tab's trade
    table until this was added."""
    df = trades_df.copy()
    df["traded_at"] = pd.to_datetime(df["traded_at"], utc=True, errors="coerce", format="ISO8601")
    return df[~df["action"].astype(str).str.upper().str.contains("SPLIT", na=False)]


def trades_in_range(trades_df, start_date, end_date, current_prices: dict | None = None) -> list[dict]:
    """Every real (non-SPLIT) trade between start_date and end_date inclusive,
    each with its gain/loss (see _row_outcome)."""
    current_prices = current_prices or {}
    if trades_df is None or trades_df.empty:
        return []

    df = _real_trades(trades_df)
    try:
        sd = pd.Timestamp(str(start_date)[:10], tz="UTC")
        ed = pd.Timestamp(str(end_date)[:10], tz="UTC") + pd.Timedelta(days=1)
    except Exception:
        return []
    df = df[(df["traded_at"] >= sd) & (df["traded_at"] < ed)]

    return [_row_outcome(r, current_prices) for _, r in df.sort_values("traded_at").iterrows()]


def trades_for_ticker(trades_df, ticker: str, current_prices: dict | None = None) -> list[dict]:
    """Every real (non-SPLIT) trade ever recorded for `ticker`, oldest first,
    each with its gain/loss (see _row_outcome). No date range — for
    "what was my trade on X" style questions. Empty list (not None) when the
    ticker has no trade history; the caller/narration must say so plainly."""
    current_prices = current_prices or {}
    ticker = str(ticker).upper().strip()
    if trades_df is None or trades_df.empty or not ticker:
        return []

    df = _real_trades(trades_df)
    df = df[df["ticker"].astype(str).str.upper() == ticker]
    return [_row_outcome(r, current_prices) for _, r in df.sort_values("traded_at").iterrows()]


_PNL_LABEL_DISPLAY = {
    "realized":        "Realized",
    "unrealized":       "Unrealized (still held)",
    "position_closed": "Position closed (see matching sale)",
    "unknown":          "—",
}


def format_trades_table(facts: list[dict]) -> pd.DataFrame:
    """Render a trades_in_range/trades_for_ticker fact list as a DataFrame
    with business-language column headers and status labels, for the "Show
    the underlying trades" expander — the raw dict keys (pnl, pnl_label,
    traded_at, ...) are internal field names, not something to show a user."""
    df = pd.DataFrame(facts)
    if df.empty:
        return df
    df["pnl_label"] = df["pnl_label"].map(lambda v: _PNL_LABEL_DISPLAY.get(v, v))
    return df.rename(columns={
        "ticker":     "Ticker",
        "action":     "Action",
        "shares":     "Shares",
        "price":      "Price ($)",
        "traded_at":  "Date",
        "pnl":        "Gain/Loss ($)",
        "pnl_label":  "Status",
    })


def recommendation_outcome(ticker: str, rec_date, recs_df, price_history_df=None,
                            horizon_days: int | None = None) -> dict:
    """
    Look up the recommendation surfaced for `ticker` on `rec_date` (exact-date
    match — no "nearest recommendation" guessing) and, if given a price
    history DataFrame spanning that date forward, the price move over the
    next `horizon_days` trading days.

    recs_df: caller-loaded via db.load_recommendations() for a range covering
    rec_date (pure function — no DB access here).
    price_history_df: caller-fetched via data.fetch_price_history(ticker, ...)
    with enough forward history past rec_date to cover horizon_days.

    Returns {"found": False, "reason": str} when no matching recommendation
    exists — never guesses. On a match, t_score/bq_score/val_score are None
    for recommendations surfaced before pillar-score persistence shipped —
    the caller/narration must say so plainly, not infer a reason.
    """
    horizon_days = horizon_days or QA_REC_OUTCOME_DEFAULT_HORIZON_DAYS
    ticker = str(ticker).upper().strip()
    rd_str = rec_date.isoformat() if hasattr(rec_date, "isoformat") else str(rec_date)[:10]

    if recs_df is None or recs_df.empty or not ticker:
        return {"found": False, "reason": "no recommendation on record for that ticker/date"}

    matches = recs_df[
        (recs_df["ticker"].astype(str).str.upper() == ticker) &
        (recs_df["rec_date"].astype(str).str[:10] == rd_str)
    ]
    if matches.empty:
        return {"found": False, "reason": "no recommendation on record for that ticker/date"}

    rec = matches.iloc[0].to_dict()
    result = {
        "found": True,
        "ticker": ticker,
        "rec_date": rd_str,
        "rec_type": rec.get("rec_type"),
        "composite_score": rec.get("composite_score"),
        "conviction": rec.get("conviction"),
        "thesis": rec.get("thesis") or None,
        "t_score": rec.get("t_score"),
        "bq_score": rec.get("bq_score"),
        "val_score": rec.get("val_score"),
        "price_at_surface": rec.get("price_at_surface"),
        "horizon_days": horizon_days,
        "price_at_horizon": None,
        "pct_move": None,
    }

    if price_history_df is None or price_history_df.empty or "Close" not in price_history_df.columns:
        return result

    try:
        hist = price_history_df.copy()
        hist.index = pd.to_datetime(hist.index)
        rd_ts = pd.Timestamp(rd_str)
        on_or_after = hist[hist.index.normalize() >= rd_ts]
        if len(on_or_after) <= horizon_days:
            return result  # not enough forward history yet to answer
        price_at_horizon = float(on_or_after["Close"].iloc[horizon_days])
    except Exception:
        return result

    result["price_at_horizon"] = round(price_at_horizon, 2)
    pas = result.get("price_at_surface")
    if pas is not None:
        try:
            pas_f = float(pas)
            if pas_f > 0:
                result["pct_move"] = round((price_at_horizon - pas_f) / pas_f * 100.0, 2)
        except (TypeError, ValueError):
            pass
    return result


# ─── Step 3: narrate the facts in plain English ─────────────────────────────

_NARRATE_SYSTEM_PROMPT = (
    "You explain a portfolio query result to the investor who asked it. "
    "Use ONLY the facts given below — never invent a number, a reason, or a "
    "pillar score that isn't present. If a value is missing or null "
    "(especially a pillar sub-score), say plainly that it wasn't recorded "
    "rather than guessing why. Do not recommend any future action and do "
    "not restate a threshold — just explain what already happened. Keep it "
    "to 2-4 short sentences."
)


def _trade_list_lines(header: str, facts: list[dict]) -> str:
    lines = [header]
    for t in facts:
        pnl = f"{t['pnl']:+.2f}" if t.get("pnl") is not None else "n/a"
        lines.append(
            f"- {t['traded_at']}: {t['action']} {t['shares']:g} {t['ticker']} "
            f"@ ${t['price']:.2f} — P&L {pnl} ({t['pnl_label']})"
        )
    return "\n".join(lines)


def facts_to_text(intent: str, facts) -> str:
    if intent == "trades_in_range":
        if not facts:
            return "No trades were recorded in this date range."
        return _trade_list_lines("Trades in range:", facts)

    if intent == "trade_lookup":
        if not facts:
            return "No trades on record for that ticker."
        return _trade_list_lines("Trades on record for this ticker:", facts)

    if intent == "rec_outcome":
        if not facts.get("found"):
            return f"No recommendation on record: {facts.get('reason')}"
        lines = [
            f"Ticker: {facts['ticker']}",
            f"Recommendation date: {facts['rec_date']} (type: {facts.get('rec_type')})",
            f"Composite score at surfacing: {facts.get('composite_score')}",
            f"Conviction: {facts.get('conviction')}",
            f"Thesis note recorded at the time: {facts.get('thesis') or 'none recorded'}",
        ]
        for label, key in (("Technical pillar", "t_score"),
                           ("Business-quality pillar", "bq_score"),
                           ("Valuation pillar", "val_score")):
            v = facts.get(key)
            lines.append(f"{label}: {v if v is not None else 'not recorded for this recommendation'}")
        if facts.get("price_at_horizon") is not None:
            lines.append(
                f"Price at surfacing: ${facts.get('price_at_surface')}; "
                f"price {facts['horizon_days']} trading days later: ${facts['price_at_horizon']} "
                f"({facts['pct_move']:+.1f}%)"
            )
        else:
            lines.append("Not enough forward price history yet to compute the outcome move.")
        return "\n".join(lines)

    return "Unsupported question."


LAST_NARRATE_ERROR: str | None = None


def narrate_answer(intent: str, facts, api_key: str,
                    model: str = _MODEL, max_tokens: int = 400) -> str | None:
    """Returns the plain-English answer, or None on ANY failure (no key, API
    error). Fail-open — never raises. Caller must show an explicit
    'AI layer offline' state on None, never a fabricated answer. On None,
    LAST_NARRATE_ERROR carries the real reason (mirrors LAST_PARSE_ERROR)."""
    global LAST_NARRATE_ERROR
    LAST_NARRATE_ERROR = None
    if not api_key:
        LAST_NARRATE_ERROR = "no API key configured"
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=_NARRATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": facts_to_text(intent, facts)}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        text = response.content[0].text.strip() if response.content else ""
        if not text:
            LAST_NARRATE_ERROR = "model returned an empty response"
            return None
        # Escape literal "$" before the caller renders this via st.markdown —
        # Streamlit treats a $...$ pair as inline LaTeX math, so any answer
        # mentioning two or more dollar amounts (routine here) silently
        # swallows the prose between the first and second "$" into a
        # garbled math span instead of plain text. The escape keeps the
        # dollar sign literal without changing the actual figures.
        return text.replace("$", "\\$")
    except Exception as e:
        LAST_NARRATE_ERROR = f"{type(e).__name__}: {e}"[:300]
        return None
