"""stock_analyzer/debrief_freshness.py: is a stored Weekly Debrief still the same
figure Reports shows? (2026-11-10: the 11-08 debrief read -9.7% / -$673 after the
11-09 EOD repaired the 11-06 account row; Performance Review then read -7.67%.)
"""
from datetime import date

import pandas as pd
import pytest

from stock_analyzer import debrief_freshness as df_mod
from stock_analyzer.debrief_freshness import (
    account_caption, stale_warning_text, stored_debrief_freshness, window_start,
)

pytestmark = pytest.mark.fast

WEEK_ENDING = "2026-11-08"          # a Sunday; Monday is 2026-11-02
GENERATED = "2026-11-08T23:30:00+00:00"   # 18:30 ET on 11-08 (UTC evening)


_SAME = object()


def fresh(generated, week_ending, df, *, current=_SAME, excluded=(), flows=()):
    """The decision with the new inputs defaulted to "nothing found" (the current
    week = the stored week, no excluded rows, no flows)."""
    return stored_debrief_freshness(
        generated, week_ending, df,
        current_week_ending=week_ending if current is _SAME else current,
        excluded_dates=excluded, flows_rows=flows)


def _snaps(rows):
    return pd.DataFrame(rows, columns=["snapshot_date", "repaired_at"])


def test_window_starts_at_the_session_before_the_weeks_monday():
    assert window_start("2026-11-08") == date(2026, 10, 30)      # Fri before Mon 11-02
    assert window_start(date(2026, 11, 8)) == date(2026, 10, 30)
    assert window_start(pd.Timestamp("2026-11-08")) == date(2026, 10, 30)
    assert window_start("not a date") is None
    assert window_start(None) is None


def test_window_start_skips_a_holiday_friday_before_monday():
    from stock_analyzer.constants import NYSE_HOLIDAYS
    assert "2026-04-03" in NYSE_HOLIDAYS             # Good Friday
    # week ending Sun 2026-04-12 -> Monday 04-06 -> Fri 04-03 is closed -> Thu 04-02
    assert window_start("2026-04-12") == date(2026, 4, 2)


def test_changed_when_a_row_in_the_window_was_repaired_after_generation():
    df = _snaps([
        ("2026-11-05", None),
        ("2026-11-06", "2026-11-10T01:00:00+00:00"),   # the 11-09 EOD repair
        ("2026-11-09", None),
    ])
    out = fresh(GENERATED, WEEK_ENDING, df)
    assert out["state"] == "changed" and out["reasons"] == ["repair"]
    assert out["repaired_dates"] == ["2026-11-06"] and out["last_repaired_et"] == "2026-11-09"
    assert out["flow_dates"] == []


def test_changed_lists_every_repaired_date_and_the_latest_repair():
    df = _snaps([
        ("2026-11-04", "2026-11-09T22:00:00+00:00"),
        ("2026-11-06", "2026-11-11T15:00:00+00:00"),
    ])
    out = fresh(GENERATED, WEEK_ENDING, df)
    assert out["state"] == "changed"
    assert out["repaired_dates"] == ["2026-11-04", "2026-11-06"]
    assert out["last_repaired_et"] == "2026-11-11"


def test_same_when_no_row_was_ever_repaired():
    df = _snaps([("2026-11-05", None), ("2026-11-06", None)])
    out = fresh(GENERATED, WEEK_ENDING, df)
    assert out["state"] == "same"
    assert out["repaired_dates"] == [] and out["last_repaired_et"] is None and out["reasons"] == []


def test_same_when_the_repair_came_before_generation():
    df = _snaps([("2026-11-06", "2026-11-08T12:00:00+00:00")])   # before 23:30Z
    assert fresh(GENERATED, WEEK_ENDING, df)["state"] == "same"


