"""Chunk 3 of docs/plans/shared-entry-check.md: Watchlist ENTRY_HELD, the
G-28/G-29 ledger rows and the registry ids."""
import json
import pathlib
from datetime import timedelta

import pytest

from stock_analyzer import entry_eligibility as ee
from stock_analyzer import gate_ledger, watchlist_advisor
from stock_analyzer.constants import (
    MACRO_IMMINENT_DAYS, MARKET_TONE_BEAR_PCT, NEWS_SENTIMENT_NEGATIVE,
)
from stock_analyzer.gate_registry import GATE_IDS
from stock_analyzer.recommendations_history import build_enter_now_rows
from stock_analyzer.watchlist_advisor import (
    build_watchlist_recommendation, sort_key_for_action,
)

pytestmark = pytest.mark.fast

TODAY_ISO = str(__import__("datetime").datetime.now(watchlist_advisor._ET).date())


def _data(**ov):
    d = {
        "total": 70.0, "rec": {"label": "Buy"}, "current_price": 100.0,
        "entry_lo": 95.0, "entry_hi": 100.0, "stop": 90.0,
        "targets": {"base": 130.0}, "earnings": None,
    }
    d.update(ov)
    return d


FLAT = {"tone": "flat", "sp500_pct": -0.1, "available": True, "source": "live", "as_of": None}
BEAR = {"tone": "bear", "sp500_pct": -0.8, "available": True, "source": "home", "as_of": "09:42"}
CLEAR_MAP = {"sectors": set(), "reasons": {}}
GOOD_NEWS = {"compound": 0.1, "n": 3, "headline": "fine"}


def _ctx(**ov):
    c = {"tone_state": FLAT, "macro_map": CLEAR_MAP, "news": GOOD_NEWS, "sector": "Technology"}
    c.update(ov)
    return c


def _rec(data=None, ctx="default", **kw):
    ctx = _ctx() if ctx == "default" else ctx
    return build_watchlist_recommendation("XYZ", data or _data(), entry_ctx=ctx, **kw)


# -- legacy path is byte-identical -------------------------------------------
_GOLDEN = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "watchlist_advisor_legacy_golden.json")
    .read_text(encoding="utf-8"))
FROZEN_TODAY = __import__("datetime").date(2026, 10, 16)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(watchlist_advisor, "_today_et", lambda: FROZEN_TODAY)


def _legacy_fixtures():
    soon = str(FROZEN_TODAY + timedelta(days=3))
    return [
        ("enter_now", _data(), None),
        ("soft", _data(), {"sector_weight_pct": 27.0, "sector_of_ticker": "Technology"}),
        ("hard_sector", _data(), {"sector_weight_pct": 40.0, "sector_of_ticker": "Technology"}),
        ("hard_beta", _data(risk_metrics={"beta": 2.5}), {"portfolio_beta": 2.0, "sector_weight_pct": 1.0}),
        ("sector_unknown", _data(), {"sector_classified": False}),
        ("rr", _data(targets={"base": 101.0}), None),
        ("remove", _data(total=30.0), None),
        ("earnings", _data(total=60.0, earnings=soon), None),
        ("near", _data(current_price=104.0), None),
        ("wait_entry", _data(current_price=130.0), None),
        ("catalyst", _data(total=50.0), None),
        ("data_unavail", _data(fundamentals_available=False), None),
    ]


