"""Item C (C2): bundle_loader wires valuation_target.choose_pt_target into the
Valuation pillar. Mocks every I/O boundary (same pattern as
tests/test_bundle_loader_asset_type.py)."""
import ast
import inspect
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import bundle_loader
from stock_analyzer.constants import VALUATION_PT_MIN_ANALYSTS, VALUATION_PT_MAX_TARGET_RATIO

pytestmark = pytest.mark.fast

_ROOT = Path(__file__).resolve().parent.parent


def _price_df(n=60, scale=1.0):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    closes = [(100 + i * 0.3) * scale for i in range(n)]   # last close ~117.7*scale
    return pd.DataFrame({"Close": closes, "High": [c + 1 for c in closes],
                         "Low": [c - 1 for c in closes], "Open": closes,
                         "Volume": [1_000_000.0] * n}, index=idx)


def _db(coverage, fund_cache=None, bundle_cache=None):
    def _cov_read(*a, **k):
        if isinstance(coverage, Exception):
            raise coverage
        return coverage

    class _Db:
        save_bundle_cache = staticmethod(lambda *a, **k: None)
        load_bundle_cache = staticmethod(lambda *a, **k: bundle_cache)
        save_fundamentals_cache = staticmethod(lambda *a, **k: None)
        load_fundamentals_cache = staticmethod(lambda *a, **k: fund_cache)
        load_analyst_coverage_or_none = staticmethod(_cov_read)
        save_sector_cache = staticmethod(lambda *a, **k: None)
        load_sector_cache = staticmethod(lambda *a, **k: "")
        save_etf_lookthrough_cache = staticmethod(lambda *a, **k: None)
        load_etf_lookthrough_cache = staticmethod(lambda *a, **k: None)
        load_sentiment_llm_cache = staticmethod(lambda *a, **k: None)
        save_sentiment_llm_cache = staticmethod(lambda *a, **k: None)
    return _Db


def _cov(*firms_targets, rating="Strong Buy"):
    rows = [{"ticker": "TESTX", "article_date": "2026-10-01",
             "analysts": [{"firm": f, "price_target": t, "rating": rating,
                           "article_date": "2026-10-01"}
                          for f, t in firms_targets]}]
    return pd.DataFrame(rows)


def _load(monkeypatch, coverage, info, fund_cache=None, scale=1.0):
    monkeypatch.setattr(bundle_loader, "db", _db(coverage, fund_cache))
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle", lambda t, p: {
        "history": _price_df(scale=scale), "info": info, "news": [],
        "earnings": {}, "revisions": {}, "_source": "yahoo_finance"})
    return bundle_loader.load_bundle("TESTX")


def test_provider_first_even_when_saved_research_exists(monkeypatch):
    out = _load(monkeypatch, _cov(("A", 140.0), ("B", 140.0), ("C", 140.0)),
                {"sector": "", "targetMeanPrice": 224.81, "numberOfAnalystOpinions": 31})
    c = out["val_pt_choice"]
    assert c["source"] == "provider" and c["target"] == 224.81
    assert c["provider_name"] == "Yahoo Finance"
    assert out["val_analyst_data"]["avg_pt"] == 224.81
    assert "224.81" in out["upside"] and "140" not in out["upside"]
    assert out["upside_tag"] == "Yahoo Finance mean, 31 analysts"
    assert "Yahoo Finance mean, 31 analysts" in out["val_signals"]["PT Upside"]
    # rating leg stays on the owner's saved research (D-1)
    assert "Strong Buy" in out["val_signals"]["Analyst Consensus"]
    assert "your saved research, 3 firms" in out["val_signals"]["Analyst Consensus"]


def test_thin_provider_falls_back_to_saved_with_enough_firms(monkeypatch):
    out = _load(monkeypatch, _cov(("A", 140.0), ("B", 150.0), ("C", 160.0)),
                {"sector": "", "targetMeanPrice": 224.81, "numberOfAnalystOpinions": 1})
    c = out["val_pt_choice"]
    assert c["source"] == "saved" and c["target"] == 150.0 and c["n"] == 3
    assert c["provider_reject"] == "too_few_analysts"
    assert "150.00" in out["upside"]          # chosen target, not the raw provider's
    assert "224.81" not in out["upside"]