def test_same_when_the_repair_is_dated_outside_the_window():
    df = _snaps([
        ("2026-10-29", "2026-11-10T01:00:00+00:00"),   # before the window opens (10-30)
        ("2026-11-09", "2026-11-10T01:00:00+00:00"),   # after week_ending
    ])
    assert fresh(GENERATED, WEEK_ENDING, df)["state"] == "same"


def test_window_edges_are_inclusive():
    for d in ("2026-10-30", "2026-11-08"):
        df = _snaps([(d, "2026-11-10T01:00:00+00:00")])
        assert fresh(GENERATED, WEEK_ENDING, df)["state"] == "changed", d


def test_empty_frame_with_the_column_is_same_without_the_column_is_unknown():
    assert fresh(GENERATED, WEEK_ENDING, _snaps([]))["state"] == "same"
    no_col = pd.DataFrame(columns=["snapshot_date", "gross_book"])
    assert fresh(GENERATED, WEEK_ENDING, no_col)["state"] == "unknown"


def test_unknown_when_nothing_can_be_checked():
    ok = _snaps([("2026-11-06", None)])
    for gen, we, df in (
        (GENERATED, WEEK_ENDING, None),                    # the read failed
        (None, WEEK_ENDING, ok),                           # generated_at missing
        ("garbage", WEEK_ENDING, ok),                      # generated_at unreadable
        (GENERATED, None, ok),                             # week_ending missing
        (GENERATED, "garbage", ok),                        # week_ending unreadable
        (GENERATED, WEEK_ENDING, pd.DataFrame({"snapshot_date": ["2026-11-06"]})),  # no column
    ):
        out = fresh(gen, we, df)
        assert out["state"] == "unknown" and out["repaired_dates"] == [], (gen, we)


def test_unreadable_repaired_at_in_the_window_is_unknown_not_same():
    df = _snaps([("2026-11-06", "not a timestamp")])
    assert fresh(GENERATED, WEEK_ENDING, df)["state"] == "unknown"
    # ... but a provable change elsewhere still wins
    df = _snaps([("2026-11-05", "not a timestamp"), ("2026-11-06", "2026-11-10T01:00:00+00:00")])
    assert fresh(GENERATED, WEEK_ENDING, df)["state"] == "changed"


def test_utc_evening_generation_is_compared_as_instants_not_dates():
    # generated 18:30 ET on 11-08 (= 23:30Z). A repair at 22:00Z the SAME UTC day is
    # earlier (same); one at 00:10Z the next UTC day is later (changed), even
    # though a date-only compare would call both "11-08" / "11-09" the wrong way.
    gen = "2026-11-08T23:30:00+00:00"
    early = _snaps([("2026-11-06", "2026-11-08T22:00:00+00:00")])
    late = _snaps([("2026-11-06", "2026-11-09T00:10:00+00:00")])
    assert fresh(gen, WEEK_ENDING, early)["state"] == "same"
    assert fresh(gen, WEEK_ENDING, late)["state"] == "changed"


def test_offset_repaired_at_is_compared_by_instant():
    # 2026-11-08 20:00-05:00 (EST) is 01:00Z on 11-09: after a 23:30Z generation,
    # although its wall-clock text "20:00" is numerically before "23:30".
    df = _snaps([("2026-11-06", "2026-11-08T20:00:00-05:00")])
    assert fresh(GENERATED, WEEK_ENDING, df)["state"] == "changed"
    # and 18:00-05:00 (23:00Z) is before 23:30Z
    df = _snaps([("2026-11-06", "2026-11-08T18:00:00-05:00")])
    assert fresh(GENERATED, WEEK_ENDING, df)["state"] == "same"


