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
    QA_HISTORY_TURNS,
    QA_PREMORTEM_TRADE_MATCH_WINDOW_DAYS,
)
from stock_analyzer.investor_mirror import build_closed_lots

_MODEL = "claude-haiku-4-5-20251001"

_VALID_INTENTS = (
    "trades_in_range", "rec_outcome", "trade_lookup",
    "holding_lookup", "portfolio_summary", "sector_composition",
    "unsupported",
)

_REASON_NO_DATE_RANGE = (
    "That sounds like a question about a range of trades, but I need both "
    "a start and an end date to answer it."
)
_REASON_NO_TICKER = (
    "I couldn't confidently identify a stock ticker in that question — try "
    "naming the exact symbol (e.g. \"HOOD\") or the company's full name."
)
_REASON_GENERIC = "That doesn't match a question I can answer yet."
_REASON_NOT_HELD = (
    "You don't currently hold that ticker — I can only answer this for a "
    "position you're holding right now."
)


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
    '{"intent": "trades_in_range" | "rec_outcome" | "trade_lookup" | '
    '"holding_lookup" | "portfolio_summary" | "sector_composition" | "unsupported", '
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
    "- \"holding_lookup\" = a question about the user's CURRENT position in "
    "one specific ticker — shares held, cost basis, current unrealized "
    "P&L, weight, composite score. E.g. \"what's my position in DELL\", "
    "\"how many shares of AAPL do I own\", \"am I up or down on MSFT right "
    "now\". Requires ticker; leave start_date/end_date/horizon_days null. "
    "Distinguish clearly from \"trade_lookup\": trade_lookup is about past "
    "TRANSACTIONS/trade history (\"what was my trade on X\", \"how did my X "
    "trade go\"); holding_lookup is about the CURRENT position snapshot "
    "right now.\n"
    "- \"portfolio_summary\" = a question about the portfolio's current "
    "AGGREGATE state across all holdings — total value, total unrealized "
    "P&L, number of positions, biggest winner/loser by P&L%. E.g. \"how is "
    "my portfolio doing\", \"what's my total unrealized P&L\", \"what's my "
    "biggest winner\". No ticker/date needed — leave all of ticker/"
    "start_date/end_date/horizon_days null. IMPORTANT scope boundary: if "
    "the question asks about performance over a SPECIFIC PAST PERIOD (e.g. "
    "\"how did I do last month\", \"what was my return this quarter\"), "
    "that is NOT portfolio_summary — return intent \"unsupported\" with "
    "reason \"I can tell you your current total P&L, but not performance "
    "over a specific past period yet.\" portfolio_summary is a snapshot of "
    "right now, never a time-boxed return calculation — do not conflate "
    "the two.\n"
    "- \"sector_composition\" = a question about how the portfolio is split "
    "across sectors — e.g. \"what sector am I heaviest in\", \"how is my "
    "portfolio diversified by sector\", \"what's my exposure by sector\". "
    "No ticker/date needed.\n"
    "- Only extract a ticker if one is explicitly named as a stock symbol or "
    "an unambiguous company name — never guess one. If the name in the "
    "question doesn't map to a specific ticker with confidence, leave "
    "ticker null.\n"
    "- If the question doesn't fit any of the shapes above, or needs "
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


def _format_history_block(history) -> str:
    """history: list of {"question": str, "answer": str} pairs, most-recent
    last (app.py assembles this from its own richer chat-turn objects — this
    function stays Streamlit-free). Returns a bounded conversation-context
    block for the parse prompt, or "" when there's no history (first
    question, or the caller doesn't pass any)."""
    if not history:
        return ""
    turns = list(history)[-QA_HISTORY_TURNS:]
    lines = [
        "\nConversation so far (most recent last) — use this ONLY to resolve "
        "references like \"what about X instead\" or \"and the week before "
        "that\"; still extract a fresh, complete structured query for the NEW "
        "question below (do not just repeat the prior one's fields):"
    ]
    for turn in turns:
        q = str(turn.get("question", "")).strip()[:200]
        a = str(turn.get("answer", "")).strip()[:300]
        lines.append(f"Q: {q}\nA: {a}")
    return "\n".join(lines) + "\n"


