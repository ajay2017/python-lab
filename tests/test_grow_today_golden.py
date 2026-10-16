"""GOLDEN MASTER of daily_briefing._grow_today (chunk 1 of docs/plans/
shared-entry-check.md, 2026-10-16).

Chunk 2 lifts Grow Today's stale-data / macro (G-07) / news / tone checks into
a shared module. This file freezes the FULL return of `_grow_today` for a
deterministic scenario matrix at the commit it was generated on, so that
refactor can prove ZERO behaviour change: any difference in any key of any
scenario fails with a per-scenario, per-key diff.

Regenerate ONLY on a deliberate behaviour change (chunk 7's N2 news check will
change the `n2_*` scenarios on purpose):

    GOLDEN_REGEN=1 python -m pytest tests/test_grow_today_golden.py -q

Notes
-----
* Clock: the only wall-clock read reachable from `_grow_today` is the macro
  map's same-day release check, read as `_dt.now(ET)` inside daily_briefing's
  macro-map builder and handed to `entry_eligibility.macro_block_map` as
  `now_et`; it is frozen by patching `daily_briefing._dt` per scenario. `today` is
  always passed explicitly (reference_shelf/macro_calendar read it from there).
* Tone: `_grow_today` takes `market_context["tone"]` as an INPUT -- the
  bull/bear/flat classification from sp500_pct lives in app.py / headless
  (chunk 2's `classify_tone`). The boundary scenarios here therefore derive
  the tone with a test-local copy of that rule, built from the constants, and
  freeze what Grow Today does with the result; they cannot prove the
  classifier itself.
* Serialisation drops NOTHING: no output field of `_grow_today` is
  wall-clock-derived once the clock is frozen and `today` is explicit
  (`composite_fetched_at` is copied from the input bundle, which is None here).
* The `n2_*` scenarios document TODAY's add-lane behaviour (no news check on
  adds); chunk 7 changes them deliberately.
"""
import json
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from stock_analyzer import daily_briefing
from stock_analyzer.constants import (
    ADD_WINNER_MIN_GAP_PCT,
    ATR_STOP_MULT,
    COMPOSITE_BUY,
    EARNINGS_IMMINENT_DAYS,
    GROW_TODAY_MAX_FUND_AGE_DAYS,
    MACRO_IMMINENT_DAYS,
    MARKET_TONE_BEAR_PCT,
    MARKET_TONE_BULL_PCT,
    NEWS_SENTIMENT_NEGATIVE,
)
from stock_analyzer.daily_briefing import _grow_today
from stock_analyzer.macro_calendar import HIGH, MEDIUM
from tests.conftest import make_port_df
from tests.test_daily_briefing import _etf_candidate, _scanner_df

pytestmark = pytest.mark.fast

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "grow_today_golden.json"
_TODAY = date(2026, 10, 15)

# Frozen wall-clock times (ET) for the macro same-day release check.
_CLOCK_EARLY = (7, 0)     # before an 08:30 release / a 14:00 FOMC
_CLOCK_RELEASE = (8, 30)  # exactly at the 08:30 release (<= resolves)
_CLOCK_LATE = (15, 0)     # after both


class _FixedDT(datetime):
    """datetime whose now(tz) is pinned; every other classmethod is real."""
    _h, _m = _CLOCK_EARLY

    @classmethod
    def now(cls, tz=None):
        naive = datetime(2026, 10, 15, cls._h, cls._m)
        return tz.localize(naive) if tz is not None else naive


def _iso(d_off):
    return (_TODAY + timedelta(days=d_off)).isoformat()


def _tone_for(pct):
    """Test-local copy of the app/headless tone rule (see module note)."""
    return ("bull" if pct >= MARKET_TONE_BULL_PCT
            else "bear" if pct <= MARKET_TONE_BEAR_PCT else "flat")


def _bundle(total=80.0, price=100.0, atr=3.0, **extra):
    b = {
        "total": total, "rec": {"label": "Buy"},
        "fundamentals_available": True,
        "stop": round(price - ATR_STOP_MULT * atr, 2), "atr": atr,
        "entry_lo": 99.0, "entry_hi": 101.5,
    }
    b.update(extra)
    return b


