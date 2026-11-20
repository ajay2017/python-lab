"""Pure tests for stock_analyzer.home_dock_digest: the one dated line the bottom dock
shows on every page but Home (dock C1b)."""
from datetime import datetime

import pytest

from stock_analyzer import coord_freshness as cf
from stock_analyzer import home_dock_digest as hdd
from stock_analyzer.market_time import ET

pytestmark = pytest.mark.fast


def _et(y, mo, d, h, mi):
    return ET.localize(datetime(y, mo, d, h, mi))


NOW = _et(2026, 11, 19, 21, 0)          # Thu 9:00 PM ET
BUILT_TODAY = _et(2026, 11, 19, 20, 46)


def _view(state="clear", active=None, n_active=0):
    return {"state": state, "active": active if active is not None else [], "n_active": n_active}


def _dg(act="clear", n_act=0, tier=None, headline=None, built=BUILT_TODAY):
    return {
        "built_at": built.isoformat() if built is not None else None,
        "act": act, "n_act": n_act,
        "notices_tier": tier, "notices_headline": headline,
    }


# ── build_digest ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("view", [None, "x", 3, [], {}, {"state": "weird"}, {"state": "offline", "active": [], "n_active": None}])
def test_unusable_or_offline_view_is_offline_with_no_count(view):
    d = hdd.build_digest(view, None, None, "2026-11-19T20:46:00-05:00")
    assert d["act"] == "offline" and d["n_act"] is None


def test_offline_never_reads_as_zero_even_with_stray_fields():
    d = hdd.build_digest({"state": "offline", "active": [{"t": 1}], "n_active": 4}, None, None, "x")
    assert d["act"] == "offline" and d["n_act"] is None


def test_active_items_make_it_act_with_the_int_count():
    d = hdd.build_digest(_view("act", [{"a": 1}, {"a": 2}], 2), None, None, "x")
    assert d["act"] == "act" and d["n_act"] == 2


def test_n_active_alone_makes_it_act_and_a_list_alone_does_too():
    assert hdd.build_digest(_view("act", [], 3), None, None, "x")["n_act"] == 3
    assert hdd.build_digest(_view("act", [{"a": 1}], None), None, None, "x") == {
        "built_at": "x", "act": "act", "n_act": 1, "notices_tier": None, "notices_headline": None}


def test_a_bool_is_not_a_count():
    d = hdd.build_digest(_view("act", [], True), None, None, "x")
    assert d["act"] == "offline"          # "act" with nothing to count is not a calm all-clear


def test_clear_when_checked_and_nothing_active():
    d = hdd.build_digest(_view("clear"), None, None, "x")
    assert d["act"] == "clear" and d["n_act"] == 0


def test_act_state_without_any_active_evidence_is_not_called_clear():
    d = hdd.build_digest(_view("act", [], 0), None, None, "x")
    assert d["act"] == "offline" and d["n_act"] is None


def test_no_holdings_is_its_own_state():
    d = hdd.build_digest(None, None, None, "x", no_holdings=True)
    assert d["act"] == "no_holdings" and d["n_act"] is None


def test_notices_are_normalized_and_halves_never_stand_alone():
    s = {"tier": "warning", "named": ["Broker drift"], "n_notes": 0}
    d = hdd.build_digest(_view(), s, "⚠️ Needs a look: **Broker drift**", "x")
    assert d["notices_tier"] == "warning" and d["notices_headline"].startswith("⚠️ Needs")
    d = hdd.build_digest(_view(), s, None, "x")
    assert d["notices_tier"] is None and d["notices_headline"] is None
    d = hdd.build_digest(_view(), {"tier": "bogus"}, "· 2 notes", "x")
    assert d["notices_tier"] is None and d["notices_headline"] is None
    d = hdd.build_digest(_view(), None, None, 123)
    assert d["built_at"] is None


# ── digest_line ──────────────────────────────────────────────────────────────