@pytest.mark.parametrize("name,data,pctx", _legacy_fixtures(), ids=lambda v: v if isinstance(v, str) else "")
def test_entry_ctx_none_is_byte_identical(name, data, pctx, frozen_clock):
    a = build_watchlist_recommendation("XYZ", data, portfolio_ctx=pctx)
    b = build_watchlist_recommendation("XYZ", data, portfolio_ctx=pctx, entry_ctx=None)
    assert a == b
    # REAL golden (chunk-3 review item 1): repr frozen from the pre-entry_ctx
    # advisor (git 400b01c), so this no longer compares new code with itself.
    # C3: the public wrapper adds one key (rr_target_note), appended last; the
    # frozen golden predates it, so compare the card without that key.
    assert a["rr_target_note"] is None and b["rr_target_note"] is None
    a_core = {k: v for k, v in a.items() if k != "rr_target_note"}
    b_core = {k: v for k, v in b.items() if k != "rr_target_note"}
    assert repr(a_core) == _GOLDEN[name]
    assert repr(b_core) == _GOLDEN[name]
    # no entry-check key leaks onto a legacy card
    assert not ({"hold_kind", "holds", "hold_reason", "clears_when", "unchecked",
                 "entry_unchecked"} & set(a))


def test_non_enter_now_branches_untouched_by_a_holding_ctx(frozen_clock):
    """D9: only the ENTER_NOW branch consults the check."""
    ctx = _ctx(tone_state=BEAR)
    for name, data, pctx in _legacy_fixtures():
        legacy = build_watchlist_recommendation("XYZ", data, portfolio_ctx=pctx)
        with_ctx = build_watchlist_recommendation("XYZ", data, portfolio_ctx=pctx, entry_ctx=ctx)
        if legacy["action"] != "ENTER_NOW":
            assert with_ctx == legacy, name


# -- holds ---------------------------------------------------------------------
def test_eligible_stays_enter_now_with_entry_unchecked_list():
    r = _rec()
    assert r["action"] == "ENTER_NOW"
    assert r["entry_unchecked"] == []


def test_bear_tone_holds_with_g29_kind_and_s_and_p_value():
    r = _rec(ctx=_ctx(tone_state=BEAR))
    assert r["action"] == "ENTRY_HELD" and r["hold_kind"] == "tone"
    assert r["suppression_kind"] == "entry_tone"
    assert r["gate_value"] == -0.8 and r["gate_threshold"] == MARKET_TONE_BEAR_PCT
    assert r["hold_reason"] and r["clears_when"]
    assert r["priority"] == "MONITOR"


def test_macro_in_sector_holds_with_g28_kind():
    mm = {"sectors": {"Technology"}, "reasons": {"Technology": "CPI (2026-10-19) — 3d away"}}
    r = _rec(ctx=_ctx(macro_map=mm))
    assert r["action"] == "ENTRY_HELD" and r["hold_kind"] == "macro"
    assert r["suppression_kind"] == "entry_macro"
    assert r["gate_threshold"] == MACRO_IMMINENT_DAYS
    assert "CPI" in r["hold_reason"]


def test_other_sector_macro_does_not_hold():
    mm = {"sectors": {"Energy"}, "reasons": {"Energy": "x"}}
    assert _rec(ctx=_ctx(macro_map=mm))["action"] == "ENTER_NOW"


def test_negative_news_holds_without_a_ledger_kind():
    news = {"compound": NEWS_SENTIMENT_NEGATIVE - 0.05, "n": 4, "headline": "bad"}
    r = _rec(ctx=_ctx(news=news))
    assert (r["action"], r["hold_kind"]) == ("ENTRY_HELD", "news")
    assert r["suppression_kind"] is None


def test_stale_bundle_holds_without_a_ledger_kind():
    r = _rec(data=_data(stale_as_of="2026-10-10"))
    assert (r["action"], r["hold_kind"]) == ("ENTRY_HELD", "stale")
    assert r["suppression_kind"] is None


def test_earnings_hold_without_a_ledger_kind():
    # Score >= 55 with earnings inside the window is HOLD_OFF_EARNINGS before the
    # ENTER_NOW branch is ever reached, so the entry check's earnings leg is
    # exercised directly through entry_check, not through this advisor.
    soon = str(__import__("datetime").date.today() + timedelta(days=3))
    r = build_watchlist_recommendation("XYZ", _data(earnings=soon), entry_ctx=_ctx())
    assert r["action"] == "HOLD_OFF_EARNINGS"


