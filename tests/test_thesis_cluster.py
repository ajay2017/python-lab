"""
Tests for stock_analyzer/thesis_cluster.py — Hidden Same-Bet Detector (D1):
corpus assembly from held BUY theses, text truncation, prompt formatting,
the two-layer-validated LLM cluster call, and the pure-Python
unverified/possible/confirmed classification. Zero coverage before this
batch. `generate_thesis_clusters`'s real Anthropic call is guard-only tested
(returns before `import anthropic` runs); the parse/validate logic is
exercised directly via `_parse_cluster_response`.
"""
import pandas as pd

from stock_analyzer import thesis_cluster as tc


# ─── build_thesis_corpus ──────────────────────────────────────────────────────

def test_build_thesis_corpus_none_port_df_returns_empty():
    trades_df = pd.DataFrame({"ticker": ["AAA"], "action": ["BUY"], "user_thesis": ["t"]})
    assert tc.build_thesis_corpus(None, trades_df) == []


def test_build_thesis_corpus_empty_port_df_returns_empty():
    trades_df = pd.DataFrame({"ticker": ["AAA"], "action": ["BUY"], "user_thesis": ["t"]})
    assert tc.build_thesis_corpus(pd.DataFrame(), trades_df) == []


def test_build_thesis_corpus_none_trades_df_returns_empty():
    port_df = pd.DataFrame({"Ticker": ["AAA", "BBB"]})
    assert tc.build_thesis_corpus(port_df, None) == []


def test_build_thesis_corpus_missing_ticker_columns_returns_empty():
    port_df = pd.DataFrame({"NotTicker": ["AAA"]})
    trades_df = pd.DataFrame({"ticker": ["AAA"], "action": ["BUY"], "user_thesis": ["t"]})
    assert tc.build_thesis_corpus(port_df, trades_df) == []


def test_build_thesis_corpus_no_buy_rows_returns_empty():
    port_df = pd.DataFrame({"Ticker": ["AAA", "BBB"]})
    trades_df = pd.DataFrame({"ticker": ["AAA", "BBB"], "action": ["SELL", "SELL"],
                              "user_thesis": ["t", "t"]})
    assert tc.build_thesis_corpus(port_df, trades_df) == []


def test_build_thesis_corpus_picks_first_row_with_nonempty_thesis():
    # trades_df is newest-first; a more recent BUY with blank thesis should be
    # skipped in favor of an older BUY that has real text.
    port_df = pd.DataFrame({"Ticker": ["AAA", "BBB"]})
    trades_df = pd.DataFrame({
        "ticker":      ["AAA", "AAA", "BBB", "BBB"],
        "action":      ["BUY", "BUY", "BUY", "BUY"],
        "user_thesis": [None,  "older real thesis", "b thesis 1", "b thesis 2"],
    })
    corpus = tc.build_thesis_corpus(port_df, trades_df)
    by_ticker = {c["ticker"]: c for c in corpus}
    assert by_ticker["AAA"]["thesis_text"] == "older real thesis"


def test_build_thesis_corpus_ticker_with_zero_thesis_buys_excluded():
    port_df = pd.DataFrame({"Ticker": ["AAA", "BBB"]})
    trades_df = pd.DataFrame({
        "ticker":      ["AAA", "BBB"],
        "action":      ["BUY", "BUY"],
        "user_thesis": [None, "real thesis for BBB"],
    })
    corpus = tc.build_thesis_corpus(port_df, trades_df)
    # only BBB qualifies -> below _MIN_THESIS_POSITIONS(2) -> []
    assert corpus == []


def test_build_thesis_corpus_fewer_than_min_positions_returns_empty():
    port_df = pd.DataFrame({"Ticker": ["AAA"]})
    trades_df = pd.DataFrame({"ticker": ["AAA"], "action": ["BUY"], "user_thesis": ["only one"]})
    assert tc.build_thesis_corpus(port_df, trades_df) == []


def test_build_thesis_corpus_sector_defaults_to_empty_string_when_absent():
    port_df = pd.DataFrame({"Ticker": ["AAA", "BBB"]})
    trades_df = pd.DataFrame({
        "ticker":      ["AAA", "BBB"],
        "action":      ["BUY", "BUY"],
        "user_thesis": ["thesis a", "thesis b"],
    })
    corpus = tc.build_thesis_corpus(port_df, trades_df)
    assert all(c["sector"] == "" for c in corpus)


# ─── _truncate ─────────────────────────────────────────────────────────────

def test_truncate_exactly_at_max_len_not_truncated():
    text = "x" * 1500
    result, truncated = tc._truncate(text)
    assert result == text
    assert truncated is False


def test_truncate_one_char_over_max_len_truncated():
    text = "x" * 1501
    result, truncated = tc._truncate(text)
    assert result == text[:1500]
    assert truncated is True


# ─── _format_corpus_for_prompt ────────────────────────────────────────────────

def test_format_corpus_for_prompt_any_truncated_true_if_any_item_truncated():
    corpus = [
        {"ticker": "AAA", "sector": "Tech", "thesis_text": "x" * 2000},
        {"ticker": "BBB", "sector": "Tech", "thesis_text": "short"},
    ]
    _, any_truncated = tc._format_corpus_for_prompt(corpus)
    assert any_truncated is True