def _ev(category="Inflation", d_off=3, impact=HIGH, **kw):
    return {"event": f"{category} print", "category": category,
            "impact": impact, "date": _iso(d_off), **kw}


def _news(ticker, compound, n=1):
    return [{"ticker": ticker, "title": f"{ticker} headline {i}", "compound": compound,
             "label": "x"} for i in range(n)]


_PCT = {"bull": 0.8, "flat": 0.0, "bear": -0.8}


def _scn(tone_pct, **o):
    """One scenario's inputs. Candidates: ZNEWX (Energy -- the macro-affected,
    overridden one) and ZCTRL (Healthcare -- unaffected control that must be
    unchanged by every ZNEWX override)."""
    s = dict(
        tone=_tone_for(tone_pct), pct=tone_pct,
        rows=[{"ticker": "ZNEWX", "score": 90.0, "sector": "Energy", "price": 100.0},
              {"ticker": "ZCTRL", "score": 89.0, "sector": "Healthcare", "price": 100.0}],
        composites={"ZNEWX": _bundle(80.0), "ZCTRL": _bundle(80.0)},
        news=[], earnings=None, held_data={}, macro=None, clock=_CLOCK_EARLY,
        port_rows=[{"ticker": "ZHELD", "weight": 5.0, "sector": "Tech"}],
        act_today=None, review_list=None, movers=None, etf=None,
    )
    s.update(o)
    return s


def _bx(**extra):
    return {"ZNEWX": _bundle(80.0, **extra), "ZCTRL": _bundle(80.0)}


# level name -> overrides (applied on top of _scn's ZNEWX/ZCTRL base)
_LEVELS = {
    # macro (None = unchecked, [] = checked/none)
    "macro_none": dict(macro=None),
    "macro_empty": dict(macro=[]),
    "macro_3d": dict(macro=[_ev(d_off=MACRO_IMMINENT_DAYS)]),
    "macro_4d": dict(macro=[_ev(d_off=MACRO_IMMINENT_DAYS + 1)]),
    "macro_3d_medium": dict(macro=[_ev(d_off=MACRO_IMMINENT_DAYS, impact=MEDIUM)]),
    "macro_past_1d": dict(macro=[_ev(d_off=-1)]),
    "macro_all_sectors_2d": dict(macro=[_ev("Fed Policy", d_off=2)]),
    "macro_all_sectors_4d": dict(macro=[_ev("Fed Policy", d_off=MACRO_IMMINENT_DAYS + 1)]),
    "macro_day0_released_flag": dict(macro=[_ev(d_off=0, released=True)]),
    "macro_day0_no_time_unreleased": dict(macro=[_ev(d_off=0)]),
    "macro_day0_before_time": dict(macro=[_ev(d_off=0, time_et="08:30")], clock=_CLOCK_EARLY),
    "macro_day0_at_time": dict(macro=[_ev(d_off=0, time_et="08:30")], clock=_CLOCK_RELEASE),
    "macro_day0_after_time": dict(macro=[_ev(d_off=0, time_et="08:30")], clock=_CLOCK_LATE),
    "macro_day0_bad_time": dict(macro=[_ev(d_off=0, time_et="garbage")], clock=_CLOCK_LATE),
    "macro_fomc_day0_before": dict(macro=[_ev("Fed Policy", d_off=0, time_et="14:00")],
                                   clock=_CLOCK_EARLY),
    "macro_fomc_day0_after": dict(macro=[_ev("Fed Policy", d_off=0, time_et="14:00")],
                                  clock=_CLOCK_LATE),
    "macro_fomc_day0_released_flag": dict(macro=[_ev("Fed Policy", d_off=0, released=True,
                                                     time_et="14:00")], clock=_CLOCK_LATE),
    # data trust (ZNEWX bundle)
    "trust_keys_absent": dict(),
    "trust_fresh": dict(composites=_bx(stale_as_of=None, fund_cache_age_days=None)),
    "trust_stale_as_of": dict(composites=_bx(stale_as_of="2026-10-10")),
    "trust_fund_age_at_max": dict(composites=_bx(fund_cache_age_days=GROW_TODAY_MAX_FUND_AGE_DAYS)),
    "trust_fund_age_max_plus1": dict(
        composites=_bx(fund_cache_age_days=GROW_TODAY_MAX_FUND_AGE_DAYS + 1)),
    "trust_fundamentals_unavailable": dict(composites=_bx(fundamentals_available=False)),
    # candidate news (compound supplied directly -- no scorer on this path)
    "news_at_threshold": dict(news=_news("ZNEWX", NEWS_SENTIMENT_NEGATIVE)),
    "news_just_above": dict(news=_news("ZNEWX", NEWS_SENTIMENT_NEGATIVE + 0.001)),
    "news_none": dict(news=[]),
    "news_single_strong_neg": dict(news=_news("ZNEWX", -0.40)),
    # earnings for a NEW-pick candidate
    "earn_0d": dict(earnings={"ZNEWX": _iso(0)}),
    "earn_at_imminent": dict(earnings={"ZNEWX": _iso(EARNINGS_IMMINENT_DAYS)}),
    "earn_imminent_plus1": dict(earnings={"ZNEWX": _iso(EARNINGS_IMMINENT_DAYS + 1)}),
    "earn_unknown": dict(earnings=None),
    "earn_in_held_data_7d": dict(held_data={"ZNEWX": {"earnings": _iso(EARNINGS_IMMINENT_DAYS)}}),
}


