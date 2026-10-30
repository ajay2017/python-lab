"""stock_analyzer/entry_posture.py: may the redeploy headline call an entry on
today's market tone? (owner decisions 2026-10-29, finding #14)."""
import math
from datetime import date

import pytest

from stock_analyzer.constants import (
    COMPOSITE_BUY, GROW_TODAY_MAX_FUND_AGE_DAYS, MARKET_TONE_BEAR_PCT,
    MARKET_TONE_BULL_PCT, RR_ENTRY_MIN,
)
from stock_analyzer.daily_briefing import _grow_today, build_daily_briefing
from stock_analyzer.entry_eligibility import resolve_tone_state
from stock_analyzer.entry_posture import (
    locked_snapshot_for_today, new_entry_posture, page_home_state,
    redeploy_call, stricter_tone_state, tone_state_for_page,
)
from stock_analyzer.rr_readout import fmt_ratio_vs, redeploy_headline
from tests.conftest import find_item, make_port_df

pytestmark = pytest.mark.fast

_TODAY = date(2026, 7, 27)
_UNAVAIL = {"tone": None, "sp500_pct": None, "available": False, "source": None, "as_of": None}
_STATES = ("entry", "rr_fails", "no_upside", "rr_unknown", "none_pass", "none_scored")
_POSTURES = ("open", "confirmed_only", "deferred", "unknown")


def _live(pct):
    return resolve_tone_state(None, False, None, pct, True)


def _post(kind):
    pct = {"open": MARKET_TONE_BULL_PCT, "confirmed_only": 0.0,
           "deferred": MARKET_TONE_BEAR_PCT}.get(kind)
    return new_entry_posture(_live(pct) if pct is not None else _UNAVAIL)


def _cand(score=COMPOSITE_BUY + 5, rr=RR_ENTRY_MIN + 1.0, passes=True, t="XYZ", sec="Energy"):
    return {"ticker": t, "sector": sec, "score": score, "rr": rr, "passes": passes}


def _headline(state):
    return {
        "entry": redeploy_headline([_cand()]),
        "rr_fails": redeploy_headline([_cand(rr=RR_ENTRY_MIN - 0.5)]),
        "no_upside": redeploy_headline([_cand(rr=-1.0)]),
        "rr_unknown": redeploy_headline([_cand(rr=None)]),
        "none_pass": redeploy_headline([_cand(score=COMPOSITE_BUY - 10, passes=False)]),
        "none_scored": redeploy_headline([]),
    }[state]


def _legacy_text(score, rr, t="XYZ", sec="Energy"):
    return (
        f"**{t}** ({sec}) clears the Buy gate "
        f"({score:.0f} ≥ {COMPOSITE_BUY:.0f} · "
        f"R:R {fmt_ratio_vs(rr, RR_ENTRY_MIN)}:1 ≥ {RR_ENTRY_MIN:.0f}:1) "
        "— a genuine redeploy entry, not just a sector filler."
    )


# ── posture mapping ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("pct,expected", [
    (MARKET_TONE_BULL_PCT, "open"),
    (MARKET_TONE_BULL_PCT - 0.01, "confirmed_only"),
    (MARKET_TONE_BEAR_PCT, "deferred"),
    (MARKET_TONE_BEAR_PCT + 0.01, "confirmed_only"),
    (0.0, "confirmed_only"),
])
def test_posture_from_live_and_home_legs(pct, expected):
    assert new_entry_posture(_live(pct))["posture"] == expected
    ctx = {"tone_available": True, "sp500_pct": pct,
           "tone": _live(pct)["tone"]}
    home = resolve_tone_state(ctx, True, None, None, False)
    assert new_entry_posture(home)["posture"] == expected