def test_dst_change_day_ambiguous_wall_time_uses_the_exact_instant():
    # 2026-11-01 is the fall-back day: 01:30 ET happens twice. A debrief generated
    # at 06:15Z (01:15 EST, the SECOND 01:xx) vs a repair at 05:30Z (01:30 EDT, the
    # FIRST 01:30): the repair is earlier in real time though its wall time is later.
    gen = "2026-11-01T06:15:00+00:00"
    df = _snaps([("2026-10-30", "2026-11-01T01:30:00-04:00")])      # = 05:30Z
    assert fresh(gen, "2026-11-01", df)["state"] == "same"
    df = _snaps([("2026-10-30", "2026-11-01T01:30:00-05:00")])      # = 06:30Z, later
    assert fresh(gen, "2026-11-01", df)["state"] == "changed"


def test_naive_generated_at_is_read_as_utc():
    df = _snaps([("2026-11-06", "2026-11-08T23:45:00+00:00")])
    assert fresh("2026-11-08T23:30:00", WEEK_ENDING, df)["state"] == "changed"
    assert fresh(pd.Timestamp("2026-11-08T23:50:00", tz="UTC"),
                                    WEEK_ENDING, df)["state"] == "same"


def test_nan_and_nat_repaired_at_read_as_never_repaired():
    df = pd.DataFrame({"snapshot_date": ["2026-11-05", "2026-11-06"],
                       "repaired_at": [float("nan"), pd.NaT]})
    assert fresh(GENERATED, WEEK_ENDING, df)["state"] == "same"


def test_snapshot_date_as_date_objects_works():
    df = pd.DataFrame({"snapshot_date": [date(2026, 11, 6)],
                       "repaired_at": ["2026-11-10T01:00:00+00:00"]})
    assert fresh(GENERATED, WEEK_ENDING, df)["state"] == "changed"


# -- wording ------------------------------------------------------------------------

def test_only_the_same_state_claims_the_figure_matches_performance_review():
    assert "same figure as" in account_caption({"state": "same"})
    assert "same figure as" not in account_caption({"state": "changed"})
    assert "same figure as" not in account_caption({"state": "unknown"})
    assert "measured when this debrief was generated" in account_caption({"state": "unknown"})
    assert "measured when this debrief was generated" not in account_caption({"state": "same"})
    for st in ("same", "changed", "unknown"):
        assert "SPY is unlevered" in account_caption(st)


def test_unknown_or_missing_state_never_claims_a_match():
    assert "same figure as" not in account_caption({"state": "something else"})


def test_stale_warning_text_only_for_changed():
    fr = {"state": "changed", "repaired_dates": ["2026-11-06"], "last_repaired_et": "2026-11-09"}
    txt = stale_warning_text(fr, "2026-11-08")
    assert txt == (
        "Recorded 2026-11-08, before a later repair of the account record for "
        "2026-11-06 (repaired 2026-11-09). These figures can differ from "
        "📄 Reports → Performance Review → Last week; press **Generate Now** to "
        "refresh this week."
    )
    for st in ("same", "unknown"):
        assert stale_warning_text({"state": st, "repaired_dates": [], "last_repaired_et": None},
                                  "2026-11-08") is None
    assert stale_warning_text(None, "2026-11-08") is None


def test_stale_warning_lists_several_dates():
    fr = {"state": "changed", "repaired_dates": ["2026-11-04", "2026-11-06"],
          "last_repaired_et": "2026-11-11"}
    assert "2026-11-04, 2026-11-06" in stale_warning_text(fr, "2026-11-08")


def test_stale_warning_escapes_dollar_signs():
    fr = {"state": "changed", "repaired_dates": ["a$b"], "last_repaired_et": "c$d"}
    txt = stale_warning_text(fr, "2026-11-08")
    assert "$" not in txt.replace("\\$", "")


# -- BLOCKING 1: a stored debrief for a different week than Performance Review shows --

def test_other_week_on_a_sunday_before_the_cron():
    # Sunday 2026-11-15 14:00 ET: resolve_period("last_week") already ends TODAY,
    # but the newest stored debrief is the week ending 11-08
    from stock_analyzer import period_attribution as pa
    _, cur = pa.resolve_period("last_week", date(2026, 11, 15))
    assert cur == date(2026, 11, 15)
    out = fresh(GENERATED, "2026-11-08", _snaps([("2026-11-06", None)]), current=cur)
    assert out["state"] == "other_week"
    assert out["week_ending"] == "2026-11-08" and out["current_week_ending"] == "2026-11-15"


