"""
Pre-Commitment Enforcement (docs/plans/premortem-enforcement.md).

Actively monitors a user's own stated Pre-Mortem exit commitment
(`trades.premortem_commitment`, Concept C / F-187 — captured at BUY time)
against live price data, and confronts the investor with a dedicated Act
Today card when a stated numeric trigger has actually fired and they're
still holding — instead of only ever quoting the commitment back as passive
narrative context, which is all `thesis_red_team.py` and `portfolio_qa.py`
do with it today.

Two entry points:
  extract_trigger()            — LLM (Haiku), ONE-SHOT at BUY-submission
                                  time. Parses the free-text commitment into
                                  a structured, checkable fact. Conservative
                                  bias: no explicit price stated -> not
                                  checkable, never inferred or guessed.
                                  Returns None on ANY failure (fails open,
                                  same as premortem_advisor.
                                  generate_case_against() — no retry; the
                                  trades grid is delete-only, app.py:21225-
                                  21248, so a failed extraction stays
                                  unmonitored for that trade).
  detect_premortem_triggers()   — pure Python, ZERO LLM cost. Called once
                                  per Daily Brief build, the same call site
                                  as deterioration_signals() in
                                  daily_briefing.py. Compares each currently
                                  open lot's stored trigger against its price
                                  history since the lot's buy_date.

Design principles (mirrors premortem_advisor.py / analyst_intel.py):
- The LLM extracts only what's explicitly stated — it never invents a price.
- The daily CHECK never calls an LLM — deterministic price comparison only.
- Lot-scoped, not ticker-scoped: reuses tax_advisor._build_open_lots() so a
  sell-then-rebuy never resurfaces an unrelated, already-closed lot's
  commitment against a brand-new position (2026-08-03 Opus design review,
  blocking finding #2).
- Split-safe: _build_open_lots() tracks each lot's cumulative split ratio
  since its buy_date; the stored (pre-split) trigger price is divided by
  that ratio before comparison against current (already split-adjusted)
  price history (blocking finding #1).
- Self-resolving: a trigger is only reported while the MOST RECENT daily
  close is still beyond the level, on a closing-price basis (matching the
  deterioration ladder's own close-basis convention, DETERIORATION_CONFIRM_
  DAYS/_REQUIRED) — a recovered position stops firing on its own the next
  trading day it closes back across the line, no acknowledge/snooze
  mechanism needed (blocking finding #3).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date as _date

import pandas as pd

from stock_analyzer import llm, trigger_price_check
from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC

_log = logging.getLogger(__name__)

_EXTRACT_SYSTEM_PROMPT = """You extract a structured price trigger from an investor's own written exit commitment ("what would make me wrong about this buy"), written before they bought a stock.

Your ONLY job: does this commitment state an EXPLICIT numeric price level the investor would exit at, and in which direction?

CRITICAL RULES:
- Only extract a trigger if an explicit dollar price is stated (e.g. "$150", "150", "$45.50"). Do NOT infer a price from percentages, "current levels", or vague language.
- If the commitment describes a QUALITATIVE condition (earnings, guidance, sector rotation, news, "loses its edge", a date, etc.) with no explicit price number, this is NOT checkable — never invent or estimate a price.
- If BOTH a qualitative condition AND an explicit price are stated, extract the price — the numeric part is the checkable part.
- "breaks $150", "falls below $150", "drops under $150", "loses $150 support" all mean direction="below".
- "breaks above $150", "rallies past $150", "exceeds $150" all mean direction="above".
- When genuinely ambiguous whether it's a support break (below) or resistance breakout (above), do not guess — mark not checkable.