@pytest.mark.parametrize("bad", [
    None, "bull", [], 5, _UNAVAIL,
    {"available": True, "tone": None, "sp500_pct": 1.0},
    {"available": True, "tone": "sideways", "sp500_pct": 1.0},
    {"available": True, "tone": "bull", "sp500_pct": float("nan")},
    {"available": True, "tone": "bull", "sp500_pct": float("inf")},
    {"available": True, "tone": "bull", "sp500_pct": None},
    {"available": "yes", "tone": "bull", "sp500_pct": 1.0},
    {"available": False, "tone": "bull", "sp500_pct": 1.0},
])
def test_posture_unknown_when_unreadable(bad):
    assert new_entry_posture(bad)["posture"] == "unknown"


def test_nan_live_read_is_unavailable_then_unknown():
    assert new_entry_posture(_live(math.nan))["posture"] == "unknown"
    assert new_entry_posture(_live(math.inf))["posture"] == "unknown"


# ── tone_state_for_page ──────────────────────────────────────────────────────
_TODAY_ISO = "2026-10-29"


def _synth(ctx, d=_TODAY_ISO):
    return {"date": d, "bundle": {"_market_context": ctx}}


def test_page_tone_uses_home_synth_dated_today():
    ctx = {"tone": "bull", "sp500_pct": 1.0, "tone_available": True, "tone_read_at": "9:42"}
    st = tone_state_for_page(_synth(ctx), _TODAY_ISO, -3.0, True)
    assert st["source"] == "home" and st["tone"] == "bull" and st["as_of"] == "9:42"


def test_page_tone_yesterdays_synth_falls_to_live():
    ctx = {"tone": "bull", "sp500_pct": 1.0, "tone_available": True}
    st = tone_state_for_page(_synth(ctx, "2026-10-28"), _TODAY_ISO, -1.0, True)
    assert st["source"] == "live" and st["tone"] == "bear"


def test_page_tone_live_failed_is_unavailable():
    assert tone_state_for_page(None, _TODAY_ISO, None, False) == _UNAVAIL
    ctx = {"tone": "bull", "sp500_pct": 1.0, "tone_available": True}
    assert tone_state_for_page(_synth(ctx, "2026-10-28"), _TODAY_ISO, None, False) == _UNAVAIL


@pytest.mark.parametrize("ctx", [
    {"tone": "bull", "sp500_pct": 1.0, "tone_available": False},
    {"tone": "bull", "sp500_pct": 1.0},
    None, "junk",
])
def test_page_tone_unreadable_home_ctx_never_bull(ctx):
    st = tone_state_for_page(_synth(ctx), _TODAY_ISO, None, False)
    assert st["available"] is False and st["tone"] is None
    # Home built today but its read failed: a live read is NOT consulted (#20a).
    st2 = tone_state_for_page(_synth(ctx), _TODAY_ISO, 0.0, True)
    assert st2 == _UNAVAIL


@pytest.mark.parametrize("synth", [
    None, 5, "x", {}, {"date": _TODAY_ISO}, {"date": _TODAY_ISO, "bundle": None},
    {"date": _TODAY_ISO, "bundle": []}, {"date": object(), "bundle": {}},
])
def test_page_tone_garbage_never_raises(synth):
    out = tone_state_for_page(synth, _TODAY_ISO, "bad", True)
    assert isinstance(out, dict) and "available" in out
    out = tone_state_for_page(synth, None, None, None)
    assert out["available"] is False


# ── redeploy_call ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("state", _STATES)
@pytest.mark.parametrize("kind", _POSTURES)
def test_entry_called_only_for_entry_on_open(state, kind):
    h = _headline(state)
    assert h["state"] == state
    call = redeploy_call(h, _post(kind))
    if call["entry_called"]:
        assert state == "entry" and kind == "open"
    if state != "entry":
        assert call["text"] is None and call["level"] is None
        assert call["entry_called"] is False
        assert call["invite_own_pick"] == (kind == "open")
    if kind != "open":
        assert call["invite_own_pick"] is False
    if not call["entry_called"] and call["text"] is not None:
        assert "genuine" not in call["text"]
    if call["text"] is not None:
        assert "$" not in call["text"]


