"""
Economic Calendar — static backbone + FRED live layer.

Static backbone:  FOMC, CPI, NFP, GDP, PPI, Retail Sales 2025-2027
                  Published months in advance by Fed / BLS / BEA / Census — 100% reliable.
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
import re as _re
import pandas as _pd
import pytz as _pytz

from stock_analyzer.constants import (
    REGIME_CPI_CONTROLLED_MAX,
    REGIME_CPI_ELEVATED_MIN,
    REGIME_CPI_HOT_MIN,
    REGIME_FEDFUNDS_TREND_PP,
    REGIME_2S10S_INVERTED_PP,
    REGIME_2S10S_FLAT_PP,
    REGIME_2S10S_STEEP_PP,
    REGIME_UNEMP_DELTA_UP_PP,
    REGIME_UNEMP_DELTA_DOWN_PP,
    REGIME_HY_SPREAD_STRESS_BP,
    REGIME_HY_SPREAD_ELEVATED_BP,
    REGIME_HY_SPREAD_CALM_BP,
    REGIME_SPY_20D_BULL_PCT,
    REGIME_SPY_20D_BEAR_PCT,
    REGIME_VIX_STRESS,
    REGIME_VIX_ELEVATED,
    REGIME_VIX_CALM,
    REGIME_WINNING_SCORE_MIN,
)

def _today_et() -> _date:
    return _datetime.now(_pytz.timezone("America/New_York")).date()

HIGH   = "HIGH"
MEDIUM = "MEDIUM"
LOW    = "LOW"

# ── Static backbone ───────────────────────────────────────────────────────────
# (date, time_ET, event, category, impact, description)
# Shelf life: registered in stock_analyzer/reference_shelf.py — its horizon is DERIVED per event series, so extending any series here clears the warning automatically.
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

    # FOMC 2027 — transcribed from the Fed's tentative 2027 schedule
    # (federalreserve.gov press release 2025-09-05, monetary20250905a). Each is a
    # TWO-DAY meeting; the date below is the SECOND day, when the policy
    # statement is released at 14:00 ET — matching how 2025/2026 are recorded
    # here (e.g. the Jan 27-28 2026 meeting is stored as 2026-01-28). Dot-plot /
    # SEP accompanies the Mar / Jun / Sep / Dec meetings.
    ("2027-01-27", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Jan 2027 — Fed rate decision + statement."),
    ("2027-03-17", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Mar 2027 — Quarterly dot-plot and SEP released."),
    ("2027-04-28", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Apr 2027 — Fed rate decision + statement."),
    ("2027-06-09", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Jun 2027 — Quarterly dot-plot and SEP released."),
    ("2027-07-28", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Jul 2027 — Fed rate decision + statement."),
    ("2027-09-15", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Sep 2027 — Quarterly dot-plot and SEP released."),
    ("2027-10-27", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Oct 2027 — Fed rate decision + statement."),
    ("2027-12-08", "14:00", "FOMC Rate Decision",   "Fed Policy",  HIGH,
     "Dec 2027 — Quarterly dot-plot and SEP released."),

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
    ("2026-05-12", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Apr 2026 CPI"),
    ("2026-06-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "May 2026 CPI"),
    ("2026-07-14", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jun 2026 CPI"),
    ("2026-08-12", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jul 2026 CPI"),
    ("2026-09-09", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Aug 2026 CPI"),
    ("2026-10-14", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Sep 2026 CPI"),
    # Oct / Nov 2026 corrected 2026-11-10 from bls.gov/schedule/news_release/cpi.htm (were 11-12 / 12-09,
    # typed 2026-05-07 in 8e14382; Oct 2026 CPI came out 11-10).
    ("2026-11-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Oct 2026 CPI"),
    ("2026-12-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Nov 2026 CPI"),

    # CPI 2027 — BLS 2027 release schedule, bls.gov/schedule/news_release/cpi.htm (read 2026-10-31)
    ("2027-01-13", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Dec 2026 CPI"),
    ("2027-02-11", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jan 2027 CPI"),
    ("2027-03-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Feb 2027 CPI"),
    ("2027-04-13", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Mar 2027 CPI"),
    ("2027-05-12", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Apr 2027 CPI"),
    ("2027-06-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "May 2027 CPI"),
    ("2027-07-14", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jun 2027 CPI"),
    ("2027-08-11", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Jul 2027 CPI"),
    ("2027-09-14", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Aug 2027 CPI"),
    ("2027-10-14", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Sep 2027 CPI"),
    ("2027-11-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Oct 2027 CPI"),
    ("2027-12-10", "08:30", "CPI Inflation",        "Inflation",   HIGH,   "Nov 2027 CPI"),

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

    # Non-Farm Payrolls 2027 — BLS, bls.gov/schedule/news_release/empsit.htm (read 2026-10-31)
    ("2027-01-08", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Dec 2026 jobs report"),
    ("2027-02-05", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Jan 2027 jobs report"),
    ("2027-03-05", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Feb 2027 jobs report"),
    ("2027-04-02", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Mar 2027 jobs report"),
    ("2027-05-07", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Apr 2027 jobs report"),
    ("2027-06-04", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "May 2027 jobs report"),
    ("2027-07-02", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Jun 2027 jobs report"),
    ("2027-08-06", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Jul 2027 jobs report"),
    ("2027-09-03", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Aug 2027 jobs report"),
    ("2027-10-08", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Sep 2027 jobs report"),
    ("2027-11-05", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Oct 2027 jobs report"),
    ("2027-12-03", "08:30", "Non-Farm Payrolls",    "Employment",  HIGH,   "Nov 2027 jobs report"),

    # GDP Advance Estimate (BEA — last week of Jan/Apr/Jul/Oct)
    ("2025-01-30", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q4 2024 GDP — first read on quarterly growth"),
    ("2025-04-30", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q1 2025 GDP"),
    ("2025-07-30", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q2 2025 GDP"),
    ("2025-10-30", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q3 2025 GDP"),
    ("2026-01-29", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q4 2025 GDP"),
    ("2026-04-29", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q1 2026 GDP"),
    ("2026-07-29", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q2 2026 GDP"),
    ("2026-10-29", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q3 2026 GDP"),

    # GDP Advance Estimate 2027 — BEA 2027 schedule, bea.gov/news/schedule/next-year (read 2026-10-31)
    ("2027-01-28", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q4 2026 GDP"),
    ("2027-04-29", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q1 2027 GDP"),
    ("2027-07-29", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q2 2027 GDP"),
    ("2027-10-28", "08:30", "GDP Advance Estimate", "Growth",      HIGH,   "Q3 2027 GDP"),

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
    # Nov 2026 corrected 2026-11-10 from bls.gov/schedule/news_release/ppi.htm (was 12-10).
    ("2026-12-15", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Nov 2026 PPI"),

    # PPI 2027 — BLS, bls.gov/schedule/news_release/ppi.htm (read 2026-10-31)
    ("2027-01-14", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Dec 2026 PPI — upstream inflation signal"),
    ("2027-02-12", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Jan 2027 PPI"),
    ("2027-03-12", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Feb 2027 PPI"),
    ("2027-04-14", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Mar 2027 PPI"),
    ("2027-05-13", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Apr 2027 PPI"),
    ("2027-06-11", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "May 2027 PPI"),
    ("2027-07-15", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Jun 2027 PPI"),
    ("2027-08-10", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Jul 2027 PPI"),
    ("2027-09-10", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Aug 2027 PPI"),
    ("2027-10-15", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Sep 2027 PPI"),
    ("2027-11-12", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Oct 2027 PPI"),
    ("2027-12-15", "08:30", "PPI Producer Prices",  "Inflation",   MEDIUM, "Nov 2027 PPI"),

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
    # Oct / Nov 2026 corrected 2026-11-10 from census.gov/retail/release_schedule.html (were 11-13 / 12-11).
    ("2026-11-17", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Oct 2026 Retail Sales"),
    ("2026-12-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Nov 2026 Retail Sales"),

    # Retail Sales 2027 — Census economic-indicators calendar, census.gov/economic-indicators/calendar-listview.html (read 2026-10-31)
    ("2027-01-15", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Dec 2026 Retail Sales — consumer spending = 70% of GDP"),
    ("2027-02-17", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Jan 2027 Retail Sales"),
    ("2027-03-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Feb 2027 Retail Sales"),
    ("2027-04-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Mar 2027 Retail Sales"),
    ("2027-05-14", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Apr 2027 Retail Sales"),
    ("2027-06-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "May 2027 Retail Sales"),
    ("2027-07-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Jun 2027 Retail Sales"),
    ("2027-08-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Jul 2027 Retail Sales"),
    ("2027-09-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Aug 2027 Retail Sales"),
    ("2027-10-15", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Sep 2027 Retail Sales"),
    ("2027-11-16", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Oct 2027 Retail Sales"),
    ("2027-12-15", "08:30", "Retail Sales",         "Consumer",    MEDIUM, "Nov 2027 Retail Sales"),
]

# ── Sector sensitivity map ────────────────────────────────────────────────────
# 3 = directly impacted, 2 = moderately impacted, 1 = minor
#
# THIS MAP IS A GATE, keyed on CURATED sector labels only (pinned by
# tests/test_macro_sector_gate.py::test_impact_map_keys_are_curated_labels_only).
# The entry gate translates a candidate's resolved sector with
# sector_vocab.to_curated before testing membership, and HOLDS an entry whose
# label it cannot translate (macro_sector_gate.decide, state "unknown": a hold
# with a visible banner, never a silent pass). portfolio.resolve_sector now
# translates the provider label at the source, so a HELD name and a candidate
# with the same provider label ("Financial Services" -> "Financials") land in
# the same curated bucket and are counted by the held-side exposure math at
# daily_briefing.py's `port_df["Sector"].isin(ev_affected_sectors)`.
#
# What stays outside the map: a label sector_vocab deliberately leaves
# unmapped ("Technology", "Consumer Cyclical", "Communication Services",
# "AI & Data Platforms", "EV & Clean Energy"; see sector_vocab.PROVIDER_TO_
# CURATED). On the entry side that name is held; on the held side it stays out
# of exposure_pct and UNDER-counts the pre-event trim (the calm direction: a
# trim under-fires, it can never manufacture a suppression).
#   STANDING RULE: when a position is opened in a ticker with no TICKER_SECTORS
#   entry, add one - otherwise it is only as good as its provider label.
# Note "Industrials" is both a curated label AND a literal yfinance sector
# string, so uncurated GICS-Industrials holdings enter that exposure sum.
#
# On 2026-08-16 the key set was found not to cover 13 of 73 scan-universe names
# (all of Enterprise Tech and Consumer Staples & Retail, plus BA, RIVN, PYPL) and
# for the held name SPCX — the four rows added below plus the TICKER_SECTORS
# entries in portfolio.py close it. tests/test_scanner.py asserts the per-ticker
# coverage (a curated sector value, or a bucket label, that the map knows).
# Note "Fed Policy" and "Growth" use __ALL__, so those two event types always
# suppressed correctly; the hole only ever affected the four keyed categories.
#
# On 2026-09-02, Materials/Utilities/Real Estate were added to the four keyed
# categories below (Inflation/Employment/Consumer/Activity) — the F-240
# scan-universe refresh's deferred gap: these three GICS sectors had NO
# taxonomy coverage anywhere in the app (see portfolio.py's TICKER_SECTORS/
# SECTOR_ETF/_SECTOR_PROFILES for the matching additions), so a scanned or
# held name in them fell back to the raw provider label and was invisible to
# this gate. Severities user-confirmed, not derived.
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
        "Industrials":    2,
        "Communications": 2,
        "Consumer Staples & Retail": 2,
        "Enterprise Tech":           2,
        # Materials: commodity/input-cost sensitivity drives Inflation (3);
        # ISM/PMI is as direct a read on materials producers as it is on
        # Industrials (Activity 3); no direct consumer revenue since miners/
        # chemicals sell to industry, not households (Consumer 1, Employment 2).
        "Materials":      3,
        # Utilities: regulated bond-proxy duration risk makes this the most
        # rate/inflation-sensitive sector in the map (Inflation 3); demand is
        # inelastic so Employment/Consumer/Activity are all 1 — defensive by
        # construction.
        "Utilities":      3,
        # Real Estate: discount-rate/10yr-yield sensitivity dominates any
        # rent-escalation offset (Inflation 3); office/retail occupancy and
        # industrial/warehouse REITs give moderate Employment/Consumer/Activity
        # reads (2 each).
        "Real Estate":    3,
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
        # Added 2026-08-16. Its absence was a LIVE fail-open, not latent debt:
        # Employment carries Non-Farm Payrolls at HIGH, so all 7 Cybersecurity
        # names (PANW/CRWD/ZS/NET/FTNT/OKTA/S) were unsuppressible ahead of every
        # payrolls print. 2 matches this sector's own Inflation score and its
        # cluster peers AI & Cloud / AI & Data — security budgets are
        # contract-based, so payrolls move them less than consumer/financial
        # names but they remain enterprise-software cyclicals.
        "Cybersecurity":  2,
        # Industrials 3: payrolls drive the cyclical capex/backlog read directly.
        "Industrials":    3,
        # Communications 1: rate-sensitive dividend defensives, closer to utilities.
        "Communications": 1,
        "Consumer Staples & Retail": 2,
        "Enterprise Tech":           2,
        "Materials":      2,
        "Utilities":      1,
        "Real Estate":    2,
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
        "Industrials":    2,
        "Communications": 2,
        # 3: WMT/TGT/COST *are* the retail-sales print — the most direct read
        # of a consumer release anywhere in this map.
        "Consumer Staples & Retail": 3,
        # 1: enterprise IT has no direct consumer revenue.
        "Enterprise Tech":           1,
        "Materials":      1,
        "Utilities":      1,
        "Real Estate":    2,
        # Added 2026-09-09 (severities set with the user — closes a coverage
        # gap flagged 2026-08-16; both were inert until now since Consumer's
        # own _STATIC event is MEDIUM and both gate sites filter to HIGH, but
        # a fail-open trap was already found once in this table — see the
        # Employment/Cybersecurity comment below — so these are filled ahead
        # of the trigger, not after it fires).
        # 2: mirrors AI & Data — these two sectors carry identical severity
        # everywhere else in this table (Inflation, Employment); ad-spend and
        # consumer-app monetization give moderate indirect exposure to a
        # retail-sales print.
        "AI & Cloud":     2,
        # 1: same reasoning as Enterprise Tech above — security budgets are
        # enterprise contracts, disconnected from consumer spending.
        "Cybersecurity":  1,
    },
    "Activity": {
        "Semiconductors": 3,
        "EV & Auto":      2,
        "AI & Data":      2,
        "Financials":     2,
        "Defense":        2,
        "Clean Energy":   1,
        "Energy":         2,
        # 3: ISM/PMI is *the* industrials print.
        "Industrials":    3,
        "Communications": 1,
        # 1: ISM manufacturing barely touches staples/retail demand.
        "Consumer Staples & Retail": 1,
        # 2: IT capex tracks PMI with a lag.
        "Enterprise Tech":           2,
        "Materials":      3,
        "Utilities":      1,
        "Real Estate":    2,
        # Added 2026-09-09 (severities set with the user — closes a coverage
        # gap flagged 2026-08-16; inert until now since Activity carries no
        # _STATIC rows at all today, so nothing yet reads these cells live).
        # 2: mirrors AI & Data — cloud infra capex tracks business-investment
        # cycles similarly to the AI & Data cluster peer.
        "AI & Cloud":     2,
        # 1: mirrors Consumer Staples & Retail's own reasoning above — Consumer
        # Tech revenue is discretionary-demand-driven, not industrial-capex-
        # driven, so ISM/PMI barely touches it.
        "Consumer Tech":  1,
        # 2: mirrors Enterprise Tech above — security spend is a subset of
        # enterprise IT capex, tracking PMI with the same lag.
        "Cybersecurity":  2,
        # 1: Healthcare is LOW (1) everywhere else in this table (Inflation,
        # Employment, Consumer) — defensive, non-cyclical, uncorrelated with
        # the manufacturing/services print.
        "Healthcare":     1,
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


def is_all_sector_category(category: str) -> bool:
    """True when an event category's impact map is the `__ALL__` sentinel
    (Fed Policy, Growth) — i.e. it threatens every sector equally and so
    provides no discrimination about which specific holding is exposed.
    Used by catalyst_stress.py to exclude these from structural-overlap
    ranking (an event hitting everything can't identify a specific weak
    point, which is the opposite of what that ranking needs)."""
    return _SECTOR_IMPACT.get(category, {}).get("__ALL__", 0) >= 2


def affected_sectors(category: str, min_severity: int = 2) -> set[str]:
    """
    Return the set of sector names materially impacted by a macro event of the
    given category, at or above min_severity (2 = moderate, 3 = direct).
    The sentinel "__ALL__" indicates all sectors are impacted.
    """
    impact_map = _SECTOR_IMPACT.get(category, {})
    if impact_map.get("__ALL__", 0) >= min_severity:
        return {"__ALL__"}
    return {sec for sec, sev in impact_map.items()
            if sec != "__ALL__" and sev >= min_severity}


# ── FRED series config ────────────────────────────────────────────────────────
# Maps each static event name to a FRED series + how to format its value.
# limit must be large enough to compute BOTH current and previous values:
#   yoy_pct  : needs 13 obs for current → fetch 14 so obs[1:] also has 13
#   mom_pct/
#   mom_diff : needs  2 obs for current → fetch  3 so obs[1:] also has 2
#   level    : needs  1 obs for current → fetch  2 so obs[1]  is available
_FRED_MAP: dict[str, dict] = {
    "CPI Inflation": {
        "series": "CPIAUCNS",        # CPI All Urban, NSA — matches the headline number quoted in media
        "transform": "yoy_pct",
        "label": "CPI YoY",
        "unit": "%",
        "limit": 14,                 # 13 for current + 1 so previous can also compute YoY
    },
    "Non-Farm Payrolls": {
        "series": "PAYEMS",          # Total Nonfarm Employees (thousands)
        "transform": "mom_diff",
        "label": "NFP Chg",
        "unit": "K",
        "limit": 3,                  # 2 for current diff + 1 extra so previous diff has 2 obs
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
        "limit": 3,                  # 2 for current pct + 1 extra for previous
    },
    "Retail Sales": {
        "series": "RSAFS",           # Advance Retail Sales, SA (millions)
        "transform": "mom_pct",
        "label": "Retail Sales MoM",
        "unit": "%",
        "limit": 3,
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
    from stock_analyzer import api_health as _ah
    import requests as _req
    api_key = api_key.strip()   # guard against accidental whitespace in secrets
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
            _ah.record("fred", "error",
                       f"HTTP {resp.status_code} ({series_id}): {resp.text[:120]}")
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
    except Exception as exc:
        _ah.record("fred", "error", f"{series_id}: {str(exc)[:120]}")
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


def _fred_series_stamp(series_id: str, api_key: str | None) -> str | None:
    """Raw FRED ``last_updated`` stamp for a series ("YYYY-MM-DD HH:MM:SS-HH",
    Central time with a UTC offset), or None if unavailable."""
    if not api_key:
        return None
    from stock_analyzer import api_health as _ah
    import requests as _req
    api_key = api_key.strip()
    params = {"series_id": series_id, "api_key": api_key, "file_type": "json"}
    try:
        resp = _req.get(
            "https://api.stlouisfed.org/fred/series",
            params=params, timeout=10,
        )
        if resp.status_code != 200:
            _ah.record("fred", "error",
                       f"series meta {series_id}: HTTP {resp.status_code}")
            return None
        data = resp.json().get("seriess", [])
        if not data:
            return None
        lu = (data[0].get("last_updated") or "").strip()
        return lu or None
    except Exception as exc:
        _ah.record("fred", "error",
                   f"series meta {series_id}: {str(exc)[:120]}")
        return None


def _fred_series_last_updated(series_id: str, api_key: str | None) -> _date | None:
    """
    Return the date FRED last updated this series (proxy for the actual release
    date), or None if unavailable. Used by the drift-detection pass to flag
    when a static _STATIC date is out-of-sync with the real release schedule.
    """
    lu = _fred_series_stamp(series_id, api_key)
    if not lu:
        return None
    try:
        # Format: "YYYY-MM-DD HH:MM:SS-HH"
        return _date.fromisoformat(lu.split(" ")[0])
    except Exception:
        return None


_FRED_STAMP_RE = _re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?([+-])(\d{2})(?::?(\d{2}))?$"
)


def _parse_fred_stamp_et(lu: str | None) -> _datetime | None:
    """Parse a FRED stamp ("YYYY-MM-DD HH:MM:SS-HH", Central, with its UTC
    offset) into a tz-aware America/New_York datetime. None on any failure
    (including a stamp with no offset: the wall-clock time can't be placed)."""
    if not lu:
        return None
    try:
        m = _re.match(_FRED_STAMP_RE, lu.strip())
        if not m:
            return None
        d_s, t_s, sign, hh, mm = m.groups()
        minutes = int(hh) * 60 + int(mm or 0)
        if sign == "-":
            minutes = -minutes
        naive = _datetime.fromisoformat(f"{d_s}T{t_s}")
        aware = _pytz.FixedOffset(minutes).localize(naive)
        return aware.astimezone(_pytz.timezone("America/New_York"))
    except Exception:
        return None


def _fred_series_last_updated_et(series_id: str, api_key: str | None) -> _datetime | None:
    """FRED's series last-updated stamp as a tz-aware America/New_York
    datetime, or None if unavailable or unparseable. Sibling of
    ``_fred_series_last_updated`` (which keeps only the stamp's date)."""
    return _parse_fred_stamp_et(_fred_series_stamp(series_id, api_key))


def _same_day_released(last_updated_et: _datetime | None, today: _date,
                       time_et: str | None) -> bool:
    """Whether a release scheduled for ``today`` has actually landed in FRED.

    True only when FRED's last-updated stamp, in ET, falls on ``today`` AND its
    ET wall-clock time is at or after the event's scheduled ``time_et``
    ("HH:MM"). Before the release FRED's newest observation is still the prior
    print, and a same-day stamp earlier than the release time is a different
    update of the series (MacroSameDay). A missing / unparseable stamp or
    ``time_et`` is False (fail safe: ``entry_eligibility.macro_block_map`` then
    lifts the hold by the wall clock). ``today`` is the ET date passed in;
    never read the clock here.
    """
    if last_updated_et is None or not time_et:
        return False
    try:
        sched = _datetime.strptime(str(time_et).strip(), "%H:%M").time()
    except ValueError:
        return False
    if last_updated_et.date() != today:
        return False
    return last_updated_et.time().replace(tzinfo=None) >= sched


def _fetch_fred(fred_key: str | None, events: list[dict], today: _date) -> None:
    """
    Enrich static event dicts in-place with FRED actual/previous values.
    Uses official St. Louis Fed data — no consensus estimates (FRED is releases only).

    For future events:  previous = last released value, actual = None
    For past events:    actual   = most recent released value,
                        previous = prior period value

    A same-day event is treated as a past one only when FRED's series was
    updated today (ET) at or after the event's release time
    (``_same_day_released``); otherwise it reads as a future event (actual
    None, previous = FRED's newest value).

    Also populates three drift/release-tracking fields on every FRED-mapped event:
      released_at  : date FRED last updated the series (release proxy), or None
      released     : True for a past event with a FRED actual, or a same-day
                     event FRED updated at or after its release time
      drift_days   : ev["date"] - released_at; positive means the static date
                     trails the actual release (e.g. static May 13 but FRED
                     released May 12 → drift_days = 1). Set only when both
                     dates are known and the event is past, or same-day and
                     confirmed released.
    """
    from stock_analyzer import api_health as _ah

    fetched: dict[str, list[float]] = {}
    fetched_meta: dict[str, _datetime | None] = {}

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

        # obs[0]  = most recently released value
        # obs[1:] = shifted one period back — used to compute the "previous" reading
        obs_shifted = obs[1:] if len(obs) > 1 else []

        current_val  = _apply_transform(obs,         cfg["transform"], cfg["unit"])
        previous_val = _apply_transform(obs_shifted, cfg["transform"], cfg["unit"])

        # One series-meta fetch per unique series — cached in fetched_meta so a
        # CPI calendar entry and a CPI macro signal don't both pay for it.
        if series not in fetched_meta:
            fetched_meta[series] = _fred_series_last_updated_et(series, fred_key)
        last_updated = fetched_meta[series]

        label = cfg["label"]
        # A same-day event counts as released only once FRED has updated the
        # series today; before the release FRED's newest obs is still the PRIOR
        # print (MacroSameDay), so it must read like a future event.
        _is_future = ev["date"] > today or (
            ev["date"] == today and not _same_day_released(last_updated, today, ev.get("time_et"))
        )
        if _is_future:
            # Future event: the most recently released FRED value IS the "previous" print
            # (e.g. for May 13 CPI, obs[0] = March CPI YoY = the prior reading).
            # previous_val (obs[1:]) would be one period further back — too old.
            ev["previous"] = ev["previous"] or (f"{label}: {current_val}"  if current_val  else None)
            ev["actual"]   = None
        else:
            # Past event: obs[0] is the released actual; obs_shifted gives the prior period.
            ev["actual"]   = ev["actual"]   or (f"{label}: {current_val}"  if current_val  else None)
            ev["previous"] = ev["previous"] or (f"{label}: {previous_val}" if previous_val else None)

        ev["source"] = "static+fred" if ev.get("source") == "static" else ev.get("source", "static+fred")

        # ── Drift / release tracking ─────────────────────────────────────────
        if last_updated:
            ev["released_at"] = last_updated.date().isoformat()
            if not _is_future:   # past, or same-day confirmed released
                # Drift sign convention: positive = static date trails the real
                # release (e.g. static = May 13, release = May 12 → +1).
                ev["drift_days"] = (ev["date"] - last_updated.date()).days
        # released flag — True iff a past event has a FRED-populated actual.
        # Future events stay False (release hasn't happened yet).
        ev["released"] = bool(ev["date"] <= today and ev.get("actual"))


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
            # Release/drift fields populated by _fetch_fred when a FRED series
            # is mapped for this event; non-FRED events keep these defaults.
            "released":         False,
            "released_at":      None,
            "drift_days":       None,
        })

    # ── FRED live layer ───────────────────────────────────────────────────────
    # Works with or without a key. Without key FRED allows ~10 req/min (enough
    # for 6 series fetched once per day). With a free key: 120 req/min.
    _fetch_fred(fred_key, rows, today)

    rows.sort(key=lambda x: (x["date"], x["time_et"]))
    return rows