def test_none_digest_is_an_info_line_that_never_says_all_clear():
    line = hdd.digest_line(None, cf.ABSENT, NOW)
    assert line == {"tier": "info", "text": (
        "🏠 Home's latest build didn't finish (or hasn't run) this session, so Act "
        "Today and notices weren't checked here. Open 🏠 Home to check.")}
    assert "nothing to act on" not in line["text"]
    assert hdd.digest_line("junk", cf.FRESH, NOW)["text"] == line["text"]


def test_act_count_is_an_error_line_with_the_time():
    line = hdd.digest_line(_dg("act", 3), cf.FRESH, NOW)
    assert line["tier"] == "error"
    assert line["text"] == "🏠 Home at 8:46 PM ET: 🔴 3 Act Today · no notices"


def test_clear_with_no_notices_is_plain_info():
    line = hdd.digest_line(_dg("clear", 0), cf.FRESH, NOW)
    assert line == {"tier": "info", "text": "🏠 Home at 8:46 PM ET: ✅ nothing to act on · no notices"}


def test_offline_says_not_checked_and_is_a_warning_and_never_zero():
    line = hdd.digest_line(_dg("offline", None), cf.FRESH, NOW)
    assert line["tier"] == "warning"
    assert "Act Today was NOT checked (the Brief couldn't be built)" in line["text"]
    assert "0 Act Today" not in line["text"]


def test_no_holdings_wording():
    line = hdd.digest_line(_dg("no_holdings", None), cf.FRESH, NOW)
    assert line["text"] == "🏠 Home at 8:46 PM ET: no holdings yet · no notices"
    assert line["tier"] == "info"


def test_quiet_notes_form_reads_once_without_a_double_separator():
    line = hdd.digest_line(_dg("clear", 0, "info", "· 4 notes"), cf.FRESH, NOW)
    assert line["text"].endswith("✅ nothing to act on · 4 notes")
    assert "· ·" not in line["text"]
    assert line["tier"] == "info"


def test_notice_tiers_raise_the_line_tier():
    named = "⚠️ Needs a look: **Broker drift**"
    assert hdd.digest_line(_dg("clear", 0, "warning", named), cf.FRESH, NOW)["tier"] == "warning"
    assert hdd.digest_line(_dg("clear", 0, "error", named), cf.FRESH, NOW)["tier"] == "error"
    # an offline Act Today with an error notice is the worse of the two
    assert hdd.digest_line(_dg("offline", None, "error", named), cf.FRESH, NOW)["tier"] == "error"
    assert named in hdd.digest_line(_dg("clear", 0, "warning", named), cf.FRESH, NOW)["text"]


def test_stale_epoch_appends_the_trade_caveat_and_raises_info_to_warning():
    line = hdd.digest_line(_dg("clear", 0), cf.STALE, NOW)
    assert line["tier"] == "warning"
    assert line["text"].endswith(
        " · may be out of date: the book changed since (a trade or holdings update)")


def test_stale_never_lowers_an_error():
    assert hdd.digest_line(_dg("act", 2), cf.STALE, NOW)["tier"] == "error"


def test_built_yesterday_shows_the_date_and_the_earlier_day_caveat():
    built = _et(2026, 11, 18, 20, 46)
    line = hdd.digest_line(_dg("clear", 0, built=built), cf.FRESH, NOW)
    assert line["text"].startswith("🏠 Home at Nov 18, 8:46 PM ET: ")
    assert line["text"].endswith(" · may be out of date: it is from an earlier day")
    assert line["tier"] == "warning"


def test_built_before_a_weekend_is_an_earlier_day():
    built = _et(2026, 11, 20, 16, 30)                 # Fri
    monday = _et(2026, 11, 23, 9, 35)
    line = hdd.digest_line(_dg("clear", 0, built=built), cf.FRESH, monday)
    assert "Nov 20, 4:30 PM ET" in line["text"]
    assert "it is from an earlier day" in line["text"]