def _held_rows():
    """Held add-to-winner fixtures (distinct sectors, so no sector breach)."""
    base = dict(weight=5.0, score=COMPOSITE_BUY + 15, signal="Strong Buy",
                gap_to_stop=ADD_WINNER_MIN_GAP_PCT + 2, price=100.0)
    names = [("ZADDOK", "Tech"), ("N2ACT", "Consumer"), ("N2REV", "Utilities"),
             ("N2NEG40", "Industrials"), ("N2EARN1", "Materials"),
             ("N2EARN1REV", "Real Estate"), ("ZEARN0", "Telecom"),
             ("ZEARN7", "Staples"), ("ZEARN8", "Retail"),
             ("ZNEWSMILD", "Insurance")]
    return [{**base, "ticker": t, "sector": sec} for t, sec in names]


def _adds_scn(pct, **o):
    news = (_news("N2NEG40", -0.40)                      # single, no card trips
            + _news("N2ACT", -0.40, n=2)                 # >=2 tier-1 headlines (critical)
            + _news("ZNEWSMILD", -0.10))                 # warn-range headline
    e = {"N2EARN1": _iso(1), "N2EARN1REV": _iso(1), "ZEARN0": _iso(0),
         f"ZEARN{EARNINGS_IMMINENT_DAYS}": _iso(EARNINGS_IMMINENT_DAYS),
         f"ZEARN{EARNINGS_IMMINENT_DAYS + 1}": _iso(EARNINGS_IMMINENT_DAYS + 1)}
    # ZEARN7/ZEARN8 names are literal; EARNINGS_IMMINENT_DAYS == 7 today. If the
    # constant moves, the keys above no longer match the held names -- fail loud.
    assert EARNINGS_IMMINENT_DAYS == 7, "rename ZEARN7/ZEARN8 held tickers"
    s = _scn(pct, rows=[], composites={}, port_rows=_held_rows(), news=news,
             earnings=e,
             act_today=[{"ticker": "N2ACT", "action": "REDUCE", "_source": "act"}],
             review_list=[{"ticker": "N2REV", "action": "WATCH", "_source": "review"},
                          {"ticker": "N2EARN1REV", "action": "WATCH", "_source": "review"}],
             held_data={"N2EARN1": {"earnings": _iso(1)}})
    s.update(o)
    return s


