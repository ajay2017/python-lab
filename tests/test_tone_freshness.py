"""Buy-check C4a (docs/plans/entry-fail-closed-c4-spec.md, owner U1 2026-11-27):
when 🏠 Home's market-tone read may be reused by another page, the resolver /
page wiring that uses it, the `tone_unread` hold and its binding precedence."""
import ast
import pathlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from stock_analyzer import entry_eligibility as ee
from stock_analyzer import entry_posture as ep
from stock_analyzer import tone_freshness as tf
from stock_analyzer.constants import (
    BRIEF_AUTO_REFRESH_MINUTES, TONE_HOME_READ_MAX_AGE_MINUTES,
)
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

ET = ZoneInfo("America/New_York")
ROOT = pathlib.Path(__file__).resolve().parent.parent


def _et(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


def _ctx(read_at, **kw):
    c = {"tone": "bull", "sp500_pct": 1.0, "tone_available": True, "tone_read_at": read_at}
    c.update(kw)
    return c


def _state(read_at, now, **kw):
    return tf.home_read_state(_ctx(read_at, **kw), True, now)["state"]


# ═════════════════════ the constant ═══════════════════════════════════════════
def test_constant_is_an_alias_of_the_brief_refresh_cadence():
    assert TONE_HOME_READ_MAX_AGE_MINUTES == BRIEF_AUTO_REFRESH_MINUTES == 30


# ═════════════════════ home_read_state ════════════════════════════════════════
WED = (2026, 11, 25)     # a normal trading day


def test_age_boundary_is_strict_29m59_usable_30m00_aged():
    now = _et(*WED, 11, 0, 0)
    assert _state(now - timedelta(minutes=29, seconds=59), now) == tf.STATE_USABLE
    assert _state(now - timedelta(minutes=30), now) == tf.STATE_AGED
    assert _state(now - timedelta(minutes=30, seconds=1), now) == tf.STATE_AGED
    assert _state(now, now) == tf.STATE_USABLE                      # age 0


def test_result_carries_read_at_and_age():
    now = _et(*WED, 11, 0)
    r = tf.home_read_state(_ctx(now - timedelta(minutes=12)), True, now)
    assert r["state"] == "usable" and r["read_at"] == now - timedelta(minutes=12)
    assert r["age_min"] == pytest.approx(12.0)


def test_future_read_time_is_unreadable_same_clock():
    now = _et(*WED, 11, 0)
    assert _state(now + timedelta(seconds=1), now) == tf.STATE_UNREADABLE


@pytest.mark.parametrize("bad", [
    datetime(2026, 11, 25, 10, 50),          # naive
    "10:50", 1.5, None, [],
])
def test_naive_or_garbage_read_time_is_unreadable(bad):
    now = _et(*WED, 11, 0)
    assert _state(bad, now) == tf.STATE_UNREADABLE


def test_naive_now_is_unreadable_not_a_crash():
    assert _state(_et(*WED, 10, 50), datetime(2026, 11, 25, 11, 0)) == tf.STATE_UNREADABLE
    assert _state(_et(*WED, 10, 50), None) == tf.STATE_UNREADABLE


def test_absent_read_time_key_is_no_read_time():
    ctx = {"tone": "bull", "sp500_pct": 1.0, "tone_available": True}
    assert tf.home_read_state(ctx, True, _et(*WED, 11, 0))["state"] == tf.STATE_NO_READ_TIME


def test_not_built_today():
    now = _et(*WED, 11, 0)
    assert tf.home_read_state(_ctx(now), False, now)["state"] == tf.STATE_NOT_TODAY


@pytest.mark.parametrize("ctx", [
    {"tone": "bull", "sp500_pct": 1.0, "tone_available": False},
    {"tone": "bull", "sp500_pct": 1.0},
    {"tone": "bull", "sp500_pct": float("nan"), "tone_available": True},
    {"tone": "bull", "sp500_pct": float("inf"), "tone_available": True},
    {"tone": None, "sp500_pct": 1.0, "tone_available": True},
    {"tone": "bull", "sp500_pct": True, "tone_available": True},
    {"tone": "bull", "sp500_pct": 1.0, "tone_available": 1},
    None, "junk",
])
def test_home_failed_when_its_own_read_failed_or_pct_not_finite(ctx):
    now = _et(*WED, 11, 0)
    full = ctx if not isinstance(ctx, dict) else {**ctx, "tone_read_at": now}
    assert tf.home_read_state(full, True, now)["state"] == tf.STATE_FAILED


def test_failed_beats_a_missing_read_time():
    now = _et(*WED, 11, 0)
    assert tf.home_read_state({"tone_available": False}, True, now)["state"] == "failed"


def test_one_second_of_age_across_the_open_is_other_session():
    # 09:29:59 read vs 09:30:00 now: 1 s old, but the pre-open read is the last
    # session's move and now is today's.
    now = _et(2026, 11, 25, 9, 30, 0)
    assert _state(_et(2026, 11, 25, 9, 29, 59), now) == tf.STATE_OTHER_SESSION
    # both pre-open: same session
    assert _state(_et(2026, 11, 25, 8, 0), _et(2026, 11, 25, 8, 20)) == tf.STATE_USABLE
    # both at / after the open: same session
    assert _state(_et(2026, 11, 25, 9, 30, 0), _et(2026, 11, 25, 9, 40)) == tf.STATE_USABLE


# ─── the time-context matrix ───
def test_matrix_preopen_home_0800_page_0820_is_usable_and_labelled_last_session():
    now = _et(2026, 11, 25, 8, 20)
    r = ee.resolve_tone_state(_ctx(_et(2026, 11, 25, 8, 0)), True,
                              _et(2026, 11, 25, 8, 0), None, False, now_et=now)
    assert r["source"] == "home" and r["why"] is None
    line = ee.tone_source_line(r)
    assert line == ("Market read from Home's Brief, 8:00 AM ET. "
                    "This is the move in the last session (Tue Nov 24), not today's.")


def test_matrix_home_0920_page_0935_is_other_session():
    assert _state(_et(*WED, 9, 20), _et(*WED, 9, 35)) == tf.STATE_OTHER_SESSION


def test_matrix_home_1000_page_1400_is_aged():
    assert _state(_et(*WED, 10, 0), _et(*WED, 14, 0)) == tf.STATE_AGED


def test_matrix_after_close_home_1500_page_1630_is_aged_not_other_session():
    assert _state(_et(*WED, 15, 0), _et(*WED, 16, 30)) == tf.STATE_AGED


def test_matrix_saturday_reads_fridays_session():
    # Sat 2026-11-21: Home 10:00 / page 10:20 -> same session (Friday); 10:31 aged.
    read = _et(2026, 11, 21, 10, 0)
    assert _state(read, _et(2026, 11, 21, 10, 20)) == tf.STATE_USABLE
    assert _state(read, _et(2026, 11, 21, 10, 31)) == tf.STATE_AGED


def test_matrix_thanksgiving_holiday_behaves_as_a_weekend():
    # Thu 2026-11-26 is a market holiday: the session is Wed 11-25 all day.
    read = _et(2026, 11, 26, 10, 0)
    assert _state(read, _et(2026, 11, 26, 10, 29)) == tf.STATE_USABLE
    assert _state(read, _et(2026, 11, 26, 10, 31)) == tf.STATE_AGED
    r = ee.resolve_tone_state(_ctx(read), True, read, None, False,
                              now_et=_et(2026, 11, 26, 10, 20))
    assert "in the last session (Wed Nov 25)" in ee.tone_source_line(r)


def test_matrix_early_close_friday_home_1250_page_1310_is_usable():
    assert _state(_et(2026, 11, 27, 12, 50), _et(2026, 11, 27, 13, 10)) == tf.STATE_USABLE


def test_matrix_dst_repeated_hour_uses_aware_math():
    # Sun 2026-11-01: 01:50 EDT (fold 0) then 01:10 EST (fold 1) = 20 minutes later.
    read = datetime(2026, 11, 1, 1, 50, tzinfo=ET, fold=0)
    now = datetime(2026, 11, 1, 1, 10, tzinfo=ET, fold=1)
    # a wall-clock subtraction would read 01:10 - 01:50 as a FUTURE read time
    assert now - read < timedelta(0)
    r = tf.home_read_state(_ctx(read), True, now)
    assert r["state"] == tf.STATE_USABLE and r["age_min"] == pytest.approx(20.0)
    # and at 01:41 EST (51 minutes after the read) it is aged
    assert tf.home_read_state(
        _ctx(read), True, datetime(2026, 11, 1, 1, 41, tzinfo=ET, fold=1))["state"] == "aged"


@pytest.mark.parametrize("day", [(2026, 11, 2), (2027, 3, 8), (2027, 3, 15)])
def test_zoneinfo_conversion_of_a_utc_read_time_across_the_open(day):
    # Read stamped in UTC at 09:29 ET, page at 09:31 ET: other_session. A fixed
    # -5 / -4 offset instead of the zone would put the read on the wrong side of
    # the open for one of these dates.
    now = _et(*day, 9, 31)
    read_utc = _et(*day, 9, 29).astimezone(timezone.utc)
    assert tf.home_read_state(_ctx(read_utc), True, now)["state"] == tf.STATE_OTHER_SESSION
    assert tf.home_read_state(_ctx(_et(*day, 9, 31).astimezone(timezone.utc)),
                              True, _et(*day, 9, 40))["state"] == tf.STATE_USABLE


def test_pytz_now_is_accepted():
    from stock_analyzer.market_time import ET as PYTZ_ET
    now = PYTZ_ET.localize(datetime(2026, 11, 25, 11, 0))
    read = PYTZ_ET.localize(datetime(2026, 11, 25, 10, 45))
    assert tf.home_read_state(_ctx(read), True, now)["state"] == tf.STATE_USABLE


def test_no_function_in_the_module_reads_the_clock():
    tree = parse_file_cached(ROOT / "stock_analyzer" / "tone_freshness.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("now", "today", "utcnow", "fromtimestamp",
                                          "time", "monotonic"), node.func.attr
    src = (ROOT / "stock_analyzer" / "tone_freshness.py").read_text(encoding="utf-8")
    assert "entry_eligibility" not in src.replace("``entry_eligibility``", "")


# ═════════════════════ wording ════════════════════════════════════════════════
def test_unread_reason_and_details_per_why():
    assert tf.unread_reason(None) == "the market move couldn't be read"
    read = _et(*WED, 9, 42)
    aged = {"why": "home_aged", "home_read_at": read}
    assert tf.unread_detail(aged) == (
        "Home's read from 9:42 AM ET is more than 30 minutes old and a fresh "
        "S&P 500 read failed.")
    assert "earlier session" in tf.unread_detail({"why": "home_other_session",
                                                  "home_read_at": read})
    assert "no readable time" in tf.unread_detail({"why": "home_unreadable"})
    assert tf.unread_detail({"why": "home_failed"}) == (
        "🏠 Home's Brief couldn't read the S&P 500 this run, so its new picks are held too.")
    assert "isn't built today" in tf.unread_detail({"why": "home_not_today"})
    for ts in ({"why": "live_failed"}, {}, None, "junk"):
        assert tf.unread_detail(ts) == "The S&P 500 read failed."


def test_session_phrase_only_when_the_move_is_an_earlier_sessions():
    assert tf.session_phrase({"as_of": _et(2026, 11, 25, 8, 0)}) == (
        "in the last session (Tue Nov 24)")
    assert tf.session_phrase({"as_of": _et(2026, 11, 25, 9, 30)}) is None
    assert tf.session_phrase({"as_of": _et(2026, 11, 28, 11, 0)}) == (
        "in the last session (Fri Nov 27)")
    for bad in ({"as_of": "9:42"}, {"as_of": None}, {}, None, "x"):
        assert tf.session_phrase(bad) is None


# ═════════════════════ resolve_tone_state ═════════════════════════════════════
NOW = _et(*WED, 11, 0)


def _resolve(ctx, today=True, live_pct=None, live_ok=False, **kw):
    built = ctx.get("tone_read_at") if isinstance(ctx, dict) else None
    return ee.resolve_tone_state(ctx, today, built, live_pct, live_ok, now_et=NOW, **kw)


def test_resolver_home_usable_uses_home_and_carries_no_why():
    r = _resolve(_ctx(NOW - timedelta(minutes=5)), live_pct=-3.0, live_ok=True)
    assert (r["source"], r["tone"], r["why"]) == ("home", "bull", None)
    assert r["home_read_at"] == NOW - timedelta(minutes=5)


@pytest.mark.parametrize("ctx_kw,why", [
    ({"read_at": NOW - timedelta(minutes=31)}, "home_aged"),
    ({"read_at": _et(*WED, 9, 20)}, "home_aged"),
    ({"read_at": "9:42"}, "home_unreadable"),
    ({"read_at": NOW + timedelta(minutes=1)}, "home_unreadable"),
])
def test_resolver_unusable_home_falls_to_live_and_says_why(ctx_kw, why):
    live_at = NOW
    r = _resolve(_ctx(ctx_kw["read_at"]), live_pct=-1.0, live_ok=True, live_read_at=live_at)
    assert (r["source"], r["tone"], r["why"], r["as_of"]) == ("live", "bear", why, live_at)


def test_resolver_other_session_why():
    now = _et(*WED, 9, 35)
    r = ee.resolve_tone_state(_ctx(_et(*WED, 9, 20)), True, None, 0.9, True, now_et=now)
    assert r["why"] == "home_other_session" and r["source"] == "live"


def test_resolver_home_failed_with_live_ok_reads_live_and_says_home_failed():
    ctx = {"tone": "flat", "sp500_pct": 0.0, "tone_available": False}
    r = _resolve(ctx, live_pct=-0.8, live_ok=True)
    assert (r["source"], r["tone"], r["why"]) == ("live", "bear", "home_failed")


def test_resolver_home_not_built_today_why():
    r = _resolve(None, today=False, live_pct=0.9, live_ok=True)
    assert (r["source"], r["why"]) == ("live", "home_not_today")


@pytest.mark.parametrize("ctx,today,want", [
    (_ctx(NOW - timedelta(minutes=45)), True, "home_aged"),
    (_ctx(_et(*WED, 9, 0)), True, "home_aged"),
    ({"tone": "bull", "sp500_pct": 1.0, "tone_available": True,
      "tone_read_at": "x"}, True, "home_unreadable"),
    ({"tone_available": False}, True, "home_failed"),
    (None, False, "live_failed"),
])
def test_resolver_nothing_readable_is_unavailable_with_a_why(ctx, today, want):
    r = _resolve(ctx, today=today)
    assert r["available"] is False and r["tone"] is None and r["source"] is None
    assert r["why"] == want


def test_resolver_nan_live_is_unavailable():
    r = _resolve(None, today=False, live_pct=float("nan"), live_ok=True)
    assert r["available"] is False and r["why"] == "live_failed"


def test_resolver_now_et_none_is_the_legacy_rule():
    # No freshness gate: a same-day Home read of any age or read time is used
    # (the cron and the Home redeploy site read it this way on purpose).
    old = _ctx(datetime(2026, 11, 25, 8, 0))          # naive, hours old
    r = ee.resolve_tone_state(old, True, "x", None, False)
    assert r["source"] == "home" and r["why"] is None
    no_key = {"tone": "bull", "sp500_pct": 1.0, "tone_available": True}
    assert ee.resolve_tone_state(no_key, True, "x", None, False)["source"] == "home"
    # live leg: as_of is the live read time (None by default)
    r = ee.resolve_tone_state(None, False, None, 0.9, True)
    assert r["source"] == "live" and r["as_of"] is None
    assert ee.resolve_tone_state(None, False, None, 0.9, True,
                                 live_read_at=NOW)["as_of"] == NOW
    r = ee.resolve_tone_state(None, False, None, None, False)
    assert r["available"] is False and r["why"] == "live_failed"


def test_live_source_line_names_why_home_was_not_used():
    read = _et(*WED, 9, 42)
    live_at = _et(*WED, 14, 14)
    r = ee.resolve_tone_state(_ctx(read), True, read, 0.3, True,
                              now_et=_et(*WED, 14, 14), live_read_at=live_at)
    assert ee.tone_source_line(r) == (
        "Market read live at 2:14 PM ET (Home's read from 9:42 AM ET is over 30 "
        "minutes old).")
    failed = ee.resolve_tone_state({"tone_available": False}, True, None, 0.3, True,
                                   now_et=_et(*WED, 14, 14), live_read_at=live_at)
    assert "its new picks are held until it can." in ee.tone_source_line(failed)
    assert "runs the day as flat" not in ee.tone_source_line(failed)
    assert ee.tone_source_line(
        ee.resolve_tone_state(None, False, None, 0.3, True, now_et=NOW)
    ) == "Market read live (🏠 Home's Brief isn't built today)."


# ═════════════════════ tone_state_for_page / page_needs_live ══════════════════
TODAY_ISO = "2026-11-25"


def _synth(ctx, d=TODAY_ISO):
    return {"date": d, "bundle": {"_market_context": ctx}}


def test_page_home_usable_never_needs_live():
    st = ep.tone_state_for_page(_synth(_ctx(NOW - timedelta(minutes=5))), TODAY_ISO,
                                None, False, now_et=NOW)
    assert st["source"] == "home" and ep.page_needs_live(st) is False


@pytest.mark.parametrize("read_at,now", [
    (NOW - timedelta(minutes=31), NOW),                      # aged
    (_et(*WED, 9, 20), _et(*WED, 9, 35)),                    # other session
    ("junk", NOW),                                           # unreadable
])
def test_page_unusable_home_needs_live_and_then_takes_it(read_at, now):
    syn = _synth(_ctx(read_at))
    first = ep.tone_state_for_page(syn, TODAY_ISO, None, False, now_et=now)
    assert first["available"] is False and ep.page_needs_live(first) is True
    second = ep.tone_state_for_page(syn, TODAY_ISO, -0.9, True, now_et=now,
                                    live_read_at=now)
    assert second["source"] == "live" and second["tone"] == "bear"
    assert ep.page_needs_live(second) is False
    # a failed live read leaves it unavailable (the entry check HOLDS)
    third = ep.tone_state_for_page(syn, TODAY_ISO, None, False, now_et=now)
    assert third["available"] is False


def test_page_home_failed_never_takes_the_live_read():
    for ctx in ({"tone_available": False}, {"tone": "bull", "tone_available": True,
                                            "sp500_pct": float("nan")}):
        st = ep.tone_state_for_page(_synth(ctx), TODAY_ISO, 2.0, True, now_et=NOW,
                                    live_read_at=NOW)
        assert st["available"] is False and st["why"] == "home_failed"
        assert ep.page_needs_live(st) is False


def test_page_home_not_built_today_needs_live():
    st = ep.tone_state_for_page(None, TODAY_ISO, None, False, now_et=NOW)
    assert st["available"] is False and ep.page_needs_live(st) is True
    live = ep.tone_state_for_page(None, TODAY_ISO, 0.9, True, now_et=NOW)
    assert live["source"] == "live"


def test_page_needs_live_edge_inputs():
    assert ep.page_needs_live(None) is True
    assert ep.page_needs_live({"available": True}) is False
    assert ep.page_needs_live({"available": False}) is True
    assert ep.page_needs_live({"available": False, "why": "home_failed"}) is False


def test_page_legacy_now_et_none_is_unchanged():
    st = ep.tone_state_for_page(_synth(_ctx(datetime(2026, 11, 25, 8, 0))), TODAY_ISO,
                                None, False)
    assert st["source"] == "home"                       # no freshness gate


def test_redeploy_unknown_text_stays_true_to_todays_grow_today():
    # C4a: Home's Grow Today still runs an unread day as flat (C4b flips this).
    p = ep.new_entry_posture({"available": False})
    out = ep.redeploy_call({"state": "entry", "ticker": "AAA", "score": 70.0, "rr": 2.5},
                           p)
    assert "Grow Today holds new picks until the market move can be read." in out["text"]
    assert "flat day" not in out["text"]


def test_redeploy_source_line_carries_why_and_home_read_at():
    read = _et(*WED, 9, 42)
    live_at = _et(*WED, 14, 14)
    st = ee.resolve_tone_state(_ctx(read), True, read, 0.3, True,
                               now_et=_et(*WED, 14, 14), live_read_at=live_at)
    p = ep.new_entry_posture(st)
    assert p["why"] == "home_aged" and p["home_read_at"] == read
    out = ep.redeploy_call({"state": "entry", "ticker": "AAA", "score": 70.0, "rr": 2.5},
                           p)
    assert out["source_line"] == (
        "Market read live at 2:14 PM ET (Home's read from 9:42 AM ET is over 30 "
        "minutes old).")
    # an unknown posture carries no why
    assert ep.new_entry_posture({"available": False, "why": "home_aged"})["why"] is None


def test_clock_text_converts_an_aware_time_to_et_and_leaves_naive_alone():
    utc = datetime(2026, 11, 25, 14, 42, tzinfo=timezone.utc)      # 9:42 AM EST
    assert ee._fmt_clock(utc) == "9:42 AM"
    assert ee._fmt_clock(datetime(2026, 7, 1, 13, 5, tzinfo=timezone.utc)) == "9:05 AM"
    assert ee._fmt_clock(datetime(2026, 11, 25, 9, 42)) == "9:42 AM"   # naive as-is
    assert ee._fmt_clock("junk") is None
    st = {"available": True, "source": "home", "as_of": utc, "tone": "flat",
          "sp500_pct": 0.1}
    assert ee.tone_source_line(st).startswith("Market read from Home's Brief, 9:42 AM ET.")


# ═════════════════════ check_tone / binding ═══════════════════════════════════
@pytest.mark.parametrize("ts", [{"available": False}, None, "junk", {}, 5,
                                {"available": False, "why": "home_aged"}])
def test_check_tone_unread_is_a_hold_with_no_gate_id(ts):
    r = ee.check_tone({"tone_state": ts, "gate_ids": {"tone": "G-29"}})
    assert r["kind"] == "hold" and r["check"] == "tone_unread"
    assert r["gate_id"] is None and r["gate_value"] is None and r["gate_threshold"] is None
    assert r["reason"] == "the market move couldn't be read"
    assert r["clears_when"] == ("the S&P 500 read succeeds — re-checked every time "
                                "you open this page")


def test_check_tone_bear_unchanged_and_flat_bull_pass():
    bear = {"available": True, "tone": "bear", "sp500_pct": -0.8}
    r = ee.check_tone({"tone_state": bear, "gate_ids": {"tone": "G-29"}})
    assert r["check"] == "tone" and r["gate_id"] == "G-29" and r["reason"].startswith(
        "market risk-off")
    for tone in ("flat", "bull"):
        assert ee.check_tone({"tone_state": {"available": True, "tone": tone,
                                             "sp500_pct": 0.1}}) is None


def test_default_checks_still_contain_check_tone():
    assert ee.check_tone in ee.DEFAULT_CHECKS


def test_entry_check_binding_precedence():
    unread = {"available": False}
    macro_map = {"sectors": {"Tech"}, "reasons": {"Tech": "CPI"}}
    news = {"compound": -0.9, "n": 2, "headline": "bad"}
    flat = {"available": True, "tone": "flat", "sp500_pct": 0.0}
    r = ee.entry_check(bundle={}, tone_state=unread, sector="Tech", macro_map=macro_map,
                       news=news)
    assert [h["check"] for h in r["holds"]] == ["tone_unread", "macro", "news"]
    assert r["binding"]["check"] == "macro"                  # policy hold first
    r = ee.entry_check(bundle={}, tone_state=unread, sector="Tech",
                       macro_map={"sectors": set(), "reasons": {}}, news=news)
    assert r["binding"]["check"] == "news"
    r = ee.entry_check(bundle={"stale_as_of": "2026-11-20"}, tone_state=unread,
                       macro_map={"sectors": set(), "reasons": {}},
                       news={"compound": 0.2, "n": 1, "headline": "ok"})
    assert [h["check"] for h in r["holds"]] == ["stale", "tone_unread"]
    assert r["binding"]["check"] == "stale"
    r = ee.entry_check(bundle={}, tone_state=unread,
                       macro_map={"sectors": set(), "reasons": {}},
                       news={"compound": 0.2, "n": 1, "headline": "ok"})
    assert r["binding"]["check"] == "tone_unread"            # alone: it binds
    # an eligible name has no binding
    assert ee.entry_check(bundle={}, tone_state=flat,
                          macro_map={"sectors": set(), "reasons": {}},
                          news={"compound": 0.2, "n": 1, "headline": "ok"})["binding"] is None
    # the unknown-sector abstention is NOT in the set: it binds ahead of tone_unread
    r = ee.entry_check(bundle={}, tone_state=unread, sector=None,
                       macro_map={"sectors": {"Tech"}, "reasons": {"Tech": "CPI"}},
                       news={"compound": 0.2, "n": 1, "headline": "ok"})
    assert [h["check"] for h in r["holds"]] == ["tone_unread", "macro_unknown_sector"]
    assert r["binding"]["check"] == "macro_unknown_sector"
    assert ee._ABSTENTION_CHECKS == frozenset({"tone_unread"})


def test_held_kind_label_and_held_label():
    assert ee.HELD_KIND_LABELS["tone_unread"] == "market move couldn't be read"
    assert ee.held_label({"hold_kind": "tone_unread"}) == "Held: market move couldn't be read"
    assert ee.held_label({"hold_kind": "tone"}) == "Deferred — market risk-off"


# ═════════════════════ AST pins (both ends) ═══════════════════════════════════
def _calls(tree, name):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if fname == name:
                out.append(node)
    return out


def test_pin_every_app_resolve_tone_state_call_passes_now_et():
    tree = parse_file_cached(ROOT / "app.py")
    calls = _calls(tree, "resolve_tone_state")
    assert len(calls) == 5          # Home redeploy + Watchlist x2 + Analysis x2
    none_sites = []
    for c in calls:
        kw = {k.arg: k.value for k in c.keywords}
        assert "now_et" in kw, f"app.py:{c.lineno} resolve_tone_state without now_et"
        if isinstance(kw["now_et"], ast.Constant) and kw["now_et"].value is None:
            none_sites.append(c.lineno)
    # only the Home-redeploy site (Home reads its own build on purpose) may pass None
    assert len(none_sites) == 1
    app_lines = (ROOT / "app.py").read_text(encoding="utf-8").splitlines()
    window = "\n".join(app_lines[max(0, none_sites[0] - 12):none_sites[0]])
    assert "Home's own market read only" in window


def test_pin_every_app_tone_state_for_page_call_passes_now_et():
    tree = parse_file_cached(ROOT / "app.py")
    calls = _calls(tree, "tone_state_for_page")
    assert len(calls) == 2
    for c in calls:
        kw = {k.arg for k in c.keywords}
        assert "now_et" in kw, f"app.py:{c.lineno} tone_state_for_page without now_et"
        assert not any(isinstance(k.value, ast.Constant) and k.value.value is None
                       for k in c.keywords if k.arg == "now_et")


def test_pin_app_live_read_raises_on_failure_so_a_failed_read_is_never_cached():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert src.count("= _cached_sp500_read()") == 3      # Signals, Analysis, Watchlist
    # the reader raises on a missing S&P row (an exception is never cached)
    i = src.index("def _cached_sp500_read")
    body = src[i:i + 900]
    assert "raise RuntimeError" in body and 'return {"pct"' in body
    assert "_math.isfinite(_pct)" in body             # a NaN is never cached either
    assert "import math as _math" in src[:400]
    assert "ttl=45" in src[i - 120:i]


def test_pin_tone_dicts_with_tone_available_carry_tone_read_at_producer_end():
    for rel in ("app.py", "stock_analyzer/headless_alert_engine.py"):
        tree = parse_file_cached(ROOT / rel)
        n_checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value: v for k, v in zip(node.keys, node.values)
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "tone_available" not in keys:
                continue
            v = keys["tone_available"]
            if isinstance(v, ast.Constant) and v.value is False:
                continue
            n_checked += 1
            assert "tone_read_at" in keys, (
                f"{rel}:{node.lineno} a readable-tone dict without tone_read_at")
        assert n_checked >= 1, rel


def test_pin_unavailable_tone_dict_literals_carry_a_why():
    tree = parse_file_cached(ROOT / "app.py")
    seen = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if {"tone", "sp500_pct", "available", "source", "as_of"} <= keys:
                seen += 1
                assert "why" in keys and "home_read_at" in keys, node.lineno
    assert seen >= 4

