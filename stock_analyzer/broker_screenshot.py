"""
stock_analyzer/broker_screenshot.py — Robinhood History text parser (pure)

Accepts text pasted from the Robinhood Account → History page and parses it
into structured trade records. No image processing or Vision API required.

Each order in the Robinhood History page appears as four lines:
  Line 1: "[Company Name] [order_type] [buy|sell]"
  Line 2: "Individual · [Month] [Day]"
  Line 3: "Canceled"  — OR —  "$[total]"
  Line 4: "[N] share[s] at $[price]"  (only for executed trades)

No Streamlit or database imports.
"""

from __future__ import annotations

import collections
import datetime
import json
import re
from typing import Optional

import pandas as pd

_COLS_TRADE   = ["ticker", "action", "shares", "price", "activity_date", "company"]
_COLS_INVALID = _COLS_TRADE + ["reason"]
_TRADE_ACTIONS = {"BUY", "SELL"}

# ── Ticker lookup table ────────────────────────────────────────────────────────

_TICKER_MAP: dict[str, str] = {
    "apple": "AAPL",
    "apple inc": "AAPL",
    "microsoft": "MSFT",
    "microsoft corporation": "MSFT",
    "amazon": "AMZN",
    "amazon.com": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "meta platforms": "META",
    "tesla": "TSLA",
    "nvidia": "NVDA",
    "nvidia corporation": "NVDA",
    "amd": "AMD",
    "advanced micro devices": "AMD",
    "intel": "INTC",
    "intel corporation": "INTC",
    "qualcomm": "QCOM",
    "taiwan semiconductor": "TSM",
    "taiwan semiconductor manufacturing": "TSM",
    "palantir": "PLTR",
    "palantir technologies": "PLTR",
    "snowflake": "SNOW",
    "crowdstrike": "CRWD",
    "crowdstrike holdings": "CRWD",
    "servicenow": "NOW",
    "palo alto networks": "PANW",
    "datadog": "DDOG",
    "cloudflare": "NET",
    "sentinelone": "S",
    "zscaler": "ZS",
    "okta": "OKTA",
    "mongodb": "MDB",
    "elastic": "ESTC",
    "hashicorp": "HCP",
    "broadcom": "AVGO",
    "broadcom inc": "AVGO",
    "applied materials": "AMAT",
    "lam research": "LRCX",
    "lam research corp": "LRCX",
    "micron technology": "MU",
    "micron": "MU",
    "marvell technology": "MRVL",
    "marvell": "MRVL",
    "visa": "V",
    "mastercard": "MA",
    "american express": "AXP",
    "capital one": "COF",
    "capital one financial": "COF",
    "jpmorgan": "JPM",
    "jpmorgan chase": "JPM",
    "goldman sachs": "GS",
    "bank of america": "BAC",
    "wells fargo": "WFC",
    "robinhood": "HOOD",
    "robinhood markets": "HOOD",
    "coinbase": "COIN",
    "coinbase global": "COIN",
    "medtronic": "MDT",
    "biogen": "BIIB",
    "boston scientific": "BSX",
    "novo nordisk": "NVO",
    "eli lilly": "LLY",
    "johnson & johnson": "JNJ",
    "pfizer": "PFE",
    "unitedhealth": "UNH",
    "unitedhealth group": "UNH",
    "first solar": "FSLR",
    "eog resources": "EOG",
    "occidental petroleum": "OXY",
    "booking holdings": "BKNG",
    "general dynamics": "GD",
    "lockheed martin": "LMT",
    "raytheon": "RTX",
    "raytheon technologies": "RTX",
    "s&p global": "SPGI",
    "moody's": "MCO",
    "s&p 500 etf": "SPY",
    "invesco qqq": "QQQ",
    "spacex": "SPACEX",
}

# Companies whose ticker confidence is always "low"
_LOW_CONF_NAMES: frozenset[str] = frozenset({"spacex"})

# ── Text parsing regexes ───────────────────────────────────────────────────────

# Line 1: "[Company] [order_type] [buy|sell]"
_ACTION_RE = re.compile(
    r'^(.+?)\s+(market|limit|stop\s+limit|stop|trailing\s+stop)\s+(buy|sell)\s*$',
    re.IGNORECASE,
)

# Line 2: "Individual · [Month Day]"
_DATE_LINE_RE = re.compile(r'^Individual\s*[·•]\s*(.+?)\s*$', re.IGNORECASE)