Respond with ONLY a JSON object, no other text before or after:
{"checkable": true, "direction": "below", "price_level": 150.0}
or
{"checkable": false, "direction": null, "price_level": null}"""


def _json_objects(text: str) -> list:
    """Every top-level JSON value that decodes from a '{' in `text`, in order.
    A '{' that doesn't start a decodable object (prose like "{closing basis}") is
    skipped; a '{' inside an object already decoded is never revisited."""
    dec = json.JSONDecoder()
    out, i = [], 0
    while True:
        i = text.find("{", i)
        if i == -1:
            return out
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        out.append(obj)
        i = end


def _parse_trigger(text: str) -> dict | None:
    """Strict parse of the extraction reply. Finds EVERY JSON object in the reply
    that has a "checkable" key and accepts only when there is at least one and
    they are all identical, then validates the shape strictly. Returns None on
    any malformed or ambiguous reply rather than guessing.

    Why every object, not the first: the system prompt itself shows example
    objects (incl. a checkable $150 "below" one), so a reply that echoes an
    example before its real answer must be refused, never saved as a fabricated
    declared stop. Why not one json.loads of the whole text: a fenced object
    followed by an explanation (seen live on Haiku 4.5, 2026-11-01: 13 trades)
    is a valid single answer and used to be dropped."""
    if not text:
        return None
    answers = [o for o in _json_objects(text) if isinstance(o, dict) and "checkable" in o]
    if not answers or any(a != answers[0] for a in answers[1:]):
        return None
    # Every quoted "checkable" must belong to a decoded answer: an echoed example
    # followed by a MALFORMED real answer would otherwise leave the example as the
    # only decoded object (a fabricated stop). Prose quoting the key fails closed.
    if text.count('"checkable"') != len(answers):
        return None
    parsed = answers[0]
    checkable = parsed.get("checkable")
    if not isinstance(checkable, bool):
        return None
    if not checkable:
        return {"checkable": False, "direction": None, "price_level": None}
    direction = parsed.get("direction")
    if direction not in ("below", "above"):
        return None
    try:
        price_level = float(parsed.get("price_level"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price_level) or price_level <= 0:
        return None
    return {"checkable": True, "direction": direction, "price_level": price_level}


def extract_trigger_outcome(
    commitment_text: str,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 200,
    *,
    setting=None,
) -> dict:
    """
    Parse a free-text Pre-Mortem commitment into a structured price trigger and
    apply the in-text price check (`trigger_price_check.guard_trigger`).

    Returns {"raw": parsed|None (the model's answer, before the check),
    "trigger": guarded|None (what production saves), "refused_price": float|None,
    "refused_direction": str|None (set only when the check refused a checkable
    price that isn't written in the commitment)}. Fails open: any failure (no key,
    API error, malformed response) gives all-None, and the trade is simply never
    monitored. The model eval reads "raw"; production reads "trigger".
    """
    out = {"raw": None, "trigger": None, "refused_price": None, "refused_direction": None}
    if not api_key or not (commitment_text or "").strip():
        return out
    try:
        r = llm.complete(
            llm.ROLE_EXTRACT_TRIGGER,
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            temperature=0,  # structured JSON output — deterministic, not prose
            system=_EXTRACT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": commitment_text.strip()}],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
            setting=setting,
        )
        if not r.ok:
            return out
        parsed = _parse_trigger(r.text.strip())
        guarded, refused_price = trigger_price_check.guard_trigger(parsed, commitment_text)
        out["raw"], out["trigger"] = parsed, guarded
        if refused_price is not None:
            out["refused_price"] = refused_price
            out["refused_direction"] = (parsed or {}).get("direction")
            _log.warning("[premortem] trigger refused: $%.2f not in commitment", refused_price)
        return out
    except Exception:
        return {"raw": None, "trigger": None, "refused_price": None, "refused_direction": None}


def extract_trigger(
    commitment_text: str,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 200,
    *,
    setting=None,
) -> dict | None:
    """
    The trigger production saves: {"checkable": bool, "direction":
    "below"|"above"|None, "price_level": float|None} — or None on ANY failure
    (no key, API error, malformed response) or when the AI's checkable price
    isn't written in the commitment (see `extract_trigger_outcome`).
    """
    return extract_trigger_outcome(
        commitment_text, api_key, model, max_tokens, setting=setting)["trigger"]


def detect_premortem_triggers(
    trades_df: pd.DataFrame | None,
    held_data: dict | None,
    today: _date,
) -> list[dict]:
    """
    Pure Python, ZERO LLM cost. For every ticker with a currently-open lot
    (`tax_advisor._build_open_lots`) whose GOVERNING lot (the most recently
    opened) carries a checkable premortem trigger, compares that trigger
    (split-ratio-adjusted) against the ticker's daily-close price history
    since the lot's buy_date.

    Returns one dict per CURRENTLY ACTIVE trigger — the most recent daily
    close is still beyond the level in the stated direction:
        {ticker, direction, trigger_price, first_breach_date, days_since,
         current_price}
    A ticker whose price has recovered back across the line since a past
    breach is silently excluded — see the module docstring's "self-resolving"
    principle. Never raises on malformed input; degrades to [] on any
    missing/empty data.
    """
    if trades_df is None or trades_df.empty or not held_data:
        return []
    if "premortem_trigger_direction" not in trades_df.columns:
        return []  # DDL not applied yet — nothing to check, not an error

    from stock_analyzer.tax_advisor import _build_open_lots  # local — avoids circular dep

    out: list[dict] = []
    tickers = (
        trades_df.loc[trades_df["action"].astype(str).str.upper() == "BUY", "ticker"]
        .dropna().astype(str).str.upper().unique()
    )
    for ticker in tickers:
        bundle = held_data.get(ticker)
        if not bundle:
            continue
        hist = bundle.get("df")
        if hist is None or hist.empty or "Close" not in hist.columns:
            continue

        lots = _build_open_lots(ticker, trades_df, today)
        if not lots:
            continue
        governing = max(lots, key=lambda lot: lot["buy_date"])

        buy_rows = trades_df[
            (trades_df["ticker"].astype(str).str.upper() == ticker)
            & (trades_df["action"].astype(str).str.upper() == "BUY")
        ].copy()
        if buy_rows.empty:
            continue
        buy_rows["_ts"] = pd.to_datetime(buy_rows["traded_at"], errors="coerce", utc=True, format="ISO8601")
        buy_rows = buy_rows.dropna(subset=["_ts"])
        buy_rows = buy_rows[buy_rows["_ts"].dt.date == governing["buy_date"]]
        if buy_rows.empty:
            continue
        trade_row = buy_rows.iloc[0]  # same-day-multi-BUY limitation accepted elsewhere in this app

        direction = trade_row.get("premortem_trigger_direction")
        if direction not in ("below", "above"):
            continue  # None (not yet extracted) or "not_checkable" — nothing to check
        try:
            raw_price = float(trade_row.get("premortem_trigger_price"))
        except (TypeError, ValueError):
            continue
        if raw_price <= 0:
            continue

        split_ratio = governing.get("split_ratio") or 1.0
        adjusted_price = raw_price / split_ratio

        closes = hist["Close"].dropna().copy()
        if getattr(closes.index, "tz", None) is not None:
            closes.index = closes.index.tz_localize(None)
        # Strictly after buy_date, not >=: an open-market "today" row from the
        # provider can be a live/intraday quote mislabeled with today's date,
        # not a settled close — including it here would let a trigger fire
        # the same day it was set (2026-08-04 audit finding).
        closes = closes[closes.index.date > governing["buy_date"]]
        if closes.empty:
            continue

        latest_close = float(closes.iloc[-1])
        is_active = (
            latest_close < adjusted_price if direction == "below"
            else latest_close > adjusted_price
        )
        if not is_active:
            continue

        first_breach_date = None
        for idx, close_val in closes.items():
            cv = float(close_val)
            breached = cv < adjusted_price if direction == "below" else cv > adjusted_price
            if breached:
                first_breach_date = idx.date()
                break
        if first_breach_date is None:
            # Inconsistent data (is_active True but no historical breach found)
            # — skip rather than fabricate a breach date.
            continue

        out.append({
            "ticker": ticker,
            "direction": direction,
            "trigger_price": adjusted_price,
            "first_breach_date": first_breach_date,
            "days_since": (today - first_breach_date).days,
            "current_price": latest_close,
        })
    return out