def test_multiple_holds_binding_is_precedence_first_all_listed():
    news = {"compound": -0.5, "n": 2, "headline": "bad"}
    mm = {"sectors": {"Technology"}, "reasons": {"Technology": "CPI"}}
    r = _rec(data=_data(stale_as_of="2026-10-10"),
             ctx=_ctx(tone_state=BEAR, macro_map=mm, news=news))
    assert r["hold_kind"] == "stale"
    assert [h["check"] for h in r["holds"]] == ["stale", "tone", "macro", "news"]
    assert r["suppression_kind"] is None   # binding is stale: no ledger kind
    r = _rec(ctx=_ctx(tone_state=BEAR, macro_map=mm, news=news))
    assert r["hold_kind"] == "tone" and r["suppression_kind"] == "entry_tone"
    assert [h["check"] for h in r["holds"]] == ["tone", "macro", "news"]


def test_fit_gate_downgrade_wins_over_an_entry_hold():
    pctx = {"sector_weight_pct": 40.0, "sector_of_ticker": "Technology"}
    r = build_watchlist_recommendation(
        "XYZ", _data(), portfolio_ctx=pctx, entry_ctx=_ctx(tone_state=BEAR))
    legacy = build_watchlist_recommendation("XYZ", _data(), portfolio_ctx=pctx)
    assert r["action"] == "NEAR_ENTRY" and r == legacy
    assert r["suppression_kind"] == "sector"


def test_tone_unavailable_fails_open_with_a_visible_note():
    r = _rec(ctx=_ctx(tone_state={"tone": None, "sp500_pct": None, "available": False,
                                  "source": None, "as_of": None}))
    assert r["action"] == "ENTER_NOW"
    assert [u["check"] for u in r["entry_unchecked"]] == ["tone"]


def test_missing_news_and_macro_are_noted_not_silent():
    r = _rec(ctx=_ctx(news=None, macro_map=None))
    assert r["action"] == "ENTER_NOW"
    assert {u["check"] for u in r["entry_unchecked"]} == {"macro", "news"}


