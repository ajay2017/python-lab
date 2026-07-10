"""
stock_analyzer/broker_screenshot.py — Robinhood History screenshot parser (pure)

Accepts one or more PNG/JPG screenshots of the Robinhood Account → History page,
calls Claude Vision (Opus 4.8 by default) to extract executed trades, and returns
a result dict in the same shape as broker_import.parse_robinhood_csv so the
downstream dedup + preview + write path is reused unchanged.

No Streamlit or database imports.
"""

from __future__ import annotations

import base64
import collections
import datetime
import json
import re
from typing import Optional

import pandas as pd

_COLS_TRADE   = ["ticker", "action", "shares", "price", "activity_date", "company"]
_COLS_INVALID = _COLS_TRADE + ["reason"]
_TRADE_ACTIONS = {"BUY", "SELL"}

# ── Vision prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a precise data-extraction assistant. You will be shown a screenshot of the
Robinhood Account → History page. Your job is to extract ONLY executed (non-canceled)
buy and sell orders as structured JSON.

ROBINHOOD HISTORY FORMAT — each order appears as four lines:
  Line 1: "[Company Name] [order type] [buy/sell]"
           e.g. "Snowflake market sell", "Palantir Technologies limit buy"
  Line 2: "Individual · [Month] [Day]"
           e.g. "Individual · Jul 9"
  Line 3: Either "Canceled"  ← SKIP THIS ORDER ENTIRELY
           or    "$[total]"  ← executed trade (e.g. "$1,301.52")
  Line 4: "[N] share[s] at $[price per share]"  (only for executed trades)
           e.g. "5 shares at $260.31" or "1 share at $260.31"

EXTRACTION RULES:
1. Skip ALL canceled orders — do not include them in "trades".
2. For each executed trade extract: company name, ticker (inferred), action, date
   string, shares, price per share.
3. Infer the stock ticker from the company name using your training knowledge.
   Examples: Apple→AAPL, Palantir Technologies→PLTR, Snowflake→SNOW,
   CrowdStrike Holdings→CRWD, Medtronic→MDT, Occidental Petroleum→OXY,
   ServiceNow→NOW, Biogen→BIIB, Boston Scientific→BSX, Robinhood Markets→HOOD,
   Broadcom→AVGO, Capital One→COF, First Solar→FSLR, Palo Alto Networks→PANW,
   Novo Nordisk→NVO, Micron Technology→MU, Lam Research Corp→LRCX,
   General Dynamics→GD, Visa→V, Booking Holdings→BKNG, EOG Resources→EOG,
   Occidental Petroleum→OXY, SpaceX→SPACEX.
4. Set ticker_confidence to "high" for standard publicly-traded tickers you are
   certain about, "low" for private companies (e.g. SpaceX), ambiguous names, or
   any name you are not confident about.
5. action must be exactly "BUY" or "SELL" (uppercase).
6. date_str must be the date exactly as shown in the screenshot (e.g. "Jul 9").
7. shares and price must be numbers (float), not strings.
8. Return ONLY a valid JSON object — no markdown fences, no explanation.