def build_parse_prompt(today_et, history=None) -> str:
    """today_et: a date/str already resolved by the caller (app.py's _today_et()).
    history: optional list of prior {"question","answer"} pairs (most-recent
    last) so a referential follow-up question can be resolved — see
    _format_history_block. Narration (narrate_answer) never receives history;
    only parsing does — multi-turn context helps interpret what's being
    asked, never what's true."""
    today_str = today_et.isoformat() if hasattr(today_et, "isoformat") else str(today_et)[:10]
    # .replace(), not .format() — the template's JSON example is full of
    # literal { } that .format() would misparse as fields.
    prompt = _PARSE_SYSTEM_PROMPT_TEMPLATE.replace("{today}", today_str)
    return prompt + _format_history_block(history)


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
    if intent in ("rec_outcome", "trade_lookup", "holding_lookup") and not ticker:
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


def parse_question(question: str, api_key: str, today_et, history=None,
                    model: str = _MODEL, max_tokens: int = 300) -> dict | None:
    """Returns the validated structured query dict, or None on ANY failure
    (no key, API error, malformed response). Fail-open — never raises.
    On None, LAST_PARSE_ERROR carries the real reason (mirrors
    analyst_intel.LAST_EXTRACT_ERROR) so the caller can show a "Details:"
    caption instead of a mute failure.

    history: optional list of prior {"question","answer"} pairs (most-recent
    last) for multi-turn follow-up resolution — see build_parse_prompt."""
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
            system=build_parse_prompt(today_et, history),
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

def _trade_reasoning(r) -> str | None:
    """Combine whichever of a trade row's user_thesis/notes/lesson fields are
    actually populated into one short line, or None when nothing was
    recorded — most trades have none of these filled in, and surfacing
    "not recorded" for every field on every trade would bury the answer in
    noise rather than help it."""
    parts = []
    for label, key in (("thesis", "user_thesis"), ("note", "notes"), ("lesson", "lesson")):
        v = r.get(key)
        if v is not None and str(v).strip():
            parts.append(f"{label}: {str(v).strip()}")
    if not parts:
        return None
    return "; ".join(parts)[:160]


def _closed_shares_by_buy(trades_df) -> dict:
    """Map (ticker, buy_date_str) -> total shares FIFO-matched to a SELL,
    via investor_mirror.build_closed_lots() run over the ticker's FULL trade
    history (not a query-filtered subset — a BUY inside a queried range can
    be fully closed by a SELL outside it, and FIFO consumption order depends
    on every earlier BUY/SELL/SPLIT too, so this must see everything).

    Used only to tell a BUY row whose lot has been entirely sold apart from
    one that's genuinely still open — see _row_outcome for why that
    distinction matters. Keyed by (ticker, date) rather than by trade id, so
    it inherits the same limitation build_closed_lots/tax_advisor._build_
    open_lots already have elsewhere in this codebase: two separate same-day
    BUYs of the same ticker are aggregated together, so a same-day partial
    lot could in principle be over-matched. This fails in the conservative
    direction (a still-open lot gets hidden as "position_closed" rather than
    a closed one fabricating a duplicate unrealized figure), and same-day
    multi-buy-same-ticker is rare, so it's accepted rather than solved here."""
    if trades_df is None or trades_df.empty:
        return {}
    df = trades_df
    if "id" not in df.columns:
        # build_closed_lots sorts by (timestamp, id) to break same-timestamp
        # ties deterministically — synthesize one for callers/tests that
        # don't carry a DB id column rather than erroring.
        df = df.copy()
        df["id"] = range(len(df))
    try:
        closed = build_closed_lots(df)
    except Exception:
        # Fail open: worst case, callers fall back to the pre-fix ticker-level
        # held/not-held check — never let this crash a Q&A answer.
        return {}
    if closed.empty:
        return {}
    result: dict = {}
    for (ticker, buy_date), grp in closed.groupby(["ticker", "buy_date"]):
        key = (ticker, str(buy_date))
        result[key] = result.get(key, 0.0) + float(grp["shares"].sum())
    return result