# ── Macro regime detection ─────────────────────────────────────────────────────

# Per-event, per-scenario regime notes.  Only defined where the rate_cut or
# stagflation regime inverts (or meaningfully alters) the textbook reaction.
_REGIME_NOTES: dict[str, dict[str, dict[str, str]]] = {
    "rate_cut": {
        "Non-Farm Payrolls": {
            "bear": (
                "Rate-cut regime: a jobs miss accelerates Fed easing expectations — "
                "market reaction is typically positive for growth/tech. "
                "Textbook bear call likely does not apply today."
            ),
            "bull": (
                "Rate-cut regime: a strong jobs print may slow the pace of cuts — "
                "growth stocks often sell off on a 'too-hot' jobs beat."
            ),
            "base": "In-line data unlikely to shift rate-cut expectations materially.",
        },
        "CPI Inflation": {
            "bear": "Cool CPI reinforces the rate-cut path — typically bullish for growth/tech.",
            "bull": (
                "Hot CPI threatens the rate-cut timeline — "
                "negative for growth stocks even if other signals are positive."
            ),
            "base": "CPI in-line; rate-cut path unchanged.",
        },
        "FOMC Rate Decision": {
            "bear": "More cuts (or larger cut) than expected — strongly bullish in this regime.",
            "bull": "Fewer cuts than expected — market disappointment likely.",
            "base": "Decision as expected — muted reaction.",
        },
        "GDP Advance Estimate": {
            "bear": (
                "Weak GDP in a rate-cut regime = Fed cuts faster. "
                "Mild positive for equities if cut expectations dominate recession fears."
            ),
            "bull": "Strong GDP = Fed may slow cuts — mixed reaction.",
            "base": "In-line GDP — neutral.",
        },
    },
    "stagflation_risk": {
        "Non-Farm Payrolls": {
            "bear": (
                "Stagflation signal: weak jobs + elevated inflation = "
                "Fed is trapped. Typically very negative — can't cut without re-igniting inflation."
            ),
            "bull": (
                "Strong jobs with high inflation = Fed remains restricted. "
                "Mixed: economy ok but rate relief unlikely."
            ),
            "base": "Mixed signals — watch Fed language for guidance.",
        },
        "CPI Inflation": {
            "bear": "Disinflation with weak growth — positive if it opens the door to cuts.",
            "bull": "Stagflation confirmed — strongly negative.",
            "base": "Inflation sticky — rate relief remains distant.",
        },
    },
    "inflation_fight": {
        "Non-Farm Payrolls": {
            "bear": (
                "Jobs miss = less wage pressure — mild positive as it reduces hike urgency, "
                "but recession risk begins to emerge."
            ),
            "bull": (
                "Strong jobs = more wage inflation = more rate hikes likely. "
                "Negative for growth and rate-sensitive sectors."
            ),
            "base": "In-line — Fed stays on its current path.",
        },
        "CPI Inflation": {
            "bear": "Cooling CPI is the best possible outcome in this regime — expect a relief rally.",
            "bull": "Hot CPI confirms more hikes — strongly negative.",
            "base": "Inflation holding — Fed maintains restrictive stance.",
        },
    },
}