def test_other_week_on_a_monday_after_a_missed_cron():
    from stock_analyzer import period_attribution as pa
    _, cur = pa.resolve_period("last_week", date(2026, 11, 16))   # Monday
    assert cur == date(2026, 11, 15)
    out = fresh(GENERATED, "2026-11-08", _snaps([("2026-11-06", None)]), current=cur)
    assert out["state"] == "other_week"


def test_matching_week_falls_through_to_the_normal_logic():
    from stock_analyzer import period_attribution as pa
    _, cur = pa.resolve_period("last_week", date(2026, 11, 9))    # Monday after
    assert cur == date(2026, 11, 8)
    assert fresh(GENERATED, "2026-11-08", _snaps([("2026-11-06", None)]), current=cur)["state"] == "same"
    rep = _snaps([("2026-11-06", "2026-11-10T01:00:00+00:00")])
    assert fresh(GENERATED, "2026-11-08", rep, current=cur)["state"] == "changed"


def test_other_week_wins_over_an_unreadable_check_and_an_unreadable_current_is_unknown():
    assert fresh(GENERATED, "2026-11-08", None, current="2026-11-15")["state"] == "other_week"
    assert fresh(GENERATED, "2026-11-08", _snaps([]), current=None)["state"] == "unknown"
    assert fresh(GENERATED, "2026-11-08", _snaps([]), current="garbage")["state"] == "unknown"


def test_other_week_caption_names_both_weeks_and_never_claims_a_match():
    out = fresh(GENERATED, "2026-11-08", _snaps([]), current="2026-11-15")
    cap = account_caption(out)
    assert "week ending 2026-11-08" in cap and "week ending 2026-11-15" in cap
    assert "same figure as" not in cap
    assert stale_warning_text(out, "2026-11-08") is None      # a caption, not a warning


# -- BLOCKING 2: rows Performance Review leaves out are invisible to repaired_at --

def test_an_excluded_date_inside_the_window_is_unknown():
    ok = _snaps([("2026-11-06", None)])
    assert fresh(GENERATED, WEEK_ENDING, ok, excluded=["2026-11-06"])["state"] == "unknown"
    assert fresh(GENERATED, WEEK_ENDING, ok, excluded=[date(2026, 10, 30)])["state"] == "unknown"  # edge
    assert fresh(GENERATED, WEEK_ENDING, ok, excluded=[{"snapshot_date": "2026-11-08",
                                                      "reason": "x"}])["state"] == "unknown"


def test_an_excluded_date_outside_the_window_is_unaffected():
    ok = _snaps([("2026-11-06", None)])
    assert fresh(GENERATED, WEEK_ENDING, ok, excluded=["2026-10-29", "2026-11-09"])["state"] == "same"
    assert fresh(GENERATED, WEEK_ENDING, ok, excluded=[])["state"] == "same"


def test_a_failed_exclusion_call_is_unknown():
    assert fresh(GENERATED, WEEK_ENDING, _snaps([("2026-11-06", None)]), excluded=None)["state"] == "unknown"


def test_a_provable_repair_still_wins_over_an_excluded_row():
    df = _snaps([("2026-11-06", "2026-11-10T01:00:00+00:00")])
    assert fresh(GENERATED, WEEK_ENDING, df, excluded=["2026-11-05"])["state"] == "changed"


