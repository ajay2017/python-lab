"""Tests for stock_analyzer/catalyst_watch.py — forward earnings-awareness
row builder (`build_catalyst_watch`) and its two small parsing helpers
(`_parse_date`, `_when_label`). Pure logic, no I/O. Previously zero test
coverage.
"""
from datetime import date

from stock_analyzer import catalyst_watch as cw


# ─── _parse_date ─────────────────────────────────────────────────────────────

def test_parse_date_valid_string_returns_date():
    assert cw._parse_date("2026-08-01") == date(2026, 8, 1)


def test_parse_date_extra_trailing_chars_still_parses_via_slice():
    assert cw._parse_date("2026-08-01T00:00:00Z") == date(2026, 8, 1)


def test_parse_date_none_returns_none():
    assert cw._parse_date(None) is None


def test_parse_date_empty_string_returns_none():
    assert cw._parse_date("") is None


def test_parse_date_unparseable_returns_none():
    assert cw._parse_date("not-a-date") is None


# ─── _when_label ─────────────────────────────────────────────────────────────

def test_when_label_before_open_synonyms():
    for w in ("bmo", "before market open", "pre", "premarket"):
        assert cw._when_label(w) == "before open"


def test_when_label_after_close_synonyms():
    for w in ("amc", "after market close", "post", "aftermarket"):
        assert cw._when_label(w) == "after close"


def test_when_label_case_insensitive():
    assert cw._when_label("BMO") == "before open"
    assert cw._when_label("AMC") == "after close"


def test_when_label_unrecognized_or_missing_returns_empty_string():
    assert cw._when_label("unknown") == ""
    assert cw._when_label("") == ""
    assert cw._when_label(None) == ""


# ─── build_catalyst_watch — builders ────────────────────────────────────────

TODAY = date(2026, 7, 29)


def _row(ticker, d, when=""):
    return {"ticker": ticker, "date": d, "when": when}


def _base_kwargs(**overrides):
    kwargs = dict(
        tracked=set(),
        held_tickers=set(),
        watchlist=set(),
        sector_lookup={},
        calendar_rows=[],
        held_earnings={},
        leading_sector_names=set(),
        today=TODAY,
        window_days=14,
    )
    kwargs.update(overrides)
    return kwargs


# ─── untracked ticker dropped ────────────────────────────────────────────────

def test_build_catalyst_watch_untracked_ticker_is_dropped():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        calendar_rows=[_row("BBB", "2026-08-01")],
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result == []


# ─── same-ticker duplicate calendar rows: earliest date kept ────────────────

def test_build_catalyst_watch_duplicate_rows_keeps_earliest_date():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        calendar_rows=[
            _row("AAA", "2026-08-05"),
            _row("AAA", "2026-08-01"),
        ],
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert len(result) == 1
    assert result[0]["date"] == "2026-08-01"


# ─── held_earnings fallback ──────────────────────────────────────────────────

def test_build_catalyst_watch_held_earnings_fallback_only_applies_to_held():
    kwargs = _base_kwargs(
        tracked={"AAA", "BBB"},
        held_tickers={"AAA"},
        held_earnings={"AAA": "2026-08-01", "BBB": "2026-08-01"},
    )
    result = cw.build_catalyst_watch(**kwargs)
    tickers = {r["ticker"] for r in result}
    assert "AAA" in tickers
    assert "BBB" not in tickers  # not held -- held_earnings fallback ignored


def test_build_catalyst_watch_held_earnings_fallback_overrides_only_when_earlier():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        held_tickers={"AAA"},
        calendar_rows=[_row("AAA", "2026-08-10")],
        held_earnings={"AAA": "2026-08-01"},  # earlier than market calendar
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result[0]["date"] == "2026-08-01"


def test_build_catalyst_watch_held_earnings_fallback_ignored_when_later():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        held_tickers={"AAA"},
        calendar_rows=[_row("AAA", "2026-08-01")],
        held_earnings={"AAA": "2026-08-10"},  # later -- market calendar wins
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result[0]["date"] == "2026-08-01"