def test_coverage_read_failure_and_unusable_provider_scores_no_pt(monkeypatch):
    out = _load(monkeypatch, None,
                {"sector": "", "targetMeanPrice": 224.81, "numberOfAnalystOpinions": None})
    c = out["val_pt_choice"]
    assert c["source"] is None and c["target"] is None
    assert c["saved_reject"] == "read_failed"
    assert out["val_signals"]["PT Upside"].startswith("Not scored")   # visible, 0 pts
    assert out["upside"] is None and out["upside_tag"] is None


def test_val_pt_choice_present_when_nothing_at_all(monkeypatch):
    out = _load(monkeypatch, pd.DataFrame(), {"sector": ""})
    assert out["val_pt_choice"]["saved_reject"] == "none"
    assert out["val_pt_choice"]["target"] is None
    assert out["upside"] is None


def test_implausible_provider_target_does_not_sneak_back_in(monkeypatch):
    # 4x the price: chooser rejects it; valuation must not re-read it from financials
    out = _load(monkeypatch, pd.DataFrame(),
                {"sector": "", "targetMeanPrice": 600.0, "numberOfAnalystOpinions": 20})
    assert out["val_pt_choice"]["provider_reject"] == "implausible"
    assert out["val_signals"]["PT Upside"].startswith("Not scored")
    assert "224" not in out["val_signals"]["PT Upside"] and "600" not in out["val_signals"]["PT Upside"]


