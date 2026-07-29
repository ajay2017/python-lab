"""Tests for stock_analyzer/broker_import.py — the Robinhood CSV statement
importer (parsing + dedup classification). Pure parsing/classification, no
Streamlit or DB. Previously zero test coverage.
"""
import datetime
import io

import pandas as pd

from stock_analyzer import broker_import as bi


_HEADER = "Activity Date,Instrument,Trans Code,Quantity,Price,Description\n"


def _csv(body: str) -> io.StringIO:
    return io.StringIO(_HEADER + body)


# ─── _money_to_float ──────────────────────────────────────────────────────────

def test_money_to_float_none_returns_none():
    assert bi._money_to_float(None) is None


def test_money_to_float_plain_number():
    assert bi._money_to_float("123.45") == 123.45


def test_money_to_float_dollar_sign_and_commas():
    assert bi._money_to_float("$1,234.56") == 1234.56


def test_money_to_float_parenthesized_is_absolute_value():
    assert bi._money_to_float("(518.00)") == 518.0


def test_money_to_float_unparseable_returns_none():
    assert bi._money_to_float("not-a-number") is None


# ─── _parse_date ──────────────────────────────────────────────────────────────

def test_parse_date_valid_m_d_yyyy_not_zero_padded():
    assert bi._parse_date("1/15/2026") == datetime.date(2026, 1, 15)


def test_parse_date_none_returns_none():
    assert bi._parse_date(None) is None


def test_parse_date_unparseable_returns_none():
    assert bi._parse_date("not-a-date") is None


# ─── _parse_company ───────────────────────────────────────────────────────────

def test_parse_company_strips_cusip_line():
    assert bi._parse_company("Apple Inc\nCUSIP: 037833100") == "Apple Inc"


def test_parse_company_no_cusip_line_returns_whole_first_line():
    assert bi._parse_company("Just A Company Name") == "Just A Company Name"


def test_parse_company_non_string_input_returns_empty_string():
    assert bi._parse_company(None) == ""
    assert bi._parse_company(12345) == ""


# ─── parse_robinhood_csv — header validation ─────────────────────────────────

def test_parse_robinhood_csv_missing_required_columns_returns_error():
    csv = io.StringIO("Date,Symbol,Type\n1/1/2026,AAPL,BUY\n")
    result = bi.parse_robinhood_csv(csv)
    assert result["error"] is not None
    assert result["trades"].empty
    assert result["invalid"].empty
    assert result["skipped"] == {}


def test_parse_robinhood_csv_unparseable_file_returns_error():
    # Unterminated quoted field triggers a real pandas ParserError.
    csv = io.StringIO(_HEADER + '1/15/2026,AAPL,BUY,10,150.00,"Unterminated quote\n')
    result = bi.parse_robinhood_csv(csv)
    assert result["error"] is not None
    assert result["trades"].empty


# ─── parse_robinhood_csv — skip / trade / invalid classification ────────────

def test_parse_robinhood_csv_non_trade_codes_counted_in_skipped():
    csv = _csv(
        '1/17/2026,AAPL,CDIV,,,"Apple Inc"\n'
        '1/18/2026,CASH,ACH,,,"Cash Transfer"\n'
    )
    result = bi.parse_robinhood_csv(csv)
    assert result["skipped"] == {"CDIV": 1, "ACH": 1}
    assert result["trades"].empty


def test_parse_robinhood_csv_valid_buy_sell_rows_land_in_trades():
    csv = _csv(
        '1/15/2026,AAPL,BUY,10,150.00,"Apple Inc\nCUSIP: 037833100"\n'
        '1/16/2026,MSFT,SELL,5,300.00,"Microsoft Corp\nCUSIP: 594918104"\n'
    )
    result = bi.parse_robinhood_csv(csv)
    assert result["error"] is None
    assert len(result["trades"]) == 2
    row = result["trades"].iloc[0]
    assert row["ticker"] == "AAPL"
    assert row["action"] == "BUY"
    assert row["shares"] == 10.0
    assert row["price"] == 150.0
    assert row["company"] == "Apple Inc"


def test_parse_robinhood_csv_row_failing_multiple_checks_joins_all_reasons():
    csv = _csv('not-a-date,AAPL,BUY,-5,-10.00,"Bad Co"\n')
    result = bi.parse_robinhood_csv(csv)
    assert len(result["invalid"]) == 1
    reason = result["invalid"].iloc[0]["reason"]
    assert "unparseable date" in reason
    assert "shares" in reason
    assert "price" in reason
    assert reason.count(";") == 2  # 3 reasons joined by "; "


def test_parse_robinhood_csv_zero_shares_invalid():
    csv = _csv('1/15/2026,AAPL,BUY,0,150.00,"Apple Inc"\n')
    result = bi.parse_robinhood_csv(csv)
    assert len(result["invalid"]) == 1
    assert result["trades"].empty