def _row_outcome(r, current_prices: dict, closed_shares: dict | None = None) -> dict:
    """Per-trade gain/loss for one trades_df row. Shared by trades_in_range
    and trades_for_ticker so both surfaces use identical P&L logic.

    SELLs report the already-stored realized_pnl directly (no recomputation).
    BUYs report unrealized P&L against current_prices (caller-supplied —
    e.g. from the session's _port_df_enriched, so this stays a pure function
    with no data-fetch of its own) when the ticker is still held — UNLESS
    `closed_shares` (see _closed_shares_by_buy) shows this specific BUY's
    lot has already been fully sold, in which case it's labeled
    "position_closed" even if the ticker is currently held again via a
    LATER re-buy. Without this check, a ticker with a full sell-then-rebuy
    round trip had EVERY historical BUY row marked "unrealized" against
    today's price — including lots already closed weeks earlier — silently
    duplicating P&L that was already reported on the matching SELL row(s)
    (confirmed live, 2026-08-02: an AAPL history with two closed round trips
    and a fresh re-buy showed three "unrealized" BUY rows instead of one).
    A genuinely PARTIALLY-sold lot still marks against the full original
    share count — the UI already carries a caption for that approximation;
    this fix only corrects the fully-closed case, which had no such caveat
    and was flatly wrong rather than a documented simplification.

    Returns {"ticker", "action", "shares", "price", "traded_at",
    "pnl": float|None, "pnl_label": "realized"|"unrealized"|"position_closed"|"unknown",
    "reasoning": str|None} — reasoning is whichever of the trade's own
    recorded user_thesis/notes/lesson fields are non-empty, joined into one
    short line, or None when nothing was recorded (most trades).
    """
    ticker = str(r.get("ticker", "")).upper()
    action = str(r.get("action", "")).upper()
    shares = _f(r.get("shares"))
    price  = _f(r.get("price"))
    traded_at_str = str(r.get("traded_at"))[:10]
    row = {
        "ticker": ticker, "action": action, "shares": shares, "price": price,
        "traded_at": traded_at_str,
        "pnl": None, "pnl_label": "unknown",
        "reasoning": _trade_reasoning(r),
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
        total_closed = (closed_shares or {}).get((ticker, traded_at_str), 0.0)
        if shares > 0 and total_closed >= shares - 1e-6:
            row["pnl_label"] = "position_closed"
            return row
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

    # Computed from the FULL trades_df (before date-range filtering) — a BUY
    # inside the range can be closed by a SELL outside it. See _row_outcome.
    closed_shares = _closed_shares_by_buy(trades_df)

    df = _real_trades(trades_df)
    try:
        sd = pd.Timestamp(str(start_date)[:10], tz="UTC")
        ed = pd.Timestamp(str(end_date)[:10], tz="UTC") + pd.Timedelta(days=1)
    except Exception:
        return []
    df = df[(df["traded_at"] >= sd) & (df["traded_at"] < ed)]

    return [_row_outcome(r, current_prices, closed_shares) for _, r in df.sort_values("traded_at").iterrows()]


def trades_for_ticker(trades_df, ticker: str, current_prices: dict | None = None) -> list[dict]:
    """Every real (non-SPLIT) trade ever recorded for `ticker`, oldest first,
    each with its gain/loss (see _row_outcome). No date range — for
    "what was my trade on X" style questions. Empty list (not None) when the
    ticker has no trade history; the caller/narration must say so plainly."""
    current_prices = current_prices or {}
    ticker = str(ticker).upper().strip()
    if trades_df is None or trades_df.empty or not ticker:
        return []

    closed_shares = _closed_shares_by_buy(trades_df)

    df = _real_trades(trades_df)
    df = df[df["ticker"].astype(str).str.upper() == ticker]
    return [_row_outcome(r, current_prices, closed_shares) for _, r in df.sort_values("traded_at").iterrows()]


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
    if "reasoning" in df.columns:
        df["reasoning"] = df["reasoning"].fillna("—")
    return df.rename(columns={
        "ticker":     "Ticker",
        "action":     "Action",
        "shares":     "Shares",
        "price":      "Price ($)",
        "traded_at":  "Date",
        "pnl":        "Gain/Loss ($)",
        "pnl_label":  "Status",
        "reasoning":  "Notes",
    })


def _find_buy_trade_for_rec(trades_df, ticker: str, rec_date, window_days: int) -> dict | None:
    """Find the BUY trade a recommendation was most plausibly acted on by:
    same ticker, action == BUY, traded_at within [rec_date, rec_date +
    window_days] inclusive. Returns the full raw trade row as a dict (unlike
    recommendations_history.match_recs_to_trades()'s narrow projection) so
    notes/user_thesis/lesson/premortem fields are available — or None if no
    BUY falls in that window. Never guesses further out or across tickers;
    when multiple BUYs fall in the window, returns the earliest (the one
    closest to the recommendation itself)."""
    ticker = str(ticker).upper().strip()
    if trades_df is None or trades_df.empty or not ticker:
        return None

    df = _real_trades(trades_df)
    df = df[(df["ticker"].astype(str).str.upper() == ticker) & (df["action"].astype(str).str.upper() == "BUY")]
    if df.empty:
        return None

    try:
        rd_ts = pd.Timestamp(str(rec_date)[:10], tz="UTC")
    except Exception:
        return None
    window_end = rd_ts + pd.Timedelta(days=window_days) + pd.Timedelta(days=1)
    df = df[(df["traded_at"] >= rd_ts) & (df["traded_at"] < window_end)]
    if df.empty:
        return None

    return df.sort_values("traded_at").iloc[0].to_dict()


def recommendation_outcome(ticker: str, rec_date, recs_df, price_history_df=None,
                            horizon_days: int | None = None, trades_df=None) -> dict:
    """
    Look up the recommendation surfaced for `ticker` on `rec_date` (exact-date
    match — no "nearest recommendation" guessing) and, if given a price
    history DataFrame spanning that date forward, the price move over the
    next `horizon_days` trading days.

    recs_df: caller-loaded via db.load_recommendations() for a range covering
    rec_date (pure function — no DB access here).
    price_history_df: caller-fetched via data.fetch_price_history(ticker, ...)
    with enough forward history past rec_date to cover horizon_days.
    trades_df: optional, caller-loaded via db.load_trades()/session cache. When
    given, looks up the BUY trade this recommendation was plausibly acted on by
    (see _find_buy_trade_for_rec) and adds "acted_on" (bool) plus whatever of
    that trade's user_thesis/notes/lesson/premortem_case_against/
    premortem_commitment were actually recorded — None for anything absent,
    and None for all of these (not False) when trades_df isn't supplied at
    all, so the caller/narration can tell "not checked" apart from "checked,
    nothing found."

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
        # Pre-Mortem cross-reference (item 3, "complete the circle") — None
        # for all of these when trades_df isn't supplied at all, so "not
        # checked" is distinguishable from "checked, no matching trade."
        "acted_on": None,
        "trade_notes": None,
        "trade_lesson": None,
        "user_thesis": None,
        "premortem_case_against": None,
        "premortem_commitment": None,
    }

    if trades_df is not None:
        buy_trade = _find_buy_trade_for_rec(trades_df, ticker, rd_str, QA_PREMORTEM_TRADE_MATCH_WINDOW_DAYS)
        result["acted_on"] = buy_trade is not None
        if buy_trade is not None:
            def _nonempty(v):
                return str(v).strip() or None if v is not None else None
            result["trade_notes"] = _nonempty(buy_trade.get("notes"))
            result["trade_lesson"] = _nonempty(buy_trade.get("lesson"))
            result["user_thesis"] = _nonempty(buy_trade.get("user_thesis"))
            # jsonb -> native list via the Supabase client; a str form would
            # mean the column was never populated in that shape and is
            # intentionally ignored here, not a bug in this guard.
            pca = buy_trade.get("premortem_case_against")
            result["premortem_case_against"] = pca if isinstance(pca, list) and pca else None
            result["premortem_commitment"] = _nonempty(buy_trade.get("premortem_commitment"))

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


def current_holding(port_df, ticker: str) -> dict:
    """Current position snapshot for `ticker` from the session's already-
    enriched port_df — never fetches. Returns found=False with reason
    "portfolio_not_loaded" when port_df is None/empty/missing columns
    (portfolio isn't loaded this session — distinct from genuinely not
    holding the ticker, mirrors F-261's cold-path distinction), or reason
    "not_held" when port_df is loaded but the ticker isn't a current row."""
    ticker = str(ticker).upper().strip()
    if port_df is None or port_df.empty or "Ticker" not in port_df.columns:
        return {"found": False, "reason": "portfolio_not_loaded"}
    match = port_df[port_df["Ticker"].astype(str).str.upper() == ticker]
    if match.empty:
        return {"found": False, "reason": "not_held"}
    row = match.iloc[0]
    shares = _f(row.get("Shares"))
    avg_cost = _f(row.get("Avg Cost"))
    mval = _f(row.get("Market Value"))
    current_price = (mval / shares) if shares else None
    return {
        "found": True,
        "ticker": ticker,
        "shares": shares,
        "avg_cost": round(avg_cost, 2) if avg_cost else None,
        "current_price": round(current_price, 2) if current_price else None,
        "market_value": round(mval, 2),
        "pnl_dollar": round(_f(row.get("P&L ($)")), 2),
        "pnl_pct": round(_f(row.get("P&L (%)")), 2),
        "weight_pct": round(_f(row.get("Weight (%)")), 2) if "Weight (%)" in port_df.columns else None,
        "composite_score": row.get("Score"),
        "sector": row.get("Sector"),
    }


def portfolio_summary(port_df) -> dict:
    """Current aggregate snapshot across all held positions — total value,
    total unrealized P&L, position count, best/worst performer by P&L%.
    This is a SNAPSHOT of right now, NOT a time-period return calculation —
    the app's Modified-Dietz-based period returns live elsewhere (SnapTrade
    account_flows) and are deliberately not reused/reinvented here; a
    period-scoped question is routed to "unsupported" at parse time instead
    of answered with a different, less rigorous methodology under the same
    name. See docs/plans/portfolio-qa.md."""
    if port_df is None or port_df.empty:
        return {"found": False, "reason": "portfolio_not_loaded"}
    total_value = round(float(port_df["Market Value"].sum()), 2)
    total_cost = round(float((port_df["Shares"] * port_df["Avg Cost"]).sum()), 2)
    total_pnl_dollar = round(total_value - total_cost, 2)
    total_pnl_pct = round((total_pnl_dollar / total_cost * 100), 2) if total_cost else None
    best = port_df.loc[port_df["P&L (%)"].idxmax()]
    worst = port_df.loc[port_df["P&L (%)"].idxmin()]
    return {
        "found": True,
        "position_count": int(len(port_df)),
        "total_value": total_value,
        "total_pnl_dollar": total_pnl_dollar,
        "total_pnl_pct": total_pnl_pct,
        "best_ticker": str(best["Ticker"]),
        "best_pnl_pct": round(_f(best["P&L (%)"]), 2),
        "worst_ticker": str(worst["Ticker"]),
        "worst_pnl_pct": round(_f(worst["P&L (%)"]), 2),
    }


def sector_composition(port_df) -> list[dict]:
    """Sector weight breakdown, reusing portfolio.sector_exposure() unchanged
    — same numbers Portfolio Overview's own sector chart shows, so this
    answer can never disagree with that page."""
    if port_df is None or port_df.empty:
        return []
    from stock_analyzer.portfolio import sector_exposure
    exp = sector_exposure(port_df)
    if exp.empty:
        return []
    return [
        {"sector": r["Sector"], "value": round(float(r["Value"]), 2), "pct": round(float(r["Pct"]), 1)}
        for _, r in exp.sort_values("Pct", ascending=False).iterrows()
    ]


# ─── Step 3: narrate the facts in plain English ─────────────────────────────

_NARRATE_SYSTEM_PROMPT = (
    "You explain a portfolio query result to the investor who asked it. "
    "Use ONLY the facts given below — never invent a number, a reason, or a "
    "pillar score that isn't present. If a value is missing or null "
    "(especially a pillar sub-score), say plainly that it wasn't recorded "
    "rather than guessing why. Do not recommend any future action and do "
    "not restate a threshold — just explain what already happened. Keep it "
    "to 2-4 short sentences.\n\n"
    "If a Pre-Mortem risk case or exit commitment is present in the facts, "
    "assess RETROSPECTIVELY whether the actual price/outcome data shows it "
    "materializing — say 'unclear' if the facts don't clearly show it either "
    "way. This is a read of what already happened, never a recommendation or "
    "forward-looking call — do not suggest any future action here either."
)


def _trade_list_lines(header: str, facts: list[dict]) -> str:
    lines = [header]
    for t in facts:
        pnl = f"{t['pnl']:+.2f}" if t.get("pnl") is not None else "n/a"
        line = (
            f"- {t['traded_at']}: {t['action']} {t['shares']:g} {t['ticker']} "
            f"@ ${t['price']:.2f} — P&L {pnl} ({t['pnl_label']})"
        )
        if t.get("reasoning"):
            line += f" — {t['reasoning']}"
        lines.append(line)
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

        if facts.get("acted_on") is True:
            if facts.get("user_thesis"):
                lines.append(f"User's thesis recorded at purchase: {facts['user_thesis']}")
            if facts.get("premortem_case_against"):
                for c in facts["premortem_case_against"]:
                    lines.append(f"Pre-Mortem risk case ({c.get('angle')}): {c.get('argument')}")
            if facts.get("premortem_commitment"):
                lines.append(f"Stated exit commitment if wrong: {facts['premortem_commitment']}")
            if facts.get("trade_lesson"):
                lines.append(f"Lesson recorded on this trade: {facts['trade_lesson']}")
        elif facts.get("acted_on") is False:
            lines.append("No matching BUY trade on record for this recommendation — it doesn't look like it was acted on.")

        return "\n".join(lines)

    if intent == "holding_lookup":
        if not facts.get("found"):
            if facts.get("reason") == "portfolio_not_loaded":
                return "Portfolio isn't loaded this session."
            return "You don't currently hold that ticker."
        lines = [
            f"Ticker: {facts['ticker']}",
            f"Shares held: {facts['shares']:g}",
            f"Average cost: ${facts.get('avg_cost')}" if facts.get('avg_cost') is not None else "Average cost: not recorded",
            f"Current price: ${facts.get('current_price')}" if facts.get('current_price') is not None else "Current price: not available",
            f"Market value: ${facts['market_value']}",
            f"Unrealized P&L: ${facts['pnl_dollar']:+.2f} ({facts['pnl_pct']:+.1f}%)",
        ]
        if facts.get("weight_pct") is not None:
            lines.append(f"Portfolio weight: {facts['weight_pct']}%")
        if facts.get("composite_score") is not None:
            lines.append(f"Composite score: {facts['composite_score']}")
        if facts.get("sector"):
            lines.append(f"Sector: {facts['sector']}")
        return "\n".join(lines)

    if intent == "portfolio_summary":
        if not facts.get("found"):
            return "Portfolio isn't loaded this session."
        lines = [
            f"Number of positions: {facts['position_count']}",
            f"Total market value: ${facts['total_value']}",
            f"Total unrealized P&L: ${facts['total_pnl_dollar']:+.2f}" +
            (f" ({facts['total_pnl_pct']:+.1f}%)" if facts.get('total_pnl_pct') is not None else ""),
            f"Biggest winner: {facts['best_ticker']} ({facts['best_pnl_pct']:+.1f}%)",
            f"Biggest loser: {facts['worst_ticker']} ({facts['worst_pnl_pct']:+.1f}%)",
        ]
        return "\n".join(lines)

    if intent == "sector_composition":
        if not facts:
            return "Portfolio isn't loaded this session, or has no sector data."
        lines = ["Sector breakdown by market value:"]
        for s in facts:
            lines.append(f"- {s['sector']}: {s['pct']}% (${s['value']})")
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