def test_trades_unreadable_masks_every_provenance_row_so_the_state_is_unknown():
    # the real exclude_stale_rows with trades=None excludes every provenance row
    from stock_analyzer import account_row_trust as art
    acct = pd.DataFrame({
        "snapshot_date": ["2026-11-05", "2026-11-06"],
        "journal_max_trade_id": [100, 101],
        "repaired_at": [None, None],
    })
    _, excluded = art.exclude_stale_rows(acct, None)
    assert len(excluded) == 2
    out = fresh(GENERATED, WEEK_ENDING, acct, excluded=excluded)
    assert out["state"] == "unknown"
    assert "same figure as" not in account_caption(out)


# -- flows recorded after generation (Account % is money-weighted on them) --

def _flow(day, created, **kw):
    return {"id": 1, "flow_date": day, "flow_type": "deposit", "amount": 500.0,
            "created_at": created, **kw}


def test_a_flow_recorded_after_generation_in_the_window_is_changed():
    snaps = _snaps([("2026-11-06", None)])
    out = fresh(GENERATED, WEEK_ENDING, snaps,
                flows=[_flow("2026-11-04", "2026-11-09T14:00:00+00:00")])
    assert out["state"] == "changed" and out["reasons"] == ["flows"]
    assert out["flow_dates"] == ["2026-11-04"] and out["repaired_dates"] == []
    txt = stale_warning_text(out, "2026-11-08")
    assert "a deposit/withdrawal for 2026-11-04 was recorded after this debrief" in txt
    assert "repair" not in txt and "Generate Now" in txt
    assert "same figure as" not in account_caption(out)


def test_a_flow_recorded_before_generation_or_outside_the_window_is_same():
    snaps = _snaps([("2026-11-06", None)])
    flows = [
        _flow("2026-11-04", "2026-11-08T10:00:00+00:00"),    # recorded before generation
        _flow("2026-10-30", "2026-11-09T14:00:00+00:00"),    # dated at the window start: not (d0, d1]
        _flow("2026-11-09", "2026-11-09T14:00:00+00:00"),    # dated after week_ending
    ]
    assert fresh(GENERATED, WEEK_ENDING, snaps, flows=flows)["state"] == "same"
    assert fresh(GENERATED, WEEK_ENDING, snaps, flows=[])["state"] == "same"


def test_flows_unreadable_is_unknown():
    snaps = _snaps([("2026-11-06", None)])
    assert fresh(GENERATED, WEEK_ENDING, snaps, flows=None)["state"] == "unknown"
    # column missing on every row (a pre-column table)
    assert fresh(GENERATED, WEEK_ENDING, snaps,
                 flows=[{"flow_date": "2026-11-04", "amount": 5.0}])["state"] == "unknown"
    # a window flow with no readable created_at
    assert fresh(GENERATED, WEEK_ENDING, snaps,
                 flows=[_flow("2026-11-04", None, id=2), _flow("2026-11-05", "x", id=3)]
                 )["state"] == "unknown"


def test_repair_and_flows_together_say_both():
    snaps = _snaps([("2026-11-06", "2026-11-10T01:00:00+00:00")])
    out = fresh(GENERATED, WEEK_ENDING, snaps,
                flows=[_flow("2026-11-04", "2026-11-09T14:00:00+00:00")])
    assert out["reasons"] == ["repair", "flows"]
    txt = stale_warning_text(out, "2026-11-08")
    assert "a later repair of the account record for 2026-11-06 (repaired 2026-11-09)" in txt
    assert "a deposit/withdrawal for 2026-11-04 was recorded after this debrief" in txt


def test_flow_created_at_is_compared_as_an_instant():
    snaps = _snaps([("2026-11-06", None)])
    # 18:00-05:00 = 23:00Z, before the 23:30Z generation
    early = [_flow("2026-11-04", "2026-11-08T18:00:00-05:00")]
    late = [_flow("2026-11-04", "2026-11-08T19:00:00-05:00")]      # 00:00Z next day
    assert fresh(GENERATED, WEEK_ENDING, snaps, flows=early)["state"] == "same"
    assert fresh(GENERATED, WEEK_ENDING, snaps, flows=late)["state"] == "changed"