def test_parse_robinhood_csv_blank_junk_rows_dropped_before_classification():
    csv = _csv(
        '1/15/2026,AAPL,BUY,10,150.00,"Apple Inc"\n'
        ',,,,,\n'  # entirely blank row -- dropped, not counted anywhere
    )
    result = bi.parse_robinhood_csv(csv)
    assert len(result["trades"]) == 1
    assert result["skipped"] == {}
    assert result["invalid"].empty


# ─── classify_against_existing ───────────────────────────────────────────────

def _candidates(rows):
    cols = ["ticker", "action", "shares", "price", "activity_date", "company"]
    return pd.DataFrame(rows, columns=cols)


def _existing_trades(rows):
    cols = ["ticker", "action", "shares", "price", "traded_at"]
    return pd.DataFrame(rows, columns=cols)


def test_classify_against_existing_empty_candidates_unchanged_with_new_columns():
    empty = _candidates([])
    result = bi.classify_against_existing(empty, _existing_trades([]))
    assert result.empty
    assert "is_new" in result.columns
    assert "match_reason" in result.columns


def test_classify_against_existing_none_trades_df_all_new():
    cands = _candidates([
        {"ticker": "AAPL", "action": "BUY", "shares": 10.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 15), "company": "Apple Inc"},
    ])
    result = bi.classify_against_existing(cands, None)
    assert bool(result.iloc[0]["is_new"]) is True
    assert result.iloc[0]["match_reason"] == "new"


def test_classify_against_existing_empty_trades_df_all_new():
    cands = _candidates([
        {"ticker": "AAPL", "action": "BUY", "shares": 10.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 15), "company": "Apple Inc"},
    ])
    result = bi.classify_against_existing(cands, _existing_trades([]))
    assert bool(result.iloc[0]["is_new"]) is True


def test_classify_against_existing_exact_same_day_match_is_not_new():
    cands = _candidates([
        {"ticker": "AAPL", "action": "BUY", "shares": 10.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 15), "company": "Apple Inc"},
    ])
    existing = _existing_trades([
        {"ticker": "AAPL", "action": "BUY", "shares": 10.0, "price": 150.0,
         "traded_at": "2026-01-15T09:30:00Z"},
    ])
    result = bi.classify_against_existing(cands, existing)
    assert bool(result.iloc[0]["is_new"]) is False
    assert "existing trade on this date" in result.iloc[0]["match_reason"]


def test_classify_against_existing_same_content_different_date_flagged_possible_duplicate():
    cands = _candidates([
        {"ticker": "AAPL", "action": "BUY", "shares": 10.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 15), "company": "Apple Inc"},
    ])
    existing = _existing_trades([
        # Same content, but logged on a different (manual-entry) date.
        {"ticker": "AAPL", "action": "BUY", "shares": 10.0, "price": 150.0,
         "traded_at": "2026-01-20T09:30:00Z"},
    ])
    result = bi.classify_against_existing(cands, existing)
    assert bool(result.iloc[0]["is_new"]) is False
    assert "different date" in result.iloc[0]["match_reason"]


def test_classify_against_existing_two_identical_same_day_fills_both_matched():
    cands = _candidates([
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 15), "company": "Apple Inc"},
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 15), "company": "Apple Inc"},
    ])
    existing = _existing_trades([
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "traded_at": "2026-01-15T09:30:00Z"},
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "traded_at": "2026-01-15T09:31:00Z"},
    ])
    result = bi.classify_against_existing(cands, existing)
    assert (result["is_new"] == False).all()  # noqa: E712


def test_classify_against_existing_third_occurrence_beyond_existing_count_is_new():
    cands = _candidates([
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 15), "company": "Apple Inc"},
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 15), "company": "Apple Inc"},
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 15), "company": "Apple Inc"},
    ])
    existing = _existing_trades([
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "traded_at": "2026-01-15T09:30:00Z"},
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "traded_at": "2026-01-15T09:31:00Z"},
    ])
    result = bi.classify_against_existing(cands, existing)
    assert list(result["is_new"]) == [False, False, True]


def test_classify_against_existing_exact_match_consumes_agnostic_budget_too():
    # A same-day (exact) match should not ALSO leave the date-agnostic budget
    # unconsumed -- 1 existing exact-match row must not let a SECOND
    # different-date candidate also match against the same existing row.
    cands = _candidates([
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 15), "company": "Apple Inc"},   # exact match
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "activity_date": datetime.date(2026, 1, 20), "company": "Apple Inc"},  # different date
    ])
    existing = _existing_trades([
        {"ticker": "AAPL", "action": "BUY", "shares": 1.0, "price": 150.0,
         "traded_at": "2026-01-15T09:30:00Z"},  # only ONE existing row
    ])
    result = bi.classify_against_existing(cands, existing)
    assert bool(result.iloc[0]["is_new"]) is False
    assert "existing trade on this date" in result.iloc[0]["match_reason"]
    # The second candidate has no remaining agnostic budget -- genuinely new.
    assert bool(result.iloc[1]["is_new"]) is True