def test_format_corpus_for_prompt_any_truncated_false_when_none_truncated():
    corpus = [
        {"ticker": "AAA", "sector": "Tech", "thesis_text": "short a"},
        {"ticker": "BBB", "sector": "Tech", "thesis_text": "short b"},
    ]
    _, any_truncated = tc._format_corpus_for_prompt(corpus)
    assert any_truncated is False


# ─── generate_thesis_clusters — guards only ──────────────────────────────────

def test_generate_thesis_clusters_no_api_key_returns_none():
    corpus = [{"ticker": "A", "thesis_text": "t"}, {"ticker": "B", "thesis_text": "t"}]
    assert tc.generate_thesis_clusters(corpus, api_key="") is None


def test_generate_thesis_clusters_too_few_positions_returns_none():
    corpus = [{"ticker": "A", "thesis_text": "t"}]
    assert tc.generate_thesis_clusters(corpus, api_key="fake-key") is None


# ─── _parse_cluster_response ──────────────────────────────────────────────────

def _corpus_2():
    return [
        {"ticker": "AAA", "sector": "Tech", "thesis_text": "AI capex supercycle continues"},
        {"ticker": "BBB", "sector": "Semis", "thesis_text": "riding the AI capex supercycle wave"},
    ]


def test_parse_cluster_response_empty_or_none_returns_none():
    assert tc._parse_cluster_response("", _corpus_2()) is None
    assert tc._parse_cluster_response(None, _corpus_2()) is None


def test_parse_cluster_response_non_dict_json_returns_none():
    assert tc._parse_cluster_response("[1, 2]", _corpus_2()) is None


def test_parse_cluster_response_clusters_not_list_returns_none():
    assert tc._parse_cluster_response('{"clusters": "nope"}', _corpus_2()) is None


def test_parse_cluster_response_quote_not_in_thesis_text_drops_member():
    raw = (
        '{"clusters": [{"tickers": ["AAA", "BBB"], '
        '"shared_assumption": "AI capex", '
        '"quotes": {"AAA": "AI capex supercycle", "BBB": "totally unrelated text"}}]}'
    )
    result = tc._parse_cluster_response(raw, _corpus_2())
    # BBB's quote doesn't appear in its own thesis_text -> dropped, leaving
    # only AAA which is below _MIN_THESIS_POSITIONS(2) -> whole cluster dropped
    assert result == []


def test_parse_cluster_response_quotes_missing_ticker_entirely_drops_member():
    raw = (
        '{"clusters": [{"tickers": ["AAA", "BBB"], '
        '"shared_assumption": "AI capex", '
        '"quotes": {"AAA": "AI capex supercycle"}}]}'
    )
    result = tc._parse_cluster_response(raw, _corpus_2())
    assert result == []


def test_parse_cluster_response_below_min_members_drops_cluster():
    raw = (
        '{"clusters": [{"tickers": ["AAA"], '
        '"shared_assumption": "AI capex", '
        '"quotes": {"AAA": "AI capex supercycle"}}]}'
    )
    result = tc._parse_cluster_response(raw, _corpus_2())
    assert result == []


def test_parse_cluster_response_valid_cluster_survives():
    raw = (
        '{"clusters": [{"tickers": ["AAA", "BBB"], '
        '"shared_assumption": "AI capex supercycle", '
        '"quotes": {"AAA": "AI capex supercycle", "BBB": "AI capex supercycle"}}]}'
    )
    result = tc._parse_cluster_response(raw, _corpus_2())
    assert len(result) == 1
    assert result[0]["tickers"] == ["AAA", "BBB"]


# ─── classify_clusters ────────────────────────────────────────────────────────

def test_classify_clusters_none_validated_clusters_returns_empty():
    assert tc.classify_clusters(None, None) == []
    assert tc.classify_clusters([], None) == []


def test_classify_clusters_none_correlation_result_unverified():
    validated = [{"tickers": ["AAA", "BBB"], "shared_assumption": "x", "quotes": {}}]
    result = tc.classify_clusters(validated, None)
    assert result[0]["state"] == "unverified"
    assert result[0]["corr_subpairs"] == []


def test_classify_clusters_superset_match_confirmed():
    validated = [{"tickers": ["AAA", "BBB"], "shared_assumption": "x", "quotes": {}}]
    corr_result = [{"tickers": ["AAA", "BBB", "CCC"]}]
    result = tc.classify_clusters(validated, corr_result)
    assert result[0]["state"] == "confirmed"


def test_classify_clusters_no_superset_possible_with_subpairs():
    validated = [{"tickers": ["AAA", "BBB", "CCC"], "shared_assumption": "x", "quotes": {}}]
    corr_result = [{"tickers": ["AAA", "BBB"]}]  # partial overlap, not a superset
    result = tc.classify_clusters(validated, corr_result)
    assert result[0]["state"] == "possible"
    assert result[0]["corr_subpairs"] == [["AAA", "BBB"]]