_NEUTRAL_REGIME: dict = {
    "regime": "neutral", "label": "Data-Dependent", "icon": "📊",
    "color": "#6b7280", "bg": "#111827", "fed_trend": "unknown", "cpi_yoy": None,
    "rationale": (
        "Regime detection unavailable — using textbook scenario interpretation. "
        "A free FRED key (fred.stlouisfed.org) enables auto-detection."
    ),
    "source": "fallback",
    "confidence": 0,
    "scores": {},
    "signals": [],
}


def detect_macro_regime(fred_key: str | None = None) -> dict:
    """
    Auto-detect the current macro regime using 7-signal weighted scoring.

    Signals:
      1. Fed Funds trend          (FEDFUNDS, 4 obs monthly)
      2. CPI YoY                  (CPIAUCNS, 14 obs monthly)
      3. 2yr-10yr yield spread    (DGS2 + DGS10, 3 obs each daily)
      4. Unemployment 3-month Δ   (UNRATE, 4 obs monthly)
      5. HY credit spread         (BAMLH0A0HYM2, 3 obs daily)
      6. SPY 20-day return        (yfinance)
      7. VIX level                (yfinance, same download)

    Returns a dict with:
      regime      : "rate_cut" | "inflation_fight" | "recession_fear" |
                    "stagflation_risk" | "neutral"
      label       : human-readable name
      icon        : emoji
      color       : hex colour for UI accents
      bg          : hex background colour
      fed_trend   : "cutting" | "hiking" | "holding" | "unknown"
      cpi_yoy     : float or None
      rationale   : one-sentence explanation
      confidence  : int 0-100
      scores      : dict of raw scores per regime
      signals     : list of (label, reading, icon, regime_hint) tuples
      source      : "fred+market" | "fallback"
    """
    try:
        _key = str(fred_key).strip() if fred_key else None
    except Exception:
        return _NEUTRAL_REGIME

    scores: dict[str, int] = {
        "rate_cut": 0,
        "inflation_fight": 0,
        "recession_fear": 0,
        "stagflation_risk": 0,
    }
    signals: list[tuple] = []
    any_success = False

    fed_trend = "unknown"
    cpi_yoy: float | None = None

    # ── Signal 1: Fed Funds trend ──────────────────────────────────────────────
    try:
        fed_obs = _fred_obs("FEDFUNDS", 4, _key)
        if len(fed_obs) >= 3:
            any_success = True
            diff = fed_obs[0] - fed_obs[2]
            if diff < -REGIME_FEDFUNDS_TREND_PP:
                fed_trend = "cutting"
                scores["rate_cut"] += 3
                signals.append(("Fed Funds", f"{fed_obs[0]:.2f}% (cutting)", "✂️", "rate_cut"))
            elif diff > REGIME_FEDFUNDS_TREND_PP:
                fed_trend = "hiking"
                scores["inflation_fight"] += 3
                signals.append(("Fed Funds", f"{fed_obs[0]:.2f}% (hiking)", "🔺", "inflation_fight"))
            else:
                fed_trend = "holding"
                signals.append(("Fed Funds", f"{fed_obs[0]:.2f}% (holding)", "→", None))
    except Exception:
        pass

    # ── Signal 2: CPI YoY ─────────────────────────────────────────────────────
    try:
        cpi_obs = _fred_obs("CPIAUCNS", 14, _key)
        if len(cpi_obs) >= 13:
            any_success = True
            cpi_yoy = (cpi_obs[0] - cpi_obs[12]) / cpi_obs[12] * 100
            if cpi_yoy > REGIME_CPI_HOT_MIN:
                scores["inflation_fight"] += 3
                scores["stagflation_risk"] += 2
                scores["rate_cut"] -= 2
                signals.append(("CPI YoY", f"{cpi_yoy:.1f}%", "🔥", "inflation_fight"))
            elif cpi_yoy >= REGIME_CPI_ELEVATED_MIN:
                scores["inflation_fight"] += 1
                scores["stagflation_risk"] += 1
                signals.append(("CPI YoY", f"{cpi_yoy:.1f}%", "⚠️", "inflation_fight"))
            elif cpi_yoy < REGIME_CPI_CONTROLLED_MAX:
                scores["rate_cut"] += 2
                scores["inflation_fight"] -= 2
                signals.append(("CPI YoY", f"{cpi_yoy:.1f}%", "✅", "rate_cut"))
            else:
                signals.append(("CPI YoY", f"{cpi_yoy:.1f}%", "→", None))
    except Exception:
        pass

    # ── Signal 3: 2yr-10yr yield spread ───────────────────────────────────────
    try:
        dgs2_obs  = _fred_obs("DGS2",  3, _key)
        dgs10_obs = _fred_obs("DGS10", 3, _key)
        if dgs2_obs and dgs10_obs:
            any_success = True
            spread = dgs10_obs[0] - dgs2_obs[0]
            spread_str = f"{spread:+.2f}%"
            if spread < REGIME_2S10S_INVERTED_PP:
                scores["recession_fear"] += 3
                scores["rate_cut"] -= 1
                signals.append(("2s10s Spread", spread_str + " (inverted)", "🔴", "recession_fear"))
            elif spread < REGIME_2S10S_FLAT_PP:
                scores["recession_fear"] += 1
                signals.append(("2s10s Spread", spread_str + " (flat/inv)", "⚠️", "recession_fear"))
            elif spread > REGIME_2S10S_STEEP_PP:
                scores["rate_cut"] += 1
                scores["recession_fear"] -= 1
                signals.append(("2s10s Spread", spread_str + " (steep)", "🟢", "rate_cut"))
            else:
                signals.append(("2s10s Spread", spread_str, "→", None))
    except Exception:
        pass

    # ── Signal 4: Unemployment 3-month delta ──────────────────────────────────
    try:
        unrate_obs = _fred_obs("UNRATE", 4, _key)
        if len(unrate_obs) >= 4:
            any_success = True
            unemp_delta = unrate_obs[0] - unrate_obs[3]
            delta_str = f"{unemp_delta:+.1f}pp"
            if unemp_delta > REGIME_UNEMP_DELTA_UP_PP:
                scores["recession_fear"] += 2
                scores["stagflation_risk"] += 1
                signals.append(("Unemployment Δ3m", delta_str, "⚠️", "recession_fear"))
            elif unemp_delta < REGIME_UNEMP_DELTA_DOWN_PP:
                scores["rate_cut"] -= 1
                scores["inflation_fight"] += 1
                scores["recession_fear"] -= 1
                signals.append(("Unemployment Δ3m", delta_str, "🟢", "rate_cut"))
            else:
                signals.append(("Unemployment Δ3m", delta_str + " (stable)", "→", None))
    except Exception:
        pass

    # ── Signal 5: HY credit spread ────────────────────────────────────────────
    try:
        hy_obs = _fred_obs("BAMLH0A0HYM2", 3, _key)
        if hy_obs:
            any_success = True
            hy_spread = hy_obs[0]   # in basis points (percent)
            # BAMLH0A0HYM2 is reported as a percent (e.g. 4.5 = 450 bps)
            hy_bps = hy_spread * 100
            hy_str = f"{hy_bps:.0f}bps"
            if hy_bps > REGIME_HY_SPREAD_STRESS_BP:
                scores["recession_fear"] += 3
                scores["rate_cut"] -= 2
                signals.append(("HY Spread", hy_str, "🔴", "recession_fear"))
            elif hy_bps >= REGIME_HY_SPREAD_ELEVATED_BP:
                scores["recession_fear"] += 1
                scores["stagflation_risk"] += 1
                signals.append(("HY Spread", hy_str, "⚠️", "recession_fear"))
            elif hy_bps < REGIME_HY_SPREAD_CALM_BP:
                scores["rate_cut"] += 2
                scores["recession_fear"] -= 2
                signals.append(("HY Spread", hy_str, "🟢", "rate_cut"))
            else:
                signals.append(("HY Spread", hy_str + " (normal)", "→", None))
    except Exception:
        pass

    # ── Signals 6 & 7: SPY 20-day return + VIX ────────────────────────────────
    try:
        import yfinance as _yf
        _mkt = _yf.download(["SPY", "^VIX"], period="25d", progress=False, auto_adjust=True)
        # Handle both MultiIndex and flat column structures
        if isinstance(_mkt.columns, _pd.MultiIndex):
            _spy_close = _mkt["Close"]["SPY"].dropna()
            _vix_close = _mkt["Close"]["^VIX"].dropna()
        else:
            _spy_close = _mkt.get("SPY", _pd.Series(dtype=float)).dropna()
            _vix_close = _mkt.get("^VIX", _pd.Series(dtype=float)).dropna()

        # Signal 6: SPY 20-day return
        if len(_spy_close) >= 2:
            any_success = True
            _spy_ret = (_spy_close.iloc[-1] - _spy_close.iloc[0]) / _spy_close.iloc[0] * 100
            _spy_str = f"{_spy_ret:+.1f}%"
            if _spy_ret > REGIME_SPY_20D_BULL_PCT:
                scores["rate_cut"] += 2
                scores["recession_fear"] -= 2
                signals.append(("SPY 20d Return", _spy_str, "🟢", "rate_cut"))
            elif _spy_ret < REGIME_SPY_20D_BEAR_PCT:
                scores["recession_fear"] += 1
                signals.append(("SPY 20d Return", _spy_str, "🔴", "recession_fear"))
            else:
                signals.append(("SPY 20d Return", _spy_str, "→", None))

        # Signal 7: VIX level
        if len(_vix_close) >= 1:
            any_success = True
            _vix = float(_vix_close.iloc[-1])
            _vix_str = f"{_vix:.1f}"
            if _vix > REGIME_VIX_STRESS:
                scores["recession_fear"] += 2
                scores["rate_cut"] -= 1
                signals.append(("VIX", _vix_str, "🔴", "recession_fear"))
            elif _vix >= REGIME_VIX_ELEVATED:
                scores["recession_fear"] += 1
                signals.append(("VIX", _vix_str, "⚠️", "recession_fear"))
            elif _vix < REGIME_VIX_CALM:
                scores["rate_cut"] += 1
                scores["recession_fear"] -= 1
                signals.append(("VIX", _vix_str, "🟢", "rate_cut"))
            else:
                signals.append(("VIX", _vix_str, "→", None))
    except Exception:
        pass

    # ── Determine winner ───────────────────────────────────────────────────────
    if not any_success:
        return _NEUTRAL_REGIME

    winner = max(scores, key=lambda k: scores[k])
    winning_score = scores[winner]

    # Hard gate: "Rate-Cut Optimism" claims "controlled inflation" in its
    # rationale, so it must not be selected when CPI exceeds the controlled
    # ceiling (REGIME_CPI_CONTROLLED_MAX) even if other risk-on signals (low
    # VIX, strong SPY) push the score that way. Without this, a 3.95% CPI print
    # could still land in the rate-cut regime — a direct contradiction of the
    # label.
    if (
        winner == "rate_cut"
        and cpi_yoy is not None
        and cpi_yoy > REGIME_CPI_CONTROLLED_MAX
    ):
        _alt_scores = {k: v for k, v in scores.items() if k != "rate_cut"}
        if _alt_scores:
            winner = max(_alt_scores, key=lambda k: _alt_scores[k])
            winning_score = scores[winner]

    # Confidence: winner's positive score share of total positive scores
    pos_total = sum(max(0, v) for v in scores.values())
    confidence = round(scores[winner] / pos_total * 100) if pos_total > 0 else 0

    # Fall back to neutral if signal is too weak
    if winning_score <= REGIME_WINNING_SCORE_MIN:
        winner = "neutral"

    source = "fred+market" if any_success else "fallback"

    _REGIME_DEFS = {
        "rate_cut": {
            "label":     "Rate-Cut Optimism",
            "icon":      "✂️",
            "color":     "#3b82f6",
            "bg":        "#0a1628",
            "rationale": (
                "Fed easing + controlled inflation + risk-on signals. "
                "Weak macro data = faster cuts = growth stocks rally."
            ),
        },
        "inflation_fight": {
            "label":     "Inflation Fight",
            "icon":      "🔥",
            "color":     "#f59e0b",
            "bg":        "#1a1200",
            "rationale": (
                "Inflation and/or Fed hiking pressure dominant. "
                "Strong data = more hikes = growth stocks under pressure."
            ),
        },
        "recession_fear": {
            "label":     "Recession Fear",
            "icon":      "📉",
            "color":     "#ef4444",
            "bg":        "#1a0000",
            "rationale": (
                "Multiple signals flag growth slowdown risk. "
                "Weak data confirms fears — expect defensive rotation."
            ),
        },
        "stagflation_risk": {
            "label":     "Stagflation Risk",
            "icon":      "⚠️",
            "color":     "#8b5cf6",
            "bg":        "#1a0a2e",
            "rationale": (
                "Elevated inflation + slowing growth = Fed is trapped. "
                "Neither cutting nor holding resolves the problem."
            ),
        },
        "neutral": {
            "label":     "Data-Dependent",
            "icon":      "📊",
            "color":     "#6b7280",
            "bg":        "#111827",
            "rationale": (
                "Mixed signals — no dominant regime. "
                "Market reactions follow standard textbook patterns."
            ),
        },
    }

    defn = _REGIME_DEFS[winner]

    return {
        "regime":     winner,
        "label":      defn["label"],
        "icon":       defn["icon"],
        "color":      defn["color"],
        "bg":         defn["bg"],
        "fed_trend":  fed_trend,
        "cpi_yoy":    cpi_yoy,
        "rationale":  defn["rationale"],
        "confidence": confidence,
        "scores":     dict(scores),
        "signals":    signals,
        "source":     source,
    }