# Line 3: canceled or dollar total
_CANCELED_RE = re.compile(r'^canceled$', re.IGNORECASE)
_DOLLAR_RE   = re.compile(r'^\$([\d,]+(?:\.\d+)?)\s*$')

# Line 4: "N share(s) at $price"
_SHARES_RE = re.compile(
    r'^(\d+(?:\.\d+)?)\s+shares?\s+at\s+\$([\d,]+(?:\.\d+)?)\s*$',
    re.IGNORECASE,
)

# ── Claude text API prompt for unknown tickers ─────────────────────────────────

_TICKER_LOOKUP_PROMPT = """\
Map each company name to its primary US exchange ticker symbol.
Return only a valid JSON object mapping company name → ticker.
For private companies (e.g. SpaceX) or names you are not certain about,
use null as the value.
No markdown, no explanation.

Companies: {names_json}
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _lookup_ticker(company: str) -> tuple[str, str]:
    """
    Look up ticker in the local table. Returns (ticker, confidence).
    confidence is 'high', 'low', or '' (unknown — needs API lookup).
    """
    key = company.lower().strip()
    if key in _LOW_CONF_NAMES:
        return (key.upper(), "low")
    if key in _TICKER_MAP:
        return (_TICKER_MAP[key], "high")
    return ("", "")


def _infer_year(date_str: str, reference_date: datetime.date) -> Optional[datetime.date]:
    """
    Given "Jul 9" and a reference date (today), return the most plausible
    calendar date. Assumes current year unless the result is in the future
    (> 1 day ahead), in which case uses prior year.
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