# ─── days window boundaries ──────────────────────────────────────────────────

def test_build_catalyst_watch_days_zero_today_is_included():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        calendar_rows=[_row("AAA", TODAY.isoformat())],
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert len(result) == 1
    assert result[0]["days"] == 0


def test_build_catalyst_watch_days_negative_one_already_passed_excluded():
    yesterday = date(2026, 7, 28)
    kwargs = _base_kwargs(
        tracked={"AAA"},
        calendar_rows=[_row("AAA", yesterday.isoformat())],
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result == []


def test_build_catalyst_watch_days_exactly_at_window_days_included():
    d = date(2026, 8, 12)  # TODAY + 14
    assert (d - TODAY).days == 14
    kwargs = _base_kwargs(
        tracked={"AAA"},
        calendar_rows=[_row("AAA", d.isoformat())],
        window_days=14,
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert len(result) == 1
    assert result[0]["days"] == 14


def test_build_catalyst_watch_days_one_past_window_days_excluded():
    d = date(2026, 8, 13)  # TODAY + 15
    assert (d - TODAY).days == 15
    kwargs = _base_kwargs(
        tracked={"AAA"},
        calendar_rows=[_row("AAA", d.isoformat())],
        window_days=14,
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result == []


# ─── ownership priority: held > watchlist > universe ───────────────────────

def test_build_catalyst_watch_ownership_held_wins_over_watchlist():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        held_tickers={"AAA"},
        watchlist={"AAA"},
        calendar_rows=[_row("AAA", "2026-08-01")],
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result[0]["ownership"] == "held"


def test_build_catalyst_watch_ownership_watchlist_wins_over_universe():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        watchlist={"AAA"},
        calendar_rows=[_row("AAA", "2026-08-01")],
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result[0]["ownership"] == "watchlist"


def test_build_catalyst_watch_ownership_defaults_to_universe():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        calendar_rows=[_row("AAA", "2026-08-01")],
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result[0]["ownership"] == "universe"


# ─── sector_hot flag ─────────────────────────────────────────────────────────

def test_build_catalyst_watch_sector_hot_true_when_sector_leading():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        sector_lookup={"AAA": "Semiconductors"},
        calendar_rows=[_row("AAA", "2026-08-01")],
        leading_sector_names={"Semiconductors"},
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result[0]["sector_hot"] is True


def test_build_catalyst_watch_sector_hot_false_when_sector_not_leading():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        sector_lookup={"AAA": "Financials"},
        calendar_rows=[_row("AAA", "2026-08-01")],
        leading_sector_names={"Semiconductors"},
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result[0]["sector_hot"] is False


def test_build_catalyst_watch_sector_hot_false_when_sector_empty():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        sector_lookup={},  # sector defaults to ""
        calendar_rows=[_row("AAA", "2026-08-01")],
        leading_sector_names={""},  # even if "" is somehow "leading"
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result[0]["sector_hot"] is False


# ─── final sort order: by days, then ticker alphabetically ────────────────

def test_build_catalyst_watch_sorted_by_days_then_ticker_alpha():
    kwargs = _base_kwargs(
        tracked={"ZZZ", "AAA", "MMM"},
        calendar_rows=[
            _row("ZZZ", "2026-08-01"),  # same day as AAA
            _row("AAA", "2026-08-01"),
            _row("MMM", "2026-08-05"),  # later day
        ],
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert [r["ticker"] for r in result] == ["AAA", "ZZZ", "MMM"]


# ─── when label passthrough ─────────────────────────────────────────────────

def test_build_catalyst_watch_when_label_normalized_in_output():
    kwargs = _base_kwargs(
        tracked={"AAA"},
        calendar_rows=[_row("AAA", "2026-08-01", when="BMO")],
    )
    result = cw.build_catalyst_watch(**kwargs)
    assert result[0]["when"] == "before open"