def test_bundle_loader_passes_constants_and_calls_chooser_once():
    src = (_ROOT / "stock_analyzer" / "bundle_loader.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "choose_pt_target"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert kw["min_analysts"].id == "VALUATION_PT_MIN_ANALYSTS"
    assert kw["max_ratio"].id == "VALUATION_PT_MAX_TARGET_RATIO"
    assert VALUATION_PT_MIN_ANALYSTS == 3 and VALUATION_PT_MAX_TARGET_RATIO == 3.0


def test_valuation_py_no_longer_reads_analyst_target():
    src = (_ROOT / "stock_analyzer" / "valuation.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    consts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
              and isinstance(n.value, str)]
    assert "analyst_target" not in consts


def test_valuation_target_is_a_gate_file():
    from importlib.util import module_from_spec, spec_from_file_location
    p = _ROOT / ".claude" / "hooks" / "pre_tool_checks.py"
    assert '"stock_analyzer/valuation_target.py"' in p.read_text(encoding="utf-8")


# ── NB-1: as_of dates ────────────────────────────────────────────────────────
def test_fundamentals_cache_path_uses_cache_row_date_and_cached_flag(monkeypatch):
    from datetime import timedelta
    from stock_analyzer.market_time import ET
    from datetime import datetime, timezone
    fetched = datetime.now(timezone.utc) - timedelta(days=3)
    fc = {"fetched_at": fetched.isoformat(),
          "financials": {"revenue_growth": 0.1, "analyst_target": 224.81,
                         "num_analyst_opinions": 31}}
    out = _load(monkeypatch, pd.DataFrame(), {"sector": ""}, fund_cache=fc)
    c = out["val_pt_choice"]
    assert c["source"] == "provider"
    assert c["cached"] is True
    assert c["as_of"] == fetched.astimezone(ET).date().isoformat()


def test_stale_bundle_date_is_the_bundle_cache_date(monkeypatch):
    # db.load_bundle_cache's real contract: fetched_at is a bare DATE string
    # (`fetched.date().isoformat()`), not a timestamp. It must pass through
    # unchanged — reading it as UTC midnight would move it a day early. A date
    # that isn't today, so a branch that returns now_et() fails this test.
    info = {"sector": "", "targetMeanPrice": 224.81, "numberOfAnalystOpinions": 31}
    monkeypatch.setattr(bundle_loader, "db", _db(
        pd.DataFrame(), bundle_cache={
            "bundle": {"history": _price_df(), "info": info, "news": [],
                       "earnings": {}, "revisions": {}},
            "fetched_at": "2026-10-15"}))

    def _boom(t, p):
        raise RuntimeError("provider down")
    monkeypatch.setattr(bundle_loader, "fetch_ticker_bundle", _boom)
    out = bundle_loader.load_bundle("TESTX")
    assert out["val_pt_choice"]["as_of"] == "2026-10-15"
    assert out["val_pt_choice"]["cached"] is True


def test_et_date_str_helper():
    f = bundle_loader._et_date_str
    assert f("2026-10-22T01:30:00Z") == "2026-10-21"
    assert f("2026-10-22T01:30:00+00:00") == "2026-10-21"
    assert f("2026-10-22T02:00:00") == "2026-10-21"     # naive = UTC (as ET it'd stay 10-22)
    assert f("2026-10-21") == "2026-10-21"              # a bare date passes through
    assert f("2026-13-45") is None
    assert f(None) is None and f("garbage") is None


# ── NB-2: a not-scored PT leg says why ───────────────────────────────────────
def test_not_scored_pt_reason_is_published_dollar_free_and_shown(monkeypatch):
    out = _load(monkeypatch, None,
                {"sector": "", "targetMeanPrice": 224.81, "numberOfAnalystOpinions": None})
    why = out["val_analyst_data"]["pt_not_scored"]
    assert why.startswith("Not scored") and "$" not in why
    assert out["val_signals"]["PT Upside"] == why


def test_scored_pt_has_no_not_scored_reason(monkeypatch):
    out = _load(monkeypatch, pd.DataFrame(),
                {"sector": "", "targetMeanPrice": 224.81, "numberOfAnalystOpinions": 31})
    assert "pt_not_scored" not in out["val_analyst_data"]


# ── NB-4: coverage read failure ──────────────────────────────────────────────
@pytest.mark.parametrize("which", ["read", "derive"])
def test_coverage_exception_reads_as_read_failed(monkeypatch, which):
    info = {"sector": "", "targetMeanPrice": 224.81, "numberOfAnalystOpinions": None}
    if which == "read":
        cov = RuntimeError("boom")
    else:
        cov = _cov(("A", 140.0), ("B", 150.0), ("C", 160.0))
        from stock_analyzer import analyst_intel

        def _bad(_a):
            raise ValueError("bad")
        monkeypatch.setattr(analyst_intel, "derive_consensus", _bad)
    out = _load(monkeypatch, cov, info)
    assert out["val_pt_choice"]["source"] is None
    assert out["val_pt_choice"]["saved_reject"] == "read_failed"


def test_coverage_exception_is_logged_not_recorded_in_api_health(monkeypatch, caplog):
    # Logged so a code bug is observable — but never sent to api_health, which
    # has no Data Health / System Trust row for it and would turn the health
    # icon red with nothing explaining why.
    from stock_analyzer import api_health
    calls = []
    monkeypatch.setattr(api_health, "record", lambda *a, **k: calls.append(a))
    with caplog.at_level("WARNING", logger="stock_analyzer.bundle_loader"):
        _load(monkeypatch, RuntimeError("boom"),
              {"sector": "", "targetMeanPrice": 224.81, "numberOfAnalystOpinions": None})
    assert any("RuntimeError" in r.getMessage() and "saved-research" in r.getMessage()
               for r in caplog.records)
    assert not any(c and c[0] == "analyst_coverage" for c in calls)


# ── NB-9: numeric strings ────────────────────────────────────────────────────
def test_string_saved_targets_are_coerced(monkeypatch):
    out = _load(monkeypatch, _cov(("A", "685"), ("B", "$700"), ("C", "1,200.50")),
                {"sector": "", "targetMeanPrice": 224.81, "numberOfAnalystOpinions": None},
                scale=4.0)   # price ~471 so the 3x plausibility guard passes
    c = out["val_pt_choice"]
    assert c["source"] == "saved" and c["n"] == 3
    assert c["target"] == pytest.approx((685 + 700 + 1200.5) / 3)


def test_coerce_pt_str():
    f = bundle_loader._coerce_pt_str
    assert f("685") == 685.0 and f("$700") == 700.0 and f(" 1,200.50 ") == 1200.5
    assert f(12.5) == 12.5 and f(None) is None
    assert f("abc") == "abc" and f("nan") == "nan" and f("inf") == "inf"