def test_crash_fails_closed_check_failed_no_ledger_kind(monkeypatch):
    def boom(**kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(ee, "entry_check", boom)
    r = _rec()
    assert r["action"] == "ENTRY_HELD" and r["hold_kind"] == "check_failed"
    assert r["hold_reason"] == "entry check couldn't run"
    assert r["suppression_kind"] is None
    assert r["holds"][0]["check"] == "check_failed"


def test_raising_check_function_via_checks_param_fails_closed(monkeypatch):
    def bad(ctx):
        raise ValueError("x")
    monkeypatch.setattr(ee, "DEFAULT_CHECKS", (bad,))
    # entry_check reads DEFAULT_CHECKS at call time through the module global
    r = _rec()
    assert (r["action"], r["hold_kind"]) == ("ENTRY_HELD", "check_failed")


# -- card shape ----------------------------------------------------------------
_SIZING_KEYS = {"ps", "shares", "size", "position_size", "dollars", "alloc", "allocation",
                "suggested_shares", "budget", "rec_budget_state"}


def test_entry_held_card_carries_enter_now_fields_and_no_sizing():
    held = _rec(ctx=_ctx(tone_state=BEAR))
    ready = _rec()
    for k in ("ticker", "score", "signal", "price", "entry_lo", "entry_hi", "stop", "rr",
              "earn_days", "readiness_pct", "title", "summary", "detail"):
        assert k in held
        if k not in ("title", "summary", "detail", "readiness_pct"):
            assert held[k] == ready[k]
    assert not (_SIZING_KEYS & set(held))
    assert held["readiness_pct"] < ready["readiness_pct"]
    assert set(held) - set(ready) == {"hold_kind", "holds", "hold_reason", "clears_when"}
    assert "entry_unchecked" in held and "entry_unchecked" in ready


def test_sort_rank_entry_held_directly_after_enter_now_and_order_preserved():
    order = ["ENTER_NOW", "ENTRY_HELD", "NEAR_ENTRY", "REMOVE", "HOLD_OFF_EARNINGS",
             "DATA_UNAVAILABLE", "WAIT_ENTRY", "WAIT_CATALYST"]
    ranks = [sort_key_for_action(a) for a in order]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    assert sort_key_for_action("SOMETHING_NEW") > max(ranks)


def test_every_action_the_advisor_can_emit_is_registered():
    from stock_analyzer.watchlist_advisor import _ACTION_PRIORITY, _ACTION_SORT_RANK
    assert set(_ACTION_PRIORITY) == set(_ACTION_SORT_RANK)
    assert "ENTRY_HELD" in _ACTION_PRIORITY


# -- downstream pure consumers -------------------------------------------------
def test_build_enter_now_rows_ignores_entry_held():
    held = _rec(ctx=_ctx(tone_state=BEAR))
    ready = _rec()
    import datetime
    d = datetime.date(2026, 10, 16)
    assert build_enter_now_rows([held], set(), d, {}, {}) == []
    assert len(build_enter_now_rows([ready], set(), d, {}, {})) == 1


# -- ledger ---------------------------------------------------------------------
def _ledger(cards):
    import datetime
    return gate_ledger.build_watchlist_suppression_rows(
        cards, rec_date=datetime.date(2026, 10, 16), source="app",
        sector_by_ticker={"XYZ": "Technology"})


def test_ledger_g29_and_g28_rows_only_for_their_kinds():
    tone = _rec(ctx=_ctx(tone_state=BEAR))
    mm = {"sectors": {"Technology"}, "reasons": {"Technology": "CPI"}}
    macro = _rec(ctx=_ctx(macro_map=mm))
    news = _rec(ctx=_ctx(news={"compound": -0.9, "n": 2, "headline": "x"}))
    stale = _rec(data=_data(stale_as_of="2026-10-10"))
    for c, t in ((tone, "A"), (macro, "B"), (news, "C"), (stale, "D")):
        c["ticker"] = t
    failed_card = dict(tone, ticker="E", suppression_kind=None, hold_kind="check_failed")
    rows = _ledger([tone, macro, news, stale, failed_card])
    assert sorted((r["ticker"], r["gate_id"]) for r in rows) == [("A", "G-29"), ("B", "G-28")]
    g29 = next(r for r in rows if r["gate_id"] == "G-29")
    assert g29["tone"] == "bear" and g29["gate_value"] == -0.8
    assert g29["lane"] == "downgrade" and g29["counterfactual"] is True
    g28 = next(r for r in rows if r["gate_id"] == "G-28")
    assert g28["tone"] is None and g28["lane"] == "downgrade" and g28["counterfactual"] is True


def test_ledger_entry_kind_requires_entry_held_action():
    tone = dict(_rec(ctx=_ctx(tone_state=BEAR)), ticker="A")
    assert _ledger([dict(tone, action="NEAR_ENTRY")]) == []
    assert _ledger([dict(tone, action="ENTER_NOW")]) == []


def test_ledger_near_entry_rows_unchanged():
    near = build_watchlist_recommendation(
        "XYZ", _data(), portfolio_ctx={"sector_weight_pct": 40.0, "sector_of_ticker": "Technology"})
    rows = _ledger([near])
    assert [r["gate_id"] for r in rows] == ["G-05"] and rows[0]["tone"] is None
    # NEAR_ENTRY with an entry kind is not a row either way
    assert _ledger([dict(near, suppression_kind="entry_tone")]) == []


def test_registry_has_the_new_ids_and_ledger_kinds_resolve_to_them():
    assert GATE_IDS["G-28"] and GATE_IDS["G-29"]
    assert gate_ledger._WATCHLIST_KIND["entry_macro"] == "G-28"
    assert gate_ledger._WATCHLIST_KIND["entry_tone"] == "G-29"
    assert set(gate_ledger._WATCHLIST_KIND) == set(gate_ledger._WATCHLIST_REASON)