def _parse_text_blocks(text: str) -> tuple[list[dict], int]:
    """
    Parse raw Robinhood History text into a list of raw trade dicts and a
    canceled count.

    Uses "Individual · [date]" as the anchor line; looks one line back for
    company+action and 1–2 lines forward for status and shares.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]  # drop blank lines

    trades: list[dict] = []
    canceled = 0

    for i, line in enumerate(lines):
        m_date = _DATE_LINE_RE.match(line)
        if not m_date:
            continue

        date_str = m_date.group(1).strip()

        # Line before: "[Company] [order_type] [buy|sell]"
        if i == 0:
            continue
        m_action = _ACTION_RE.match(lines[i - 1])
        if not m_action:
            continue

        company = m_action.group(1).strip()
        action  = m_action.group(3).upper()

        # Line after: "Canceled" or "$total"
        if i + 1 >= len(lines):
            continue
        status_line = lines[i + 1]

        if _CANCELED_RE.match(status_line):
            canceled += 1
            continue

        if not _DOLLAR_RE.match(status_line):
            continue  # unexpected format — skip

        # Two lines after: "N share(s) at $price"
        if i + 2 >= len(lines):
            continue
        m_shares = _SHARES_RE.match(lines[i + 2])
        if not m_shares:
            continue

        trades.append({
            "company":  company,
            "action":   action,
            "date_str": date_str,
            "shares":   float(m_shares.group(1)),
            "price":    float(m_shares.group(2).replace(",", "")),
        })

    return trades, canceled


def _resolve_unknown_tickers(
    unknowns: list[str],
    api_key: str,
    model: str,
) -> dict[str, tuple[str, str]]:
    """
    Call Claude text API to map a batch of unknown company names to tickers.
    Returns {company_name_lower: (ticker, confidence)}.
    """
    if not unknowns or not api_key:
        return {}
    try:
        import anthropic  # noqa: PLC0415
        client = anthropic.Anthropic(api_key=api_key)
        prompt = _TICKER_LOOKUP_PROMPT.format(
            names_json=json.dumps(unknowns)
        )
        resp = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
            timeout=30,
        )
        raw = resp.content[0].text.strip() if resp.content else "{}"
        raw = re.sub(r"^```(?:json)?[\r\n]*", "", raw)
        raw = re.sub(r"[\r\n]*```$", "", raw.strip()).strip()
        data = json.loads(raw)
    except Exception:
        return {}

    result: dict[str, tuple[str, str]] = {}
    for name, ticker in data.items():
        key = name.lower().strip()
        if ticker:
            result[key] = (str(ticker).upper().strip(), "high")
        else:
            result[key] = (name.upper().strip(), "low")
    return result


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_robinhood_text(
    text: str,
    api_key: Optional[str] = None,
    model: str = "claude-opus-4-8",
    reference_date: Optional[datetime.date] = None,
) -> dict:
    """
    Parse text pasted from the Robinhood Account → History page.

    Returns the same shape as broker_import.parse_robinhood_csv, plus:
      low_confidence_tickers : list[str]
      parse_warnings         : list[str]
    """
    if reference_date is None:
        reference_date = datetime.date.today()

    empty_trades  = pd.DataFrame(columns=_COLS_TRADE)
    empty_invalid = pd.DataFrame(columns=_COLS_INVALID)

    text = (text or "").strip()
    if not text:
        return {
            "trades": empty_trades, "skipped": {}, "invalid": empty_invalid,
            "error": "No text provided.", "low_confidence_tickers": [],
            "parse_warnings": [],
        }

    raw_trades, total_canceled = _parse_text_blocks(text)

    if not raw_trades and total_canceled == 0:
        return {
            "trades": empty_trades, "skipped": {}, "invalid": empty_invalid,
            "error": (
                "No trades found. Make sure you pasted from the Robinhood "
                "Account → History page. Each order should have four lines: "
                "company+order type, \"Individual · Month Day\", dollar total or "
                "\"Canceled\", then shares."
            ),
            "low_confidence_tickers": [],
            "parse_warnings": [],
        }

    # Resolve tickers: local table first, Claude API for unknowns
    unknowns: list[str] = []
    for rt in raw_trades:
        ticker, conf = _lookup_ticker(rt["company"])
        if not conf:
            key = rt["company"].lower().strip()
            if key not in unknowns:
                unknowns.append(rt["company"])

    api_resolved: dict[str, tuple[str, str]] = {}
    parse_warnings: list[str] = []
    if unknowns and api_key:
        api_resolved = _resolve_unknown_tickers(unknowns, api_key, model)
        if not api_resolved:
            parse_warnings.append(
                f"Could not resolve tickers for: {', '.join(unknowns)} via API. "
                "Ticker column is editable — correct before importing."
            )

    # Dedup across repeated pasted text
    seen_keys: set = set()
    records: list[dict]         = []
    invalid_records: list[dict] = []
    low_conf_tickers: list[str] = []

    for rt in raw_trades:
        company  = rt["company"]
        action   = rt["action"]
        date_str = rt["date_str"]
        shares   = rt["shares"]
        price    = rt["price"]

        # Dedup key
        dup_key = (company.lower(), action, date_str, shares, price)
        if dup_key in seen_keys:
            continue
        seen_keys.add(dup_key)

        # Resolve ticker
        local_ticker, local_conf = _lookup_ticker(company)
        if local_conf:
            ticker, conf = local_ticker, local_conf
        else:
            resolved = api_resolved.get(company.lower().strip())
            if resolved:
                ticker, conf = resolved
            else:
                # Fall back to using the company name as a placeholder
                ticker, conf = company.upper(), "low"

        if conf == "low" and ticker not in low_conf_tickers:
            low_conf_tickers.append(ticker)

        act_date = _infer_year(date_str, reference_date)

        base = {
            "ticker":        ticker,
            "action":        action,
            "shares":        shares,
            "price":         price,
            "activity_date": act_date,
            "company":       company,
        }

        reasons: list[str] = []
        if not ticker:                       reasons.append("no ticker resolved")
        if action not in _TRADE_ACTIONS:     reasons.append(f"unknown action '{action}'")
        if act_date is None:                 reasons.append("unparseable date")
        if shares is None or shares <= 0:    reasons.append("shares ≤ 0 or missing")
        if price  is None or price  <= 0:    reasons.append("price ≤ 0 or missing")

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

    Used to surface "in app, not in pasted history" candidates for review.
    The comparison is content-based (ticker + action + shares + price), not
    date-exact, since the app date (traded_at) may differ by a day from the
    activity date shown in Robinhood.

    Returns a DataFrame with columns: ticker, action, shares, price, traded_at.
    """
    if trades_df is None or not isinstance(trades_df, pd.DataFrame) or trades_df.empty:
        return pd.DataFrame(columns=["ticker", "action", "shares", "price", "traded_at"])

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
            matched_counts[key] += 1
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
    Return the most recent traded_at date from trades tagged as text-paste imports
    (notes containing 'RH screenshot' or 'RH text import').
    Returns None if no prior import of this type.
    """
    if trades_df is None or not isinstance(trades_df, pd.DataFrame) or trades_df.empty:
        return None
    if "notes" not in trades_df.columns or "traded_at" not in trades_df.columns:
        return None

    mask = trades_df["notes"].astype(str).str.contains(
        r"RH screenshot|RH text import", na=False, regex=True
    )
    subset = trades_df[mask]
    if subset.empty:
        return None

    dates = pd.to_datetime(subset["traded_at"], utc=True, errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max().date()