def test_open_no_bundle_is_exact_legacy_text():
    c = _cand(score=COMPOSITE_BUY + 8, rr=RR_ENTRY_MIN + 0.8)
    call = redeploy_call(redeploy_headline([c]), _post("open"))
    assert call["entry_called"] is True
    assert call["level"] == "success" and call["icon"] == "🎯"
    assert call["invite_own_pick"] is True
    assert call["text"] == _legacy_text(c["score"], c["rr"])


def test_open_clean_bundle_still_calls_entry():
    call = redeploy_call(_headline("entry"), _post("open"), bundle={})
    assert call["entry_called"] is True


# ── boundaries through redeploy_headline ────────────────────────────────────
@pytest.mark.parametrize("kind", _POSTURES)
def test_boundaries_by_posture(kind):
    from stock_analyzer.portfolio import annotate_add_candidates
    below_row = annotate_add_candidates(
        ["XYZ"], {"XYZ": {"score": COMPOSITE_BUY - 0.01, "signal": "Hold", "rr": RR_ENTRY_MIN + 1}},
        buy_gate=COMPOSITE_BUY)
    below = redeploy_headline(below_row)
    assert below["state"] == "none_pass"
    assert redeploy_call(below, _post(kind))["entry_called"] is False

    edge = redeploy_headline([_cand(score=COMPOSITE_BUY, rr=RR_ENTRY_MIN)])
    assert redeploy_call(edge, _post(kind))["entry_called"] is (kind == "open")

    rr_low = redeploy_headline([_cand(score=COMPOSITE_BUY + 5, rr=RR_ENTRY_MIN - 0.01)])
    assert redeploy_call(rr_low, _post(kind))["entry_called"] is False


def test_flat_day_is_not_a_second_composite_bar():
    """The 78 flat-day bar is scanner momentum, not a composite bar: whatever
    the composite, a confirmed_only posture never calls an entry here."""
    for score in (77.99, 78.0, 95.0):
        h = redeploy_headline([_cand(score=score, rr=3.0)])
        call = redeploy_call(h, _post("confirmed_only"))
        assert call["entry_called"] is False
        assert call["level"] == "info"
        assert "Grow Today makes today's buy calls" in call["text"]


# ── data-trust hold (bull day) ──────────────────────────────────────────────
def test_stale_bundle_withholds_on_open():
    call = redeploy_call(_headline("entry"), _post("open"),
                         bundle={"stale_as_of": "2026-10-28"})
    assert call["entry_called"] is False and call["invite_own_pick"] is False
    assert call["level"] == "warning"
    assert "cached data" in call["text"]


def test_old_fundamentals_withhold_on_open():
    call = redeploy_call(
        _headline("entry"), _post("open"),
        bundle={"fund_cache_age_days": GROW_TODAY_MAX_FUND_AGE_DAYS + 1})
    assert call["entry_called"] is False and call["level"] == "warning"
    assert "genuine" not in call["text"]


class _BadBundle:
    def get(self, *a, **k):
        raise RuntimeError("boom")


def test_hold_check_failure_is_its_own_branch_not_unknown_tone():
    for b in ("junk", _BadBundle()):
        call = redeploy_call(_headline("entry"), _post("open"), bundle=b)
        assert call["entry_called"] is False and call["level"] == "warning"
        assert "tone couldn't be read" not in call["text"]
        assert "data couldn't be checked" in call["text"]
        assert call["source_line"] == "Market read live."


@pytest.mark.parametrize("kind", _POSTURES)
def test_no_sector_never_prints_none(kind):
    h = {**_headline("entry"), "sector": None}
    for b in (None, {"stale_as_of": "2026-10-28"}, "junk"):
        call = redeploy_call(h, _post(kind), bundle=b)
        if call["text"] is not None:
            assert "None" not in call["text"] and "()" not in call["text"]
            assert call["text"].count("(") == call["text"].count(")")


def test_protect_text_matching_home_only_when_tone_came_from_home():
    ctx = {"tone": "bear", "sp500_pct": MARKET_TONE_BEAR_PCT, "tone_available": True}
    home = new_entry_posture(resolve_tone_state(ctx, True, None, None, False))
    live = _post("deferred")
    assert "matching 🏠 Home" in redeploy_call(_headline("entry"), home)["text"]
    assert "matching" not in redeploy_call(_headline("entry"), live)["text"]


