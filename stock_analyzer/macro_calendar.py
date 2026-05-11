"""
Economic Calendar — static backbone + FRED live layer.

Static backbone:  FOMC, CPI, NFP, GDP, PPI, Retail Sales 2025-2026
                  Published months in advance by Fed / BLS / BEA — 100% reliable.
FRED live layer:  Federal Reserve Economic Data (St. Louis Fed).
                  Free API key required: fred.stlouisfed.org/docs/api/api_key.html
                  (~2 minutes to register, no credit card).
                  Enriches static events with previous and actual released values.
                  No consensus estimates (FRED is official data only, not forecasts).
                  120 requests/minute on free key.

Portfolio impact: maps each event category to affected sectors, then to the
                  user's specific holdings — the key differentiator vs a plain
                  macro calendar.
"""

from datetime import date as _date, datetime as _datetime, timedelta as _td
import pandas as _pd
import pytz as _pytz

def _today_et() -> _date:
    return _datetime.now(_pytz.timezone("America/New_York")).date()

HIGH   = "HIGH"
MEDIUM = "MEDIUM"
LOW    = "LOW"

# ── Static backbone ───────────────────────────────────────────────────────────
# (date, time_ET, event, category, impact, description)
_STATIC: list[tuple] = [

    # FOMC 2025
    ("2025-01-29", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Jan 2025 — Fed rate decision + statement. Press conference follows."),
    ("2025-03-19", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Mar 2025 — Fed rate decision. Quarterly dot-plot and SEP released."),
    ("2025-05-07", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "May 2025 — Fed rate decision + statement."),
    ("2025-06-18", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Jun 2025 — Quarterly dot-plot and SEP released."),
    ("2025-07-30", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Jul 2025 — Fed rate decision + statement."),
    ("2025-09-17", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Sep 2025 — Quarterly dot-plot and SEP released."),
    ("2025-10-29", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Oct 2025 — Fed rate decision + statement."),
    ("2025-12-10", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Dec 2025 — Quarterly dot-plot and SEP released."),

    # FOMC 2026
    ("2026-01-28", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Jan 2026 — Fed rate decision + statement."),
    ("2026-03-18", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Mar 2026 — Quarterly dot-plot and SEP released."),
    ("2026-04-29", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Apr 2026 — Fed rate decision + statement."),
    ("2026-06-10", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Jun 2026 — Quarterly dot-plot and SEP released."),
    ("2026-07-29", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Jul 2026 — Fed rate decision + statement."),
    ("2026-09-16", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Sep 2026 — Quarterly dot-plot and SEP released."),
    ("2026-10-28", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Oct 2026 — Fed rate decision + statement."),
    ("2026-12-09", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Dec 2026 — Quarterly dot-plot and SEP released."),

    # CPI 2025 (BLS ~10th–15th each month)
    ("2025-01-15", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Dec 2024 CPI"),
    ("2025-02-12", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jan 2025 CPI"),
    ("2025-03-12", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Feb 2025 CPI"),
    ("2025-04-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Mar 2025 CPI"),
    ("2025-05-13", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Apr 2025 CPI"),
    ("2025-06-11", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "May 2025 CPI"),
    ("2025-07-15", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jun 2025 CPI"),
    ("2025-08-12", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jul 2025 CPI"),
    ("2025-09-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Aug 2025 CPI"),
    ("2025-10-14", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Sep 2025 CPI"),
    ("2025-11-13", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Oct 2025 CPI"),
    ("2025-12-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Nov 2025 CPI"),

    # CPI 2026
    ("2026-01-14", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Dec 2025 CPI"),
    ("2026-02-11", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jan 2026 CPI"),
    ("2026-03-11", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Feb 2026 CPI"),
    ("2026-04-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Mar 2026 CPI"),
    ("2026-05-13", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Apr 2026 CPI"),
    ("2026-06-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "May 2026 CPI"),
    ("2026-07-14", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jun 2026 CPI"),
    ("2026-08-12", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jul 2026 CPI"),
    ("2026-09-09", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Aug 2026 CPI"),
    ("2026-10-14", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Sep 2026 CPI"),
    ("2026-11-12", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Oct 2026 CPI"),
    ("2026-12-09", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Nov 2026 CPI"),

    # Non-Farm Payrolls 2025 (first Friday of each month)
    ("2025-01-10", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Dec 2024 jobs report"),
    ("2025-02-07", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Jan 2025 jobs report"),
    ("2025-03-07", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Feb 2025 jobs report"),
    ("2025-04-04", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Mar 2025 jobs report"),
    ("2025-05-02", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Apr 2025 jobs report"),
    ("2025-06-06", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "May 2025 jobs report"),
    ("2025-07-03", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Jun 2025 jobs report"),
    ("2025-08-01", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Jul 2025 jobs report"),
    ("2025-09-05", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Aug 2025 jobs report"),
    ("2025-10-03", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Sep 2025 jobs report"),
    ("2025-11-07", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Oct 2025 jobs report"),
    ("2025-12-05", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Nov 2025 jobs report"),

    # Non-Farm Payrolls 2026
    ("2026-01-09", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Dec 2025 jobs report"),
    ("2026-02-06", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Jan 2026 jobs report"),
    ("2026-03-06", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Feb 2026 jobs report"),
    ("2026-04-03", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Mar 2026 jobs report"),
    ("2026-05-08", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Apr 2026 jobs report"),
    ("2026-06-05", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "May 2026 jobs report"),
    ("2026-07-02", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Jun 2026 jobs report"),
    ("2026-08-07", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Jul 2026 jobs report"),
    ("2026-09-04", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Aug 2026 jobs report"),
    ("2026-10-02", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Sep 2026 jobs report"),
    ("2026-11-06", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Oct 2026 jobs report"),
    ("2026-12-04", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Nov 2026 jobs report"),

    # GDP Advance Estimate (BEA — last week of Jan/Apr/Jul/Oct)
    ("2025-01-30", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q4 2024 GDP — first read on quarterly growth"),
    ("2025-04-30", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q1 2025 GDP"),
    ("2025-07-30", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q2 2025 GDP"),
    ("2025-10-30", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q3 2025 GDP"),
    ("2026-01-29", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q4 2025 GDP"),
    ("2026-04-29", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q1 2026 GDP"),
    ("2026-07-29", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q2 2026 GDP"),
    ("2026-10-29", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q3 2026 GDP"),

    # PPI 2026 (typically 1 day after CPI)
    ("2026-01-15", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Dec 2025 PPI — upstream inflation signal"),
    ("2026-02-12", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Jan 2026 PPI"),
    ("2026-03-12", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Feb 2026 PPI"),
    ("2026-04-11", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Mar 2026 PPI"),
    ("2026-05-14", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Apr 2026 PPI"),
    ("2026-06-11", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "May 2026 PPI"),
    ("2026-07-15", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Jun 2026 PPI"),
    ("2026-08-13", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Jul 2026 PPI"),
    ("2026-09-10", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Aug 2026 PPI"),
    ("2026-10-15", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Sep 2026 PPI"),
    ("2026-11-13", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Oct 2026 PPI"),
    ("2026-12-10", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Nov 2026 PPI"),

    # Retail Sales 2026 (mid-month)
    ("2026-01-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Dec 2025 Retail Sales — consumer spending = 70% of GDP"),
    ("2026-02-13", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Jan 2026 Retail Sales"),
    ("2026-03-13", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Feb 2026 Retail Sales"),
    ("2026-04-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Mar 2026 Retail Sales"),
    ("2026-05-15", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Apr 2026 Retail Sales"),
    ("2026-06-12", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "May 2026 Retail Sales"),
    ("2026-07-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Jun 2026 Retail Sales"),
    ("2026-08-14", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Jul 2026 Retail Sales"),
    ("2026-09-11", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Aug 2026 Retail Sales"),
    ("2026-10-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Sep 2026 Retail Sales"),
    ("2026-11-13", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Oct 2026 Retail Sales"),
    ("2026-12-11", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Nov 2026 Retail Sales"),
]

# ── Sector sensitivity map ────────────────────────────────────────────────────
# 3 = directly impacted, 2 = moderately impacted, 1 = minor
_SECTOR_IMPACT: dict[str, dict[str, int]] = {
    "Fed Policy": {
        "__ALL__": 3,
    },
    "Inflation": {
        "AI & Data":      3,
        "AI & Cloud":     3,
        "Semiconductors": 3,
        "Cybersecurity":  2,
        "Clean Energy":   3,
        "Consumer Tech":  2,
        "EV & Auto":      2,
        "Financials":     2,
        "Energy":         2,
        "Healthcare":     1,
        "Defense":        1,
    },
    "Employment": {
        "Consumer Tech":  3,
        "EV & Auto":      3,
        "Financials":     3,
        "Semiconductors": 2,
        "AI & Data":      2,
        "AI & Cloud":     2,
        "Clean Energy":   2,
        "Healthcare":     1,
        "Energy":         1,
        "Defense":        1,
    },
    "Growth": {
        "__ALL__": 2,
    },
    "Consumer": {
        "Consumer Tech":  3,
        "EV & Auto":      3,
        "AI & Data":      2,
        "Semiconductors": 2,
        "Financials":     2,
        "Clean Energy":   1,
        "Healthcare":     1,
        "Defense":        1,
        "Energy":         1,
    },
    "Activity": {
        "Semiconductors": 3,
        "EV & Auto":      2,
        "AI & Data":      2,
        "Financials":     2,
        "Defense":        2,
        "Clean Energy":   1,
        "Energy":         2,
    },
}

# ── Institutional context for each event type ─────────────────────────────────
_EVENT_CONTEXT: dict[str, str] = {
    "FOMC Rate Decision": (
        "The single most market-moving scheduled event of the year. "
        "Rate decisions affect the discount rate for every asset — higher rates compress "
        "valuations on long-duration growth stocks (tech, AI, clean energy) most severely. "
        "Watch for: the rate decision itself, the statement language ('restrictive', 'data dependent', "
        "'patient'), the dot plot (released quarterly — shows where each member sees rates going), "
        "and the press conference tone. Markets often move MORE on the language than the decision."
    ),
    "CPI Inflation": (
        "Hotter-than-expected CPI → rates stay higher longer → growth stocks sell off, "
        "financials and energy may benefit. Cooler CPI → rate-cut expectations rise → "
        "growth/tech rally, long-duration assets reprice upward. "
        "The number that matters most: YoY Core CPI (ex-food and energy). "
        "A 0.1% surprise vs consensus can move the market 1-2%. "
        "Watch shelter costs — they've been the stickiest component and often drive beats/misses."
    ),
    "Non-Farm Payrolls": (
        "Strong jobs = good for consumer/cyclicals but reduces rate-cut probability. "
        "Weak jobs = recession fear OR rate-cut catalyst — reaction depends on context. "
        "Three numbers to watch: (1) headline payrolls vs consensus, (2) unemployment rate, "
        "(3) average hourly earnings — the wage inflation signal. "
        "Markets move most on the surprise relative to estimate, not the absolute number. "
        "Revisions to prior months are often as important as the headline."
    ),
    "GDP Advance Estimate": (
        "The first of three GDP reads — most market impact. "
        "Above-consensus growth = risk-on, cyclicals benefit, rate-cut hopes fade. "
        "Below consensus = defensive rotation, possible recession narrative. "
        "Inside the number: watch the PCE Price Index component — it's the Fed's "
        "preferred inflation gauge and can move rate expectations even if headline GDP is fine."
    ),
    "PPI Producer Prices": (
        "Leading indicator for CPI — if producers pay more, it eventually flows to consumers. "
        "Moves markets less than CPI but can front-run the next CPI reaction. "
        "Most impactful for sectors with thin or compressed margins: tech hardware, EVs, industrials. "
        "Core PPI (ex-food, energy, trade services) is the cleanest signal."
    ),
    "Retail Sales": (
        "Measures consumer spending — the engine of 70% of US GDP. "
        "Strong retail = consumer resilient, cyclicals benefit. Weak = defensive rotation. "
        "Most impactful for consumer-facing sectors: Consumer Tech, EV & Auto, discretionary retail. "
        "The control group (ex-autos, gas, building materials, food services) feeds directly "
        "into GDP calculations and is the cleanest read on underlying consumer health."
    ),
}


def _infer_category(event_name: str) -> str:
    ev = event_name.lower()
    if any(k in ev for k in ("fomc", "fed ", "federal reserve", "rate decision", "powell")):
        return "Fed Policy"
    if any(k in ev for k in ("cpi", "ppi", "inflation", "pce", "price index")):
        return "Inflation"
    if any(k in ev for k in ("payroll", "nonfarm", "non-farm", "employment", "unemployment", "jobs")):
        return "Employment"
    if any(k in ev for k in ("gdp", "gross domestic")):
        return "Growth"
    if any(k in ev for k in ("retail", "consumer spending", "personal spending")):
        return "Consumer"
    if any(k in ev for k in ("pmi", "ism", "manufacturing", "services activity")):
        return "Activity"
    return "Other"


def _days_label(d: _date, today: _date) -> str:
    delta = (d - today).days
    if delta < 0:
        return f"{abs(delta)}d ago"
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    return f"In {delta}d"


def _affected_tickers(category: str, port_df: _pd.DataFrame) -> list[str]:
    """Return portfolio tickers most affected by an event category."""
    if port_df is None or port_df.empty or "Sector" not in port_df.columns:
        return []
    impact_map = _SECTOR_IMPACT.get(category, {})
    if impact_map.get("__ALL__", 0) >= 2:
        return port_df["Ticker"].tolist()
    result = []
    for _, row in port_df.iterrows():
        if impact_map.get(str(row.get("Sector", "")), 0) >= 2:
            result.append(row["Ticker"])
    return result


# ── FRED series config ────────────────────────────────────────────────────────
# Maps each static event name to a FRED series + how to format its value.
# transform: "yoy_pct" needs 13 obs, "mom_pct"/"mom_diff" need 2, "level" needs 1.
_FRED_MAP: dict[str, dict] = {
    "CPI Inflation": {
        "series": "CPIAUCSL",        # CPI All Urban, SA (index level)
        "transform": "yoy_pct",
        "label": "CPI YoY",
        "unit": "%",
        "limit": 13,
    },
    "Non-Farm Payrolls": {
        "series": "PAYEMS",          # Total Nonfarm Employees (thousands)
        "transform": "mom_diff",
        "label": "NFP Chg",
        "unit": "K",
        "limit": 2,
    },
    "GDP Advance Estimate": {
        "series": "A191RL1Q225SBEA", # Real GDP QoQ annualised % change (pre-computed)
        "transform": "level",
        "label": "GDP QoQ Ann.",
        "unit": "%",
        "limit": 2,
    },
    "PPI Producer Prices": {
        "series": "PPIACO",          # PPI All Commodities (index level)
        "transform": "mom_pct",
        "label": "PPI MoM",
        "unit": "%",
        "limit": 2,
    },
    "Retail Sales": {
        "series": "RSAFS",           # Advance Retail Sales, SA (millions)
        "transform": "mom_pct",
        "label": "Retail Sales MoM",
        "unit": "%",
        "limit": 2,
    },
    "FOMC Rate Decision": {
        "series": "FEDFUNDS",        # Effective Fed Funds Rate (level)
        "transform": "level",
        "label": "Fed Funds Rate",
        "unit": "%",
        "limit": 2,
    },
}

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _fred_obs(series_id: str, limit: int, api_key: str | None) -> list[float]:
    """
    Fetch the most recent `limit` observations for a FRED series.
    Returns a list of floats [most_recent, ..., oldest], skipping missing values.
    Requires a free FRED API key — returns [] immediately if no key provided.
    Free key: fred.stlouisfed.org/docs/api/api_key.html (120 req/min).
    """
    if not api_key:
        return []
    import requests as _req
    params: dict = {
        "series_id":    series_id,
        "api_key":      api_key,
        "file_type":    "json",
        "sort_order":   "desc",
        "limit":        limit + 4,   # fetch a few extra to skip any "." missing values
    }
    try:
        resp = _req.get(_FRED_BASE, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json().get("observations", [])
        vals = []
        for obs in data:
            v = obs.get("value", ".")
            if v != ".":
                try:
                    vals.append(float(v))
                except ValueError:
                    pass
            if len(vals) >= limit:
                break
        return vals
    except Exception:
        return []


def _apply_transform(vals: list[float], transform: str, unit: str) -> str | None:
    """Compute the formatted value string from raw observations."""
    if not vals:
        return None
    try:
        if transform == "level":
            return f"{vals[0]:.2f}{unit}"
        if transform == "yoy_pct" and len(vals) >= 13:
            yoy = (vals[0] - vals[12]) / vals[12] * 100
            return f"{yoy:+.2f}{unit}"
        if transform == "mom_pct" and len(vals) >= 2:
            mom = (vals[0] - vals[1]) / vals[1] * 100
            return f"{mom:+.2f}{unit}"
        if transform == "mom_diff" and len(vals) >= 2:
            diff = vals[0] - vals[1]
            return f"{diff:+,.0f}{unit}"
    except (ZeroDivisionError, IndexError):
        pass
    return None


def _fetch_fred(fred_key: str | None, events: list[dict], today: _date) -> None:
    """
    Enrich static event dicts in-place with FRED actual/previous values.
    Uses official St. Louis Fed data — no consensus estimates (FRED is releases only).

    For future events:  previous = last released value, actual = None
    For past events:    actual   = most recent released value,
                        previous = prior period value
    """
    from stock_analyzer import api_health as _ah

    fetched: dict[str, list[float]] = {}

    for ev in events:
        name = ev.get("event", "")
        cfg  = _FRED_MAP.get(name)
        if cfg is None:
            continue

        series = cfg["series"]
        if series not in fetched:
            obs = _fred_obs(series, cfg["limit"], fred_key)
            fetched[series] = obs
            if obs:
                _ah.record("fred", "success")
            else:
                _ah.record("fred", "empty")

        obs = fetched.get(series, [])
        if not obs:
            continue

        # Build a second set of observations shifted by one period for "previous"
        obs_prev = obs[1:] if len(obs) > 1 else []

        current_val  = _apply_transform(obs,      cfg["transform"], cfg["unit"])
        previous_val = _apply_transform(obs_prev, cfg["transform"], cfg["unit"])

        label = cfg["label"]
        if ev["date"] > today:
            # Event not yet released — most recent FRED value is the prior print
            ev["previous"] = ev["previous"] or (f"{label}: {previous_val}" if previous_val else None)
            ev["actual"]   = None
        else:
            # Event already happened — most recent FRED value is the released actual
            ev["actual"]   = ev["actual"]   or (f"{label}: {current_val}"  if current_val  else None)
            ev["previous"] = ev["previous"] or (f"{label}: {previous_val}" if previous_val else None)

        ev["source"] = "static+fred" if ev.get("source") == "static" else ev.get("source", "static+fred")


def build_macro_calendar(
    port_df: _pd.DataFrame,
    fred_key: str | None = None,
    days_ahead: int = 45,
    days_behind: int = 7,
    today: _date | None = None,
    # kept for backward compatibility — ignored, FMP is no longer used
    fmp_key: str | None = None,
) -> list[dict]:
    """
    Build merged calendar covering today - days_behind → today + days_ahead.

    Returns list sorted by date. Each event dict contains:
      date, time_et, event, category, impact, days_label, description,
      context, affected_tickers, previous, estimate, actual, source

    FRED live layer (optional):
      Enriches static events with official released values from St. Louis Fed.
      previous = prior period's released value (e.g. last month's CPI YoY %)
      actual   = most recently released value (populated after release date)
      estimate = always None — FRED publishes data, not forecasts
    """
    if today is None:
        today = _today_et()
    lookback = today - _td(days=days_behind)
    cutoff   = today + _td(days=days_ahead)

    rows: list[dict] = []

    # ── Static backbone ───────────────────────────────────────────────────────
    for (ds, tm, ev, cat, imp, desc) in _STATIC:
        try:
            d = _date.fromisoformat(ds)
        except ValueError:
            continue
        if d < lookback or d > cutoff:
            continue
        rows.append({
            "date":             d,
            "time_et":          tm,
            "event":            ev,
            "category":         cat,
            "impact":           imp,
            "days_label":       _days_label(d, today),
            "description":      desc,
            "context":          _EVENT_CONTEXT.get(ev, ""),
            "affected_tickers": _affected_tickers(cat, port_df),
            "previous":         None,
            "estimate":         None,
            "actual":           None,
            "source":           "static",
        })

    # ── FRED live layer ───────────────────────────────────────────────────────
    # Works with or without a key. Without key FRED allows ~10 req/min (enough
    # for 6 series fetched once per day). With a free key: 120 req/min.
    _fetch_fred(fred_key, rows, today)

    rows.sort(key=lambda x: (x["date"], x["time_et"]))
    return rows
