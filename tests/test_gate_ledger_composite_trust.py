"""G-07 / G-16 gate-ledger rows must carry composite_score=None unless the
bundle's composite would have been trusted for a buy (2026-10-15 review)."""
from datetime import date, timedelta

import pytest

from stock_analyzer import asset_type, gate_ledger
from stock_analyzer.constants import COMPOSITE_BUY, GROW_TODAY_MAX_FUND_AGE_DAYS, SECTOR_CEILING
from stock_analyzer.daily_briefing import _grow_today
from stock_analyzer.gate_ledger_readout import (
    STATUS_EXCLUDED_NULL_COMPOSITE, enrich_and_grade,
)
from stock_analyzer.macro_calendar import HIGH as _MACRO_HIGH
from tests.conftest import find_item, make_port_df

pytestmark = pytest.mark.fast

_TODAY = date(2026, 7, 27)
_TOTAL = COMPOSITE_BUY + 10

_BREAKS = {
    "trusted": {},
    "legacy": None,
    "stale_as_of": {"stale_as_of": "2026-07-20"},
    "stale_fund": {"fund_cache_age_days": GROW_TODAY_MAX_FUND_AGE_DAYS + 1},
    "t_unavailable": {"t_available": False},
    "val_unavailable": {"val_available": False},
    "bq_unavailable": {"fundamentals_available": False},
    "etf_flags_true": {"asset_type": asset_type.ASSET_TYPE_ETF, "fundamentals_available": True,
                       "val_available": True},
}


def _bundle(kind, sector):
    b = {"total": _TOTAL, "rec": {"label": "Buy"}, "sector": sector}
    if kind == "legacy":
        return b
    b.update({"fundamentals_available": True, "val_available": True, "t_available": True})
    b.update(_BREAKS[kind])
    return b


def _scanner(sector):
    import pandas as pd
    return pd.DataFrame([{
        "Ticker": "NEW", "Score": _TOTAL, "Price": 50.0, "Signal": "Buy", "Sector": sector,
        "RSI": 60.0, "1M Momentum": 5.0, "Trend": "Up",
    }])


def _g16(kind):
    port_df = make_port_df([{"ticker": "HELD", "weight": SECTOR_CEILING + 5.0, "sector": "Tech"}])
    grow = _grow_today(port_df, _scanner("Tech"), [], {}, _TODAY, 100_000.0, {"tone": "bull"},
                       composites={"NEW": _bundle(kind, "Tech")})
    return find_item(grow["sector_blocked_picks"], "NEW"), grow


def _g07(kind):
    ev = [{"impact": _MACRO_HIGH, "date": (_TODAY + timedelta(days=1)).isoformat(),
           "category": "Inflation", "event": "CPI Inflation"}]
    port_df = make_port_df([{"ticker": "HELD", "weight": 10.0, "sector": "Healthcare"}])
    grow = _grow_today(port_df, _scanner("Financials"), [], {}, _TODAY, 100_000.0, {"tone": "bull"},
                       composites={"NEW": _bundle(kind, "Financials")}, macro_events=ev)
    return find_item(grow["macro_blocked_picks"], "NEW"), grow


@pytest.mark.parametrize("kind", list(_BREAKS))
@pytest.mark.parametrize("builder,gid", [(_g07, "G-07"), (_g16, "G-16")])
def test_ledger_composite_only_when_trusted(builder, gid, kind):
    item, _ = builder(kind)
    assert item is not None and item["gate_id"] == gid
    if kind in ("trusted", "legacy"):
        assert item["composite_score"] == _TOTAL
    else:
        assert item["composite_score"] is None


def test_none_composite_g07_row_is_excluded_not_graded():
    item, grow = _g07("stale_as_of")
    rows = gate_ledger.build_suppression_rows(grow, rec_date=_TODAY, source="app", tone="bull")
    g07 = [r for r in rows if r["gate_id"] == "G-07"]
    assert g07 and g07[0]["composite_score"] is None
    called = []
    graded = enrich_and_grade(
        g07, today=date(2027, 1, 4), spy_close_by_date={},
        historical_close_fn=lambda *a: called.append(a), horizon_trading_days=30,
        composite_buy=COMPOSITE_BUY,
    )
    assert graded[0]["status"] == STATUS_EXCLUDED_NULL_COMPOSITE
    assert not called
