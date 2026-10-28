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