def test_a_session_spanning_midnight_uses_the_et_date_not_utc():
    # 11:30 PM ET is already the next UTC day; both timestamps are the same ET day.
    built = _et(2026, 11, 19, 23, 30)
    now = _et(2026, 11, 19, 23, 50)
    line = hdd.digest_line(_dg("clear", 0, built=built), cf.FRESH, now.astimezone(ET))
    assert "Nov" not in line["text"] and "11:30 PM ET" in line["text"]
    assert "earlier day" not in line["text"]
    # built just before ET midnight, read just after: an earlier day
    after = _et(2026, 11, 20, 0, 10)
    assert "it is from an earlier day" in hdd.digest_line(
        _dg("clear", 0, built=built), cf.FRESH, after)["text"]


def test_dst_change_day_formats_the_clock_in_et():
    # Sun 2026-11-01 DST ends: 1:30 AM EST (second pass) is a valid ET label.
    built = ET.localize(datetime(2026, 11, 1, 1, 30), is_dst=False)
    now = ET.localize(datetime(2026, 11, 1, 9, 0), is_dst=False)
    line = hdd.digest_line(_dg("clear", 0, built=built), cf.FRESH, now)
    assert "1:30 AM ET" in line["text"]
    # a UTC-offset ISO string converts to the same ET label
    d = _dg("clear", 0)
    d["built_at"] = "2026-11-01T06:30:00+00:00"        # 1:30 AM EST
    assert "1:30 AM ET" in hdd.digest_line(d, cf.FRESH, now)["text"]
    # spring forward: 2026-03-08 3:05 AM EDT
    spring = ET.localize(datetime(2026, 3, 8, 3, 5))
    line = hdd.digest_line(_dg("clear", 0, built=spring), cf.FRESH,
                           ET.localize(datetime(2026, 3, 8, 12, 0)))
    assert "3:05 AM ET" in line["text"]


def test_unparsable_built_at_is_stale_with_no_time():
    for bad in (None, "", "not a date", "2026-11-19T20:46:00"):   # last: no zone
        d = _dg("clear", 0)
        d["built_at"] = bad
        line = hdd.digest_line(d, cf.FRESH, NOW)
        assert line["text"].startswith("🏠 Home at an unknown time: ")
        assert "may be out of date" in line["text"]
        assert line["tier"] == "warning"


def test_absent_stamp_with_a_digest_is_treated_as_stale():
    line = hdd.digest_line(_dg("clear", 0), cf.ABSENT, NOW)
    assert line["tier"] == "warning"
    assert "may be out of date: it isn't marked current" in line["text"]


def test_fresh_same_day_has_no_caveat():
    assert "may be out of date" not in hdd.digest_line(_dg("clear", 0), cf.FRESH, NOW)["text"]


def test_text_is_plain_markdown_without_html_or_dollar_signs():
    for d in (_dg("act", 2, "warning", "⚠️ Needs a look: **Broker drift** · 1 more note"),
              _dg("offline", None), _dg("no_holdings", None), _dg("clear", 0, "info", "· 3 notes")):
        for fresh in (cf.FRESH, cf.STALE, cf.ABSENT):
            text = hdd.digest_line(d, fresh, NOW)["text"]
            assert "<" not in text and "$" not in text
    assert "<" not in hdd.NO_DIGEST_TEXT and "$" not in hdd.NO_DIGEST_TEXT


def test_naive_now_is_read_as_et_and_junk_now_degrades_to_unknown_time():
    naive_now = datetime(2026, 11, 19, 21, 0)
    assert hdd.digest_line(_dg("clear", 0), cf.FRESH, naive_now)["text"].startswith(
        "🏠 Home at 8:46 PM ET: ")
    assert "unknown time" in hdd.digest_line(_dg("clear", 0), cf.FRESH, None)["text"]


def test_unknown_act_value_or_act_without_a_count_reads_not_checked():
    assert "NOT checked" in hdd.digest_line(_dg("???", None), cf.FRESH, NOW)["text"]
    assert "NOT checked" in hdd.digest_line(_dg("act", None), cf.FRESH, NOW)["text"]