def _multi_scn(pct, **o):
    rows = [
        {"ticker": "ZENRG", "score": 95.0, "sector": "Energy"},        # macro-affected
        {"ticker": "ZHLTH", "score": 94.0, "sector": "Healthcare"},    # clean
        {"ticker": "ZFIN", "score": 93.0, "sector": "Financials"},     # macro-affected, stale
        {"ticker": "ZUTIL", "score": 92.0, "sector": "Utilities"},     # negative news
        {"ticker": "ZMATL", "score": 91.0, "sector": "Materials"},     # earnings in 3d
        {"ticker": "ZREAL", "score": 90.0, "sector": "Real Estate"},   # fund age +1
        {"ticker": "ZHLTH2", "score": 89.0, "sector": "Healthcare"},   # sector slot overflow
    ]
    comps = {r["ticker"]: _bundle(80.0) for r in rows}
    comps["ZFIN"] = _bundle(80.0, stale_as_of="2026-10-10")
    comps["ZREAL"] = _bundle(80.0, fund_cache_age_days=GROW_TODAY_MAX_FUND_AGE_DAYS + 1)
    movers = [{"ticker": "ZMOVE", "price": 100.0, "sector": "Energy", "score": 50.0,
               "composite_score": 85.0, "day_change": 6.0},
              {"ticker": "ZMOVE2", "price": 100.0, "sector": "Healthcare", "score": 50.0,
               "composite_score": 84.0, "day_change": 6.0}]
    comps["ZMOVE"] = _bundle(80.0)
    comps["ZMOVE2"] = _bundle(80.0)
    s = _scn(pct, rows=rows, composites=comps, movers=movers,
             news=_news("ZUTIL", NEWS_SENTIMENT_NEGATIVE - 0.2, n=2),
             earnings={"ZMATL": _iso(3)}, macro=None,
             port_rows=[{"ticker": "ZHELD", "weight": 5.0, "sector": "Tech"}] + [
                 {**r, "weight": 5.0} for r in _held_rows()[:2]])
    s.update(o)
    return s


def _build_scenarios() -> dict:
    sc: dict = {}
    for tone, pct in _PCT.items():
        for lvl, ov in _LEVELS.items():
            sc[f"{tone}__{lvl}"] = _scn(pct, **ov)
        sc[f"{tone}__adds_matrix"] = _adds_scn(pct)
        sc[f"{tone}__adds_matrix_macro3d_energy"] = _adds_scn(
            pct, macro=[_ev(d_off=MACRO_IMMINENT_DAYS)])
        sc[f"{tone}__multi_mixed"] = _multi_scn(pct)
        # Inflation HIGH 3d blocks Energy/Financials/Utilities/Materials/Real Estate
        sc[f"{tone}__multi_mixed_macro3d"] = _multi_scn(
            pct, macro=[_ev(d_off=MACRO_IMMINENT_DAYS)])
        sc[f"{tone}__etf_clean"] = _scn(pct, rows=[], composites={}, etf=[_etf_candidate()])
        sc[f"{tone}__etf_macro_all"] = _scn(
            pct, rows=[], composites={}, etf=[_etf_candidate()],
            macro=[_ev("Fed Policy", d_off=2)])
        sc[f"{tone}__etf_negative_news"] = _scn(
            pct, rows=[], composites={}, etf=[_etf_candidate()],
            news=_news("SPY", NEWS_SENTIMENT_NEGATIVE - 0.3, n=3))
    # n2_ prefix: scenarios chunk 7 changes DELIBERATELY (adds + news/earnings)
    for k in [k for k in sc if "__adds_matrix" in k and "macro" not in k]:
        sc["n2_" + k] = sc.pop(k)
    # tone boundary pairs: otherwise-identical, only sp500 differs
    for lab, pct in (("bear_edge", MARKET_TONE_BEAR_PCT),
                     ("bear_edge_plus", MARKET_TONE_BEAR_PCT + 0.01),
                     ("bull_edge", MARKET_TONE_BULL_PCT),
                     ("bull_edge_minus", MARKET_TONE_BULL_PCT - 0.01)):
        sc[f"boundary__{lab}__clean"] = _scn(pct)
        sc[f"boundary__{lab}__multi_mixed"] = _multi_scn(pct)
        sc[f"boundary__{lab}__adds"] = _adds_scn(pct)
    return sc


# ── execution + serialisation ───────────────────────────────────────────────

