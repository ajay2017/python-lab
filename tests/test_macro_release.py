"""Tests for stock_analyzer/macro_release.py (same-day release timing)."""
from datetime import date, datetime, timezone

import pytest

from stock_analyzer.macro_release import refresh_macro_card, release_passed, released_text

pytestmark = pytest.mark.fast

D = date(2026, 10, 29)


def _now(h, m=0, tz=None):
    return datetime(2026, 10, 29, h, m, tzinfo=tz)


def test_future_day_is_false():
    assert release_passed(date(2026, 10, 30), "08:30", _now(12)) is False
    assert release_passed(date(2026, 10, 30), None, _now(12)) is False


def test_past_day_is_true_even_with_unknown_time():
    # a memoized Brief carried past midnight
    assert release_passed(date(2026, 10, 28), "14:00", _now(1)) is True
    assert release_passed(date(2026, 10, 28), None, _now(1)) is True
    card = _card(_event_date=date(2026, 10, 28), _event_time_et=None)
    out = refresh_macro_card(card, _now(1))
    assert out["directive"].startswith("Released —")


def test_before_at_after():
    assert release_passed(D, "08:30", _now(8, 29)) is False
    assert release_passed(D, "08:30", _now(8, 30)) is True
    assert release_passed(D, "08:30", _now(12)) is True


@pytest.mark.parametrize("bad", [None, "", "8", "25:00", "08:60", "ab:cd", 830, "08:30:00"])
def test_bad_time_is_none(bad):
    assert release_passed(D, bad, _now(12)) is None


def test_aware_now_is_converted_to_et():
    # 15:00 UTC on 2026-10-29 is 11:00 ET (EDT) -> before a 14:00 release
    assert release_passed(D, "14:00", _now(15, tz=timezone.utc)) is False
    # standard time: 20:00 UTC = 15:00 ET (after), 18:00 UTC = 13:00 ET (before)
    nov = date(2026, 11, 2)
    assert release_passed(nov, "14:00", datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)) is True
    assert release_passed(nov, "14:00", datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)) is False


def test_naive_now_used_as_et():
    assert release_passed(D, "14:00", _now(15)) is True


def test_fed_policy_card_says_entries_stay_held_others_do_not():
    fed = refresh_macro_card(_card(_event_category="Fed Policy"), _now(12))
    assert fed["trigger"].startswith("Released: check how it landed")
    assert fed["trigger"].endswith("New entries in affected names stay held today.")
    other = refresh_macro_card(_card(_event_category="Economic"), _now(12))
    assert "stay held" not in other["trigger"]


def test_fed_card_carried_past_midnight_does_not_say_held_today():
    # the entry gate holds a Fed day's names only on the event day itself
    card = _card(_event_category="Fed Policy", _event_date=date(2026, 10, 28),
                 _event_time_et="14:00")
    out = refresh_macro_card(card, _now(1))
    assert out["trigger"].startswith("Released: check how it landed")
    assert "stay held" not in out["trigger"]


def test_post_rules_caption_never_says_morning():
    from stock_analyzer.macro_release import post_rules_caption
    assert "morning" not in post_rules_caption(False, "14:00")
    assert "when the release drops (14:00 ET)" in post_rules_caption(False, "14:00")


def test_pre_event_view_and_post_rules_caption():
    from stock_analyzer.macro_release import post_rules_caption, pre_event_view
    live, done = pre_event_view(False), pre_event_view(True)
    assert live["protect_delta_on"] and live["auto_expand_ok"] and live["header_suffix"] == ""
    assert not done["protect_delta_on"] and not done["auto_expand_ok"] and "pre-release" in done["header_suffix"]
    assert "(14:00 ET)" in post_rules_caption(False, "14:00")
    assert "(" not in post_rules_caption(False, None)
    assert "apply these rules now" in post_rules_caption(True, "14:00")


def test_released_text():
    t = released_text("08:30")
    assert t == "Released at 08:30 ET — see 📅 Economic Calendar → 📊 Post-Event Results."


def _card(**kw):
    c = {"kind": "macro", "directive": "Hold through the print", "trigger": "x",
         "_event_date": D, "_event_time_et": "08:30"}
    c.update(kw)
    return c


def test_refresh_swaps_after_release_on_copy():
    c = _card()
    out = refresh_macro_card(c, _now(12))
    assert out is not c
    assert out["directive"].startswith("Released at 08:30 ET")
    assert out["trigger"].startswith("Released: check how it landed")
    assert c["directive"] == "Hold through the print"


def test_refresh_unchanged_before_release():
    c = _card()
    assert refresh_macro_card(c, _now(8)) is c


def test_refresh_unchanged_unknown_time():
    c = _card(_event_time_et=None)
    assert refresh_macro_card(c, _now(12)) is c


def test_refresh_unchanged_non_macro():
    c = _card(kind="stop_breach")
    assert refresh_macro_card(c, _now(12)) is c


@pytest.mark.parametrize("junk", [None, 5, "x", [], {}, {"kind": "macro"}])
def test_refresh_never_raises(junk):
    assert refresh_macro_card(junk, _now(12)) is junk


def test_refresh_never_raises_on_bad_now():
    c = _card()
    assert refresh_macro_card(c, None) is c


# ── release_signature: the Home Brief memo piece ─────────────────────────────
def _sig_events(d):
    return [
        {"event": "GDP", "date": d, "time_et": "08:30", "impact": "HIGH"},
        {"event": "Minor", "date": d, "time_et": "08:30", "impact": "LOW"},
        {"event": "Other day", "date": d.replace(day=d.day + 1) if d.day < 28 else d, "time_et": "08:30", "impact": "HIGH"},
    ]


def test_release_signature_changes_when_release_passes():
    import datetime as dt
    from stock_analyzer.macro_release import release_signature
    d = dt.date(2026, 11, 4)
    ev = _sig_events(d)
    before = release_signature(ev, d, dt.datetime(2026, 11, 4, 8, 29))
    after = release_signature(ev, d, dt.datetime(2026, 11, 4, 8, 30))
    assert before != after


def test_release_signature_same_when_time_passes_without_release():
    import datetime as dt
    from stock_analyzer.macro_release import release_signature
    d = dt.date(2026, 11, 4)
    ev = _sig_events(d)
    a = release_signature(ev, d, dt.datetime(2026, 11, 4, 6, 0))
    b = release_signature(ev, d, dt.datetime(2026, 11, 4, 8, 29))
    assert a == b
    c = release_signature(ev, d, dt.datetime(2026, 11, 4, 9, 0))
    e = release_signature(ev, d, dt.datetime(2026, 11, 4, 15, 0))
    assert c == e


def test_release_signature_ignores_low_impact_and_other_days_and_bad_input():
    import datetime as dt
    from stock_analyzer.macro_release import release_signature
    d = dt.date(2026, 11, 4)
    sig = release_signature(_sig_events(d), d, dt.datetime(2026, 11, 4, 9, 0))
    assert len(sig) == 1
    assert release_signature(None, d, dt.datetime(2026, 11, 4, 9, 0)) == frozenset()
    assert release_signature(["x", 3], d, dt.datetime(2026, 11, 4, 9, 0)) == frozenset()