def test_trimming_sentence_only_on_home_site():
    h = _headline("entry")
    assert redeploy_call(h, _post("deferred"))["text"].endswith(" Trimming is still sound.")
    assert "Trimming" not in redeploy_call(h, _post("deferred"), site="signals")["text"]


@pytest.mark.parametrize("pct,txt", [
    (-0.497, "-0.497"), (0.4999, "+0.4999"), (-1.2, "-1.20"), (0.12, "+0.12"),
])
def test_pct_never_rounds_across_a_tone_boundary(pct, txt):
    from stock_analyzer.entry_posture import _fmt_pct
    assert _fmt_pct({"sp500_pct": pct}) == txt


def test_source_text_ratchet_redeploy_blocks_use_posture():
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    idxs = [m.start() for m in re.finditer(r"redeploy_headline\(_(?:display|top)\)", src)]
    assert len(idxs) == 2
    for i in idxs:
        block = " ".join(src[i:].splitlines()[:150])
        assert "_entry_posture.redeploy_call(" in block
        assert "_market_tone_cache" not in block
    # Signals reads its tone before the per-sector loop, outside the window above.
    t = src.index("_sg_tone = _entry_posture.tone_state_for_page(")
    assert "_market_tone_cache" not in " ".join(src[t:].splitlines()[:20])


def test_malformed_inputs_never_raise_and_withhold():
    for h in (None, {}, {"state": "entry"}, {"state": "entry", "score": None, "rr": None}):
        for p in (None, {}, "x", _post("open")):
            call = redeploy_call(h, p)
            assert isinstance(call, dict)
            if call["entry_called"]:
                assert isinstance(h, dict) and h.get("score") is not None
    assert redeploy_call(_headline("entry"), None)["entry_called"] is False
    assert redeploy_call(_headline("entry"), _post("open"), bundle="junk")["entry_called"] is False


def test_source_line_only_when_tone_readable():
    live = new_entry_posture(_live(1.0))
    assert redeploy_call(_headline("entry"), live)["source_line"] == "Market read live."
    assert redeploy_call(_headline("entry"), _post("unknown"))["source_line"] is None


# ── agreement with Grow Today (C4 pin) ──────────────────────────────────────
def _winner(**kw):
    row = dict(ticker="AAA", weight=10.0, score=COMPOSITE_BUY + 5, signal="Strong Buy",
               gap_to_stop=10.0)
    row.update(kw)
    return row


def test_posture_agrees_with_grow_today_tone_gates():
    port_df = make_port_df([_winner()])

    bear = build_daily_briefing(
        port_df=port_df, alert_list=[], risk_recs=[], news_items=[],
        macro_events=[], held_data={}, scanner_results=None,
        portfolio_value=100_000.0, today=_TODAY,
        market_context={"tone": "bear", "sp500_pct": MARKET_TONE_BEAR_PCT},
    )["grow_today"]
    assert bear["new_picks"] == [] and bear.get("add_positions", []) == []
    assert new_entry_posture(_live(MARKET_TONE_BEAR_PCT))["posture"] == "deferred"

    flat = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "flat"})
    assert flat["add_positions"] == []
    assert new_entry_posture(_live(0.0))["posture"] == "confirmed_only"

    bull = _grow_today(port_df, None, [], {}, _TODAY, 100_000.0, {"tone": "bull"})
    assert find_item(bull["add_positions"], "AAA") is not None
    assert new_entry_posture(_live(MARKET_TONE_BULL_PCT))["posture"] == "open"


# ── #20: Home-today-failed withholds; lock follows the stricter tone ─────────
_BULL_CTX = {"tone": "bull", "sp500_pct": 1.0, "tone_available": True}