def _run(s: dict) -> dict:
    port_df = make_port_df(s["port_rows"]) if s["port_rows"] else None
    scanner = _scanner_df(s["rows"]) if s["rows"] else None
    ctx = {"tone": s["tone"], "sp500_pct": s["pct"], "nasdaq_pct": s["pct"] * 1.2,
           "leading_sectors": []}

    class _Clk(_FixedDT):
        _h, _m = s["clock"]

    with mock.patch.object(daily_briefing, "_dt", _Clk):
        return _grow_today(
            port_df, scanner, s["news"], s["held_data"], _TODAY, 100_000.0, ctx,
            act_today=s["act_today"], review_list=s["review_list"],
            composites=s["composites"], earnings_lookup=s["earnings"],
            macro_events=s["macro"], movers=s["movers"], etf_candidates=s["etf"])


def _ser(x):
    """Deterministic JSON-able form: sorted keys, 6-dp floats, sets sorted."""
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        if math.isnan(x):
            return "NaN"
        if math.isinf(x):
            return "Inf" if x > 0 else "-Inf"
        return round(x, 6)
    if hasattr(x, "to_dict") and hasattr(x, "columns"):      # DataFrame
        return _ser(x.to_dict("records"))
    if hasattr(x, "isoformat"):
        return x.isoformat()
    if isinstance(x, dict):
        return {str(k): _ser(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (set, frozenset)):
        return sorted((_ser(v) for v in x), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(x, (list, tuple)):
        return [_ser(v) for v in x]
    if hasattr(x, "item"):                                   # numpy scalar
        return _ser(x.item())
    return repr(x)


_SCENARIOS = _build_scenarios()
_CACHE: dict = {}


def _result(name: str) -> dict:
    if name not in _CACHE:
        _CACHE[name] = _ser(_run(_SCENARIOS[name]))
    return _CACHE[name]


def _diff(exp: dict, got: dict) -> list[str]:
    out = []
    for k in sorted(set(exp) | set(got)):
        if k not in got:
            out.append(f"  key REMOVED: {k}")
        elif k not in exp:
            out.append(f"  key ADDED: {k} = {json.dumps(got[k], sort_keys=True)[:300]}")
        elif exp[k] != got[k]:
            out.append(f"  key CHANGED: {k}\n    golden: {json.dumps(exp[k], sort_keys=True)[:400]}"
                       f"\n    now:    {json.dumps(got[k], sort_keys=True)[:400]}")
    return out


def _regen_requested() -> bool:
    return os.environ.get("GOLDEN_REGEN") == "1"


def test_regenerate_fixture_when_asked():
    if not _regen_requested():
        pytest.skip("set GOLDEN_REGEN=1 to (re)write the golden fixture")
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    data = {n: _result(n) for n in sorted(_SCENARIOS)}
    FIXTURE.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _load_fixture() -> dict:
    assert FIXTURE.exists(), f"missing {FIXTURE}; run with GOLDEN_REGEN=1"
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_covers_exactly_the_scenario_set():
    if _regen_requested():
        pytest.skip("regenerating")
    assert sorted(_load_fixture()) == sorted(_SCENARIOS)


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_grow_today_matches_golden(name):
    if _regen_requested():
        pytest.skip("regenerating")
    golden = _load_fixture()
    assert name in golden, f"scenario {name} has no golden entry"
    diff = _diff(golden[name], _result(name))
    assert not diff, f"_grow_today output changed for scenario {name!r}:\n" + "\n".join(diff)


def test_serialisation_is_deterministic():
    name = "bull__multi_mixed"
    a = json.dumps(_ser(_run(_SCENARIOS[name])), sort_keys=True)
    b = json.dumps(_ser(_run(_SCENARIOS[name])), sort_keys=True)
    assert a == b


# ── non-vacuity: the matrix really exercises each branch ────────────────────

def _t(items):
    return [i["ticker"] for i in items]


def _bucket(name, key):
    return _result(name)[key]


def test_bear_tone_yields_no_picks_and_a_message():
    for lvl in _LEVELS:
        r = _result(f"bear__{lvl}")
        assert r["new_picks"] == [] and r["add_positions"] == [] and r["message"], lvl
    assert _result("bull__trust_fresh")["new_picks"], "control: bull fires"


def test_tone_boundary_pairs():
    assert _result("boundary__bear_edge__clean")["tone"] == "bear"
    assert _result("boundary__bear_edge__clean")["new_picks"] == []
    assert _result("boundary__bear_edge_plus__clean")["tone"] == "flat"
    assert _t(_result("boundary__bear_edge_plus__clean")["new_picks"]), "flat day still picks"
    assert _result("boundary__bull_edge__clean")["tone"] == "bull"
    assert _result("boundary__bull_edge_minus__clean")["tone"] == "flat"
    # bull and flat differ visibly: bull lets adds through, flat does not
    assert _result("boundary__bull_edge__adds")["add_positions"]
    assert _result("boundary__bull_edge_minus__adds")["add_positions"] == []
    assert _result("boundary__bull_edge_minus__adds")["adds_paused"]


@pytest.mark.parametrize("tone", ["bull", "flat"])
def test_macro_window_boundary_and_levels(tone):
    assert _t(_bucket(f"{tone}__macro_3d", "macro_blocked_picks")) == ["ZNEWX"]
    assert "ZNEWX" not in _t(_result(f"{tone}__macro_3d")["new_picks"])
    assert "ZCTRL" in _t(_result(f"{tone}__macro_3d")["new_picks"])        # control unaffected
    assert _bucket(f"{tone}__macro_4d", "macro_blocked_picks") == []
    assert "ZNEWX" in _t(_result(f"{tone}__macro_4d")["new_picks"])
    for lvl in ("macro_none", "macro_empty", "macro_3d_medium", "macro_past_1d"):
        assert _bucket(f"{tone}__{lvl}", "macro_blocked_picks") == [], lvl
    # __ALL__ blocks every candidate
    assert sorted(_t(_bucket(f"{tone}__macro_all_sectors_2d", "macro_blocked_picks"))) == [
        "ZCTRL", "ZNEWX"]
    assert _bucket(f"{tone}__macro_all_sectors_4d", "macro_blocked_picks") == []
    # day-0 released vs not yet released
    assert _bucket(f"{tone}__macro_day0_released_flag", "macro_blocked_picks") == []
    assert _t(_bucket(f"{tone}__macro_day0_no_time_unreleased", "macro_blocked_picks")) == ["ZNEWX"]
    assert _t(_bucket(f"{tone}__macro_day0_before_time", "macro_blocked_picks")) == ["ZNEWX"]
    assert _bucket(f"{tone}__macro_day0_at_time", "macro_blocked_picks") == []
    assert _bucket(f"{tone}__macro_day0_after_time", "macro_blocked_picks") == []
    assert _t(_bucket(f"{tone}__macro_day0_bad_time", "macro_blocked_picks")) == ["ZNEWX"]
    # FOMC (Fed Policy -> __ALL__) never lifts same day, before/after/flagged
    for lvl in ("macro_fomc_day0_before", "macro_fomc_day0_after", "macro_fomc_day0_released_flag"):
        assert len(_bucket(f"{tone}__{lvl}", "macro_blocked_picks")) == 2, lvl
    g = _bucket(f"{tone}__macro_3d", "macro_blocked_picks")[0]
    assert g["gate_id"] == "G-07" and g["gate_threshold"] == MACRO_IMMINENT_DAYS


@pytest.mark.parametrize("tone", ["bull", "flat"])
def test_stale_and_fund_age_go_to_composite_unavailable(tone):
    assert _t(_bucket(f"{tone}__trust_stale_as_of", "composite_unavailable")) == ["ZNEWX"]
    assert _bucket(f"{tone}__trust_fund_age_at_max", "composite_unavailable") == []
    assert _t(_bucket(f"{tone}__trust_fund_age_max_plus1", "composite_unavailable")) == ["ZNEWX"]
    assert _t(_bucket(f"{tone}__trust_fundamentals_unavailable", "composite_unavailable")) == ["ZNEWX"]
    for lvl in ("trust_keys_absent", "trust_fresh"):
        assert _bucket(f"{tone}__{lvl}", "composite_unavailable") == []
        assert "ZNEWX" in _t(_result(f"{tone}__{lvl}")["new_picks"])


@pytest.mark.parametrize("tone", ["bull", "flat"])
def test_news_threshold_skip_vs_just_above(tone):
    at = _result(f"{tone}__news_at_threshold")
    above = _result(f"{tone}__news_just_above")
    assert "ZNEWX" not in _t(at["new_picks"])
    # bull: skipped by the reconciled-skip bucket; flat: the flat-day verdict check
    # (verdict "mixed", not confirmed/unverified) holds it first
    assert _t(at["news_skipped" if tone == "bull" else "flat_day_held"]) == ["ZNEWX"]
    assert above["news_skipped"] == [] and "ZNEWX" in _t(above["new_picks"])
    # prove the intended compound reached the cross-reference
    xa = next(p for p in above["new_picks"] if p["ticker"] == "ZNEWX")["xref"]
    assert any("Neutral" in a and "-0.15" in a for a in xa["agreed"]), xa["agreed"]
    assert "ZNEWX" not in _t(_result(f"{tone}__news_single_strong_neg")["new_picks"])
    assert "ZNEWX" in _t(_result(f"{tone}__news_none")["new_picks"])


@pytest.mark.parametrize("tone", ["bull", "flat"])
def test_earnings_0_and_7_wait_but_8_does_not(tone):
    for lvl in ("earn_0d", "earn_at_imminent", "earn_in_held_data_7d"):
        r = _result(f"{tone}__{lvl}")
        if tone == "bull":
            assert _t(r["earnings_waiting"]) == ["ZNEWX"], lvl
        else:   # flat: the flat-day verdict check holds it first
            assert _t(r["flat_day_held"]) == ["ZNEWX"], lvl
        assert "ZNEWX" not in _t(r["new_picks"]), lvl
    for lvl in ("earn_imminent_plus1", "earn_unknown"):
        assert "ZNEWX" in _t(_result(f"{tone}__{lvl}")["new_picks"]), lvl


def test_n2_documents_todays_add_lane():
    r = _result("n2_bull__adds_matrix")
    added = _t(r["add_positions"])
    # today's inverted behaviour: a single strongly negative headline does NOT hold an add
    assert "N2NEG40" in added
    # earnings tomorrow in held_data/earnings_lookup does not reach a gate inside _grow_today
    assert "N2EARN1" in added
    assert "ZADDOK" in added and "ZNEWSMILD" in added
    # Brief-level cards (act_today / review_list) DO block, silently
    for blocked in ("N2ACT", "N2REV", "N2EARN1REV"):
        assert blocked not in added, blocked
    # earnings at/after the window do not matter to adds either way
    for t in ("ZEARN0", "ZEARN7", "ZEARN8"):
        assert t in added, t
    # flat: nothing added, would-qualify names disclosed as paused
    f = _result("n2_flat__adds_matrix")
    assert f["add_positions"] == [] and "N2NEG40" in _t(f["adds_paused"])
    assert _result("n2_bear__adds_matrix")["add_positions"] == []
    # macro never checks adds (D8): the Energy-event scenario adds the same names
    m = _result("bull__adds_matrix_macro3d_energy")
    assert "ZADDOK" in _t(m["add_positions"])


def test_flat_day_bar_and_overflow_exercised():
    r = _result("flat__multi_mixed")
    assert r["flat_day_held"] or r["new_picks"]
    b = _result("bull__multi_mixed")
    assert b["macro_blocked_picks"] == []
    assert "ZFIN" in _t(b["composite_unavailable"]) and "ZREAL" in _t(b["composite_unavailable"])
    assert _t(b["news_skipped"]) == ["ZUTIL"]
    assert "ZMATL" in _t(b["earnings_waiting"])
    assert "ZHLTH" in _t(b["new_picks"]) and "ZENRG" in _t(b["new_picks"])
    assert "ZHLTH2" in _t(b["overflow_picks"])
    m = _result("bull__multi_mixed_macro3d")
    assert {"ZENRG", "ZUTIL", "ZMATL", "ZREAL"} <= set(_t(m["macro_blocked_picks"]))
    assert "ZHLTH" in _t(m["new_picks"])


def test_etf_lane_levels():
    assert "SPY" in _t(_result("bull__etf_clean")["new_picks"])
    assert "SPY" not in _t(_result("flat__etf_clean")["new_picks"])
    assert any(e["kind"] == "macro" for e in _result("bull__etf_macro_all")["etf_screened_out"])
    assert any(e["kind"] == "skip_verdict"
               for e in _result("bull__etf_negative_news")["etf_screened_out"])