OUTPUT SCHEMA:
{
  "trades": [
    {
      "company_name": "Snowflake",
      "ticker": "SNOW",
      "ticker_confidence": "high",
      "action": "SELL",
      "date_str": "Jul 9",
      "shares": 5.0,
      "price": 260.31
    }
  ],
  "canceled_count": 8,
  "date_range": { "earliest": "Jul 1", "latest": "Jul 9" }
}
"""

_USER_PROMPT = (
    "Extract all executed trades from this Robinhood History screenshot. "
    "Return only the JSON object as specified — no markdown, no extra text."
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _detect_mime(img_bytes: bytes) -> str:
    if img_bytes[:4] == b"\x89PNG":
        return "image/png"
    if img_bytes[:3] in (b"\xff\xd8\xff",):
        return "image/jpeg"
    return "image/png"


def _infer_year(date_str: str, reference_date: datetime.date) -> Optional[datetime.date]:
    """
    Given "Jul 9" and a reference date (the upload date), return the most
    plausible calendar date. Assumes current year unless the result would be
    in the future (> 1 day ahead), in which case uses prior year.
    """
    for fmt in ("%b %d", "%B %d", "%b %d, %Y", "%B %d, %Y"):
        try:
            parsed = datetime.datetime.strptime(date_str.strip(), fmt)
            if "%Y" in fmt:
                return parsed.date()
            year = reference_date.year
            candidate = parsed.replace(year=year).date()
            if candidate > reference_date + datetime.timedelta(days=1):
                candidate = parsed.replace(year=year - 1).date()
            return candidate
        except ValueError:
            continue
    return None


def _strip_json_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?[\r\n]*", "", text.strip())
    text = re.sub(r"[\r\n]*```$", "", text.strip())
    return text.strip()


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_robinhood_screenshots(
    images: list[bytes],
    api_key: str,
    model: str = "claude-opus-4-8",
    reference_date: Optional[datetime.date] = None,
) -> dict:
    """
    Parse one or more Robinhood Account → History screenshot images.

    Returns same shape as broker_import.parse_robinhood_csv, plus two extra keys:
      low_confidence_tickers : list[str]  — tickers flagged uncertain by the model
      parse_warnings         : list[str]  — per-image non-fatal errors
    """
    if reference_date is None:
        reference_date = datetime.date.today()

    empty_trades  = pd.DataFrame(columns=_COLS_TRADE)
    empty_invalid = pd.DataFrame(columns=_COLS_INVALID)

    if not images:
        return {
            "trades": empty_trades, "skipped": {}, "invalid": empty_invalid,
            "error": "No images provided.", "low_confidence_tickers": [],
            "parse_warnings": [],
        }

    try:
        import anthropic  # noqa: PLC0415 — lazy import matches project pattern
    except ImportError:
        return {
            "trades": empty_trades, "skipped": {}, "invalid": empty_invalid,
            "error": "anthropic package not available.", "low_confidence_tickers": [],
            "parse_warnings": [],
        }

    client = anthropic.Anthropic(api_key=api_key)

    all_raw: list[dict] = []
    total_canceled = 0
    parse_warnings: list[str] = []

    for idx, img_bytes in enumerate(images, 1):
        mime = _detect_mime(img_bytes)
        b64  = base64.standard_b64encode(img_bytes).decode()

        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": _USER_PROMPT},
                    ],
                }],
                timeout=120,
            )
        except Exception as exc:
            parse_warnings.append(f"Image {idx}: API error — {exc}")
            continue

        raw_text = response.content[0].text if response.content else ""
        raw_text = _strip_json_fences(raw_text)

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            parse_warnings.append(f"Image {idx}: could not parse model response as JSON — {exc}")
            continue

        total_canceled += int(data.get("canceled_count", 0) or 0)
        all_raw.extend(data.get("trades", []) or [])

    if parse_warnings and not all_raw:
        return {
            "trades": empty_trades, "skipped": {}, "invalid": empty_invalid,
            "error": "; ".join(parse_warnings), "low_confidence_tickers": [],
            "parse_warnings": parse_warnings,
        }

    # Dedup exact duplicates that appear across overlapping screenshots
    seen_keys: set = set()
    deduped: list[dict] = []
    for r in all_raw:
        key = (
            str(r.get("ticker", "")).upper(),
            str(r.get("action", "")),
            str(r.get("date_str", "")),
            r.get("shares"),
            r.get("price"),
        )
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(r)

    records: list[dict]         = []
    invalid_records: list[dict] = []
    low_conf_tickers: list[str] = []

    for r in deduped:
        ticker   = str(r.get("ticker", "")).strip().upper()
        action   = str(r.get("action", "")).strip().upper()
        company  = str(r.get("company_name", "")).strip()
        shares_v = r.get("shares")
        price_v  = r.get("price")
        date_str = str(r.get("date_str", "")).strip()

        if r.get("ticker_confidence") == "low" and ticker not in low_conf_tickers:
            low_conf_tickers.append(ticker)

        act_date = _infer_year(date_str, reference_date)

        try:
            shares_f = float(shares_v) if shares_v is not None else None
        except (TypeError, ValueError):
            shares_f = None
        try:
            price_f = float(price_v) if price_v is not None else None
        except (TypeError, ValueError):
            price_f = None

        base = {
            "ticker":        ticker,
            "action":        action,
            "shares":        shares_f,
            "price":         price_f,
            "activity_date": act_date,
            "company":       company,
        }

        reasons: list[str] = []
        if not ticker:                          reasons.append("no ticker extracted")
        if action not in _TRADE_ACTIONS:        reasons.append(f"unknown action '{action}'")
        if act_date is None:                    reasons.append("unparseable date")
        if shares_f is None or shares_f <= 0:  reasons.append("shares ≤ 0 or missing")
        if price_f  is None or price_f  <= 0:  reasons.append("price ≤ 0 or missing")

        if reasons:
            invalid_records.append({**base, "reason": "; ".join(reasons)})
        else:
            records.append(base)

    skipped: dict[str, int] = {}
    if total_canceled:
        skipped["Canceled"] = total_canceled

    return {
        "trades":  pd.DataFrame(records,         columns=_COLS_TRADE),
        "invalid": pd.DataFrame(invalid_records, columns=_COLS_INVALID),
        "skipped": skipped,
        "error":   None,
        "low_confidence_tickers": low_conf_tickers,
        "parse_warnings": parse_warnings,
    }


def find_app_only_in_range(
    screenshot_trades: pd.DataFrame,
    trades_df,
    date_from: datetime.date,
    date_to: datetime.date,
) -> pd.DataFrame:
    """
    Return trades in trades_df whose date falls within [date_from, date_to] that
    do NOT have a corresponding match in screenshot_trades.

    Used to surface "in app, not in screenshot" candidates for review.
    The comparison is content-based (ticker + action + shares + price), not
    date-exact, since the app date (traded_at) may differ by a day from the
    screenshot date (activity date on Robinhood may reflect settlement or
    the date the user logged it).

    Returns a DataFrame with columns: ticker, action, shares, price, traded_at.
    """
    if trades_df is None or not isinstance(trades_df, pd.DataFrame) or trades_df.empty:
        return pd.DataFrame(columns=["ticker", "action", "shares", "price", "traded_at"])

    # Build a multiset of screenshot trades by content key
    screenshot_keys: collections.Counter = collections.Counter()
    if not screenshot_trades.empty:
        for _, r in screenshot_trades.iterrows():
            try:
                key = (
                    str(r["ticker"]).upper(),
                    str(r["action"]).upper(),
                    round(float(r["shares"]), 4),
                    round(float(r["price"]),  2),
                )
                screenshot_keys[key] += 1
            except (TypeError, ValueError):
                continue

    # Filter app trades to the date range
    app_in_range = []
    matched_counts: collections.Counter = collections.Counter()

    for _, row in trades_df.iterrows():
        row_action = str(row.get("action", "")).upper().strip()
        if row_action not in _TRADE_ACTIONS:
            continue

        try:
            traded_at = pd.to_datetime(row.get("traded_at"), utc=True, errors="coerce")
            if pd.isnull(traded_at):
                continue
            row_date = traded_at.date()
        except Exception:
            continue

        if not (date_from <= row_date <= date_to):
            continue

        try:
            key = (
                str(row.get("ticker", "")).upper(),
                row_action,
                round(float(row.get("shares") or 0), 4),
                round(float(row.get("price")  or 0), 2),
            )
        except (TypeError, ValueError):
            continue

        if matched_counts[key] < screenshot_keys[key]:
            matched_counts[key] += 1  # consumed a match — not app-only
        else:
            app_in_range.append({
                "ticker":    str(row.get("ticker", "")).upper(),
                "action":    row_action,
                "shares":    row.get("shares"),
                "price":     row.get("price"),
                "traded_at": row_date.isoformat(),
            })

    return pd.DataFrame(app_in_range, columns=["ticker", "action", "shares", "price", "traded_at"])


def last_screenshot_sync_date(trades_df) -> Optional[datetime.date]:
    """
    Return the most recent traded_at date from trades tagged as screenshot imports
    (notes containing 'RH screenshot'). Returns None if no prior screenshot import.
    """
    if trades_df is None or not isinstance(trades_df, pd.DataFrame) or trades_df.empty:
        return None
    if "notes" not in trades_df.columns or "traded_at" not in trades_df.columns:
        return None

    mask = trades_df["notes"].astype(str).str.contains("RH screenshot", na=False)
    subset = trades_df[mask]
    if subset.empty:
        return None

    dates = pd.to_datetime(subset["traded_at"], utc=True, errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().date()
