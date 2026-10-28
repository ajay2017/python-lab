import pandas as pd

from stock_analyzer import thesis_review_status as trs


def _df(rows):
    return pd.DataFrame(rows, columns=["ticker", "status", "reviewed_at"])


def test_none_stays_none():
    assert trs.broken_tickers(None) is None
    assert trs.held_summary(None, {"AAA"}) is None


def test_empty_df():
    e = _df([])
    assert trs.broken_tickers(e) == set()
    assert trs.held_summary(e, {"AAA"}) == {
        "n_reviewed": 0, "n_weakening": 0, "n_broken": 0, "bad_names": []}


def test_latest_review_wins():
    df = _df([
        ("AAA", "BROKEN", "2026-10-01"), ("AAA", "INTACT", "2026-10-10"),
        ("BBB", "INTACT", "2026-10-01"), ("BBB", "BROKEN", "2026-10-10"),
    ])
    assert trs.broken_tickers(df) == {"BBB"}


def test_lowercase_ticker_upper_cased():
    df = _df([("aaa", "BROKEN", "2026-10-10")])
    assert trs.broken_tickers(df) == {"AAA"}


def test_held_filter_excludes_unheld():
    df = _df([("AAA", "BROKEN", "2026-10-10"), ("ZZZ", "BROKEN", "2026-10-10")])
    s = trs.held_summary(df, ["aaa"])
    assert s["n_reviewed"] == 1 and s["n_broken"] == 1
    assert s["bad_names"] == ["AAA"]


def test_bad_names_broken_before_weakening():
    df = _df([
        ("WWW", "WEAKENING", "2026-10-12"), ("BBB", "BROKEN", "2026-10-10"),
        ("III", "INTACT", "2026-10-11"),
    ])
    s = trs.held_summary(df, {"WWW", "BBB", "III"})
    assert s["bad_names"] == ["BBB", "WWW"]
    assert (s["n_reviewed"], s["n_weakening"], s["n_broken"]) == (3, 1, 1)


def test_act_today_note():
    assert trs.act_today_note(None, 2) == trs.UNCHECKED_ACT_NOTE
    assert trs.act_today_note(None, 0) is None
    assert trs.act_today_note(set(), 2) is None


def test_awareness_note_fires_only_when_act_empty_and_aware_present():
    assert trs.awareness_note(None, 0, 3) == trs.UNCHECKED_ACT_NOTE
    assert trs.awareness_note(None, 2, 3) is None
    assert trs.awareness_note(None, 1, 0) is None
    assert trs.awareness_note(set(), 0, 3) is None
    assert trs.awareness_note({"AAA"}, 0, 3) is None
    assert trs.awareness_note(None, 0, 0) is None


def test_act_and_awareness_notes_fire_exactly_once_when_offline():
    # Once-per-page promise: never both; one fires iff offline and any card shows.
    for broken in (None, set(), {"A"}):
        for n_act in (0, 1):
            for n_aware in (0, 1):
                act = trs.act_today_note(broken, n_act)
                aw = trs.awareness_note(broken, n_act, n_aware)
                assert not (act and aw)
                assert bool(act or aw) == (broken is None and n_act + n_aware > 0)


def test_debrief_broken_three_state():
    assert trs.debrief_broken(None, {"AAA"}) is None
    assert trs.debrief_broken(_df([]), {"AAA"}) == []


def test_debrief_broken_uses_latest_review_only():
    df = _df([
        ("AAA", "BROKEN", "2026-10-01"), ("AAA", "INTACT", "2026-10-10"),
        ("BBB", "INTACT", "2026-10-01"), ("BBB", "BROKEN", "2026-10-10"),
    ])
    assert trs.debrief_broken(df, None) == ["BBB"]


def test_debrief_broken_excludes_sold_names_when_held_given():
    df = _df([("AAA", "BROKEN", "2026-10-01"), ("BBB", "BROKEN", "2026-10-01")])
    assert trs.debrief_broken(df, {"BBB"}) == ["BBB"]
    assert trs.debrief_broken(df, set()) == []


def test_debrief_broken_held_none_means_no_filter_and_sorted():
    df = _df([("ZZZ", "BROKEN", "2026-10-01"), ("AAA", "BROKEN", "2026-10-01")])
    assert trs.debrief_broken(df, None) == ["AAA", "ZZZ"]


def test_debrief_broken_mixed_case_tickers():
    df = _df([("aaa", "BROKEN", "2026-10-01"), ("AAA", "INTACT", "2026-10-10"),
              ("bbb", "BROKEN", "2026-10-01")])
    assert trs.debrief_broken(df, ["Bbb", "aaa"]) == ["BBB"]


def test_debrief_broken_missing_status_column_is_unchecked():
    df = pd.DataFrame([{"ticker": "AAA", "reviewed_at": "2026-08-01"}])
    assert trs.debrief_broken(df, {"AAA"}) is None