def test_page_home_state_three_states():
    from stock_analyzer.entry_eligibility import (
        HOME_NOT_TODAY, HOME_TODAY_OK, HOME_TODAY_TONE_FAILED)
    assert page_home_state(_synth(_BULL_CTX), _TODAY_ISO) == HOME_TODAY_OK
    assert page_home_state(_synth({"tone_available": False}), _TODAY_ISO) == HOME_TODAY_TONE_FAILED
    assert page_home_state(_synth(_BULL_CTX, "2026-10-28"), _TODAY_ISO) == HOME_NOT_TODAY
    assert page_home_state(None, _TODAY_ISO) == HOME_NOT_TODAY
    assert page_home_state({"date": _TODAY_ISO, "bundle": []}, _TODAY_ISO) == HOME_NOT_TODAY


def test_home_today_failed_ignores_live_bull():
    st = tone_state_for_page(_synth({"tone_available": False}), _TODAY_ISO, 2.0, True)
    assert st == _UNAVAIL
    assert new_entry_posture(st)["posture"] == "unknown"


def test_home_not_today_still_uses_live():
    st = tone_state_for_page(_synth(_BULL_CTX, "2026-10-28"), _TODAY_ISO, 2.0, True)
    assert st["source"] == "live" and st["tone"] == "bull"


def _lock(tone, pct):
    return {"grow_today": {"tone": tone, "sp500_pct": pct}, "x": 1}


def _cur(pct):
    return resolve_tone_state({"tone": classify(pct), "sp500_pct": pct, "tone_available": True},
                              True, None, None, False)


def classify(pct):
    from stock_analyzer.entry_eligibility import classify_tone
    return classify_tone(pct)


def test_lock_bear_current_bull_follows_lock():
    cur = _cur(MARKET_TONE_BULL_PCT)
    out = stricter_tone_state(cur, _lock("bear", MARKET_TONE_BEAR_PCT), "9:45")
    assert out == {"tone": "bear", "sp500_pct": MARKET_TONE_BEAR_PCT, "available": True,
                   "source": "home_locked", "as_of": "9:45"}
    assert new_entry_posture(out)["posture"] == "deferred"


def test_lock_bull_current_bear_keeps_current():
    cur = _cur(MARKET_TONE_BEAR_PCT)
    assert stricter_tone_state(cur, _lock("bull", MARKET_TONE_BULL_PCT)) is cur


def test_lock_flat_vs_bull_current_follows_lock_and_tie_keeps_current():
    assert stricter_tone_state(_cur(MARKET_TONE_BULL_PCT), _lock("flat", 0.0))["tone"] == "flat"
    cur = _cur(0.0)
    assert stricter_tone_state(cur, _lock("flat", 0.1)) is cur


def test_no_lock_returns_current():
    cur = _cur(0.0)
    assert stricter_tone_state(cur, None) is cur


@pytest.mark.parametrize("snap", [
    {}, {"grow_today": None}, {"grow_today": {"tone": "sideways", "sp500_pct": 0.0}},
    {"grow_today": {"tone": "bull", "sp500_pct": float("nan")}},
    {"grow_today": {"tone": "bull", "sp500_pct": None}}, "junk",
])
def test_malformed_lock_withholds(snap):
    assert stricter_tone_state(_cur(0.0), snap) == _UNAVAIL


def test_current_unavailable_stays_unavailable():
    assert stricter_tone_state(_UNAVAIL, _lock("bull", 1.0)) == _UNAVAIL


def test_deferred_from_lock_says_matching_home():
    out = stricter_tone_state(_cur(MARKET_TONE_BULL_PCT), _lock("bear", MARKET_TONE_BEAR_PCT), "9:45")
    call = redeploy_call(_headline("entry"), new_entry_posture(out))
    assert "matching 🏠 Home" in call["text"]
    assert call["source_line"].startswith("Market read from Home's locked Brief")
    live = redeploy_call(_headline("entry"), _post("deferred"))
    assert "matching 🏠 Home" not in live["text"]


def test_redeploy_call_passes_home_state_to_live_caption():
    from stock_analyzer.entry_eligibility import HOME_NOT_TODAY
    p = new_entry_posture(_live(MARKET_TONE_BULL_PCT))
    call = redeploy_call(_headline("entry"), p, home_state=HOME_NOT_TODAY)
    assert "isn't built today" in call["source_line"]


def test_locked_snapshot_for_today():
    snap = {"grow_today": {"tone": "flat"}}
    d = date(2026, 10, 29)
    assert locked_snapshot_for_today(True, d, d, snap) is snap
    assert locked_snapshot_for_today(False, d, d, snap) is None
    assert locked_snapshot_for_today(True, date(2026, 10, 28), d, snap) is None
    assert locked_snapshot_for_today(True, d, d, {}) is None
    assert locked_snapshot_for_today(True, d, d, None) is None
    from stock_analyzer.act_today_view import OFFLINE_BRIEF_MARKER
    assert locked_snapshot_for_today(True, d, d, {OFFLINE_BRIEF_MARKER: True, "k": 1}) is None


# ── #20 review fix-first: locks built from REAL _grow_today output ───────────
def _real_gt(ctx):
    return _grow_today(make_port_df([_winner()]), None, [], {}, _TODAY, 100_000.0, ctx)


def _real_lock(ctx):
    return {"grow_today": _real_gt(ctx), "x": 1}


def test_real_bear_lock_follows_lock_and_says_protect():
    ctx = {"tone": "bear", "sp500_pct": MARKET_TONE_BEAR_PCT - 0.5, "tone_available": True}
    out = stricter_tone_state(_cur(MARKET_TONE_BULL_PCT), _real_lock(ctx), "9:45")
    assert out["source"] == "home_locked" and out["tone"] == "bear"
    assert new_entry_posture(out)["posture"] == "deferred"


def test_real_bear_lock_with_bear_current_is_protect_not_unknown():
    ctx = {"tone": "bear", "sp500_pct": MARKET_TONE_BEAR_PCT - 0.5, "tone_available": True}
    cur = _cur(MARKET_TONE_BEAR_PCT - 0.2)
    out = stricter_tone_state(cur, _real_lock(ctx))
    assert out is cur
    call = redeploy_call(_headline("entry"), new_entry_posture(out))
    assert "Protect Mode" in call["text"]


def test_grow_today_carries_tone_available_on_both_paths():
    for tone in ("bear", "flat"):
        for ta in (False, True, None):
            ctx = {"tone": tone, "sp500_pct": -2.0 if tone == "bear" else 0.0}
            if ta is not None:
                ctx["tone_available"] = ta
            gt = _real_gt(ctx)
            assert gt["tone_available"] is ta
            assert gt["sp500_pct"] == ctx["sp500_pct"]


def test_failed_read_lock_withholds_but_missing_key_keeps_behaviour():
    cur = _cur(MARKET_TONE_BULL_PCT)
    failed = {"grow_today": {"tone": "flat", "sp500_pct": 0.0, "tone_available": False}}
    assert stricter_tone_state(cur, failed) == _UNAVAIL
    ok = {"grow_today": {"tone": "flat", "sp500_pct": 0.0, "tone_available": True}}
    assert stricter_tone_state(cur, ok)["source"] == "home_locked"
    legacy = {"grow_today": {"tone": "flat", "sp500_pct": 0.0}}
    assert stricter_tone_state(cur, legacy)["source"] == "home_locked"


def test_unknown_posture_retry_wording_by_site():
    p = new_entry_posture(_UNAVAIL)
    assert "Reload to retry." in redeploy_call(_headline("entry"), p, site="home")["text"]
    sig = redeploy_call(_headline("entry"), p, site="signals")["text"]
    assert "Open 🏠 Home to retry." in sig and "Reload to retry" not in sig


def test_home_today_nan_pct_never_falls_back_to_live():
    ctx = {"tone": "bull", "sp500_pct": float("nan"), "tone_available": True}
    st = tone_state_for_page(_synth(ctx), _TODAY_ISO, 2.0, True)
    assert st == _UNAVAIL
