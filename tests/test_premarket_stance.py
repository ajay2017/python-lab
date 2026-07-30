"""Tests for stock_analyzer/premarket_stance.py — assembles pre-market data
into an LLM prompt and parses the resulting narrative + stance verdict.
`generate_stance`'s real Anthropic call is never exercised — only its two
early-return guards (`not api_key`, `not inputs`), both of which return
*before* `import anthropic` runs (confirmed by reading the source: the
import sits inside the try block, after the guard clause), so no mocking
of `anthropic` is needed or done here.
"""
import pandas as pd
import pytest

from stock_analyzer import premarket_stance as ps


# ─── assemble_inputs — all-empty default shape ──────────────────────────────

def test_assemble_inputs_all_none_produces_documented_defaults():
    result = ps.assemble_inputs(None, None, None, None)
    assert result == {
        "futures": [], "global_markets": [], "movers": [], "events": [],
        "regime_label": "—", "regime_rationale": "",
        "top_holdings": [], "news_headlines": [],
    }


def test_assemble_inputs_all_falsy_produces_documented_defaults():
    result = ps.assemble_inputs({}, {}, pd.DataFrame(), [])
    assert result["futures"] == []
    assert result["top_holdings"] == []
    assert result["news_headlines"] == []


# ─── assemble_inputs — premarket_brief slicing caps ──────────────────────────

def test_assemble_inputs_slices_premarket_brief_lists_to_documented_caps():
    brief = {
        "futures":        [{"n": i} for i in range(10)],
        "global_markets": [{"n": i} for i in range(10)],
        "movers":         [{"n": i} for i in range(10)],
        "events":         [{"n": i} for i in range(10)],
    }
    result = ps.assemble_inputs(brief, None, None, None)
    assert len(result["futures"]) == 4
    assert len(result["global_markets"]) == 5
    assert len(result["movers"]) == 6
    assert len(result["events"]) == 3


# ─── assemble_inputs — regime extraction ────────────────────────────────────

def test_assemble_inputs_extracts_regime_label_and_rationale():
    regime = {"label": "Risk-On / Growth", "rationale": "VIX low, SPY trending up"}
    result = ps.assemble_inputs(None, regime, None, None)
    assert result["regime_label"] == "Risk-On / Growth"
    assert result["regime_rationale"] == "VIX low, SPY trending up"


# ─── assemble_inputs — top_holdings from port_df ────────────────────────────

def test_assemble_inputs_port_df_missing_weight_column_leaves_top_holdings_empty():
    port_df = pd.DataFrame({"Ticker": ["AAPL"], "Sector": ["Technology"]})
    result = ps.assemble_inputs(None, None, port_df, None)
    assert result["top_holdings"] == []


def test_assemble_inputs_port_df_sorted_descending_and_capped_at_5():
    port_df = pd.DataFrame({
        "Ticker":       [f"T{i}" for i in range(7)],
        "Weight (%)":   [1.0, 7.0, 3.0, 6.0, 2.0, 5.0, 4.0],
        "Sector":       ["Tech"] * 7,
        "Signal":       ["BUY"] * 7,
    })
    result = ps.assemble_inputs(None, None, port_df, None)
    assert len(result["top_holdings"]) == 5
    weights = [h["weight"] for h in result["top_holdings"]]
    assert weights == sorted(weights, reverse=True)
    assert result["top_holdings"][0]["ticker"] == "T1"  # weight 7.0, the max


# ─── assemble_inputs — news headlines truncation/cap ─────────────────────────

def test_assemble_inputs_news_headlines_truncated_and_capped():
    headlines = ["x" * 200 for _ in range(8)]
    result = ps.assemble_inputs(None, None, None, headlines)
    assert len(result["news_headlines"]) == 5
    assert all(len(h) == 140 for h in result["news_headlines"])


# ─── format_user_prompt ──────────────────────────────────────────────────────

def test_format_user_prompt_all_empty_inputs_still_valid():
    inputs = {
        "futures": [], "global_markets": [], "movers": [], "events": [],
        "regime_label": "—", "regime_rationale": "",
        "top_holdings": [], "news_headlines": [],
    }
    prompt = ps.format_user_prompt(inputs)
    assert "Current macro regime: —" in prompt
    assert "Write the 4-6 sentence stance note now" in prompt


def test_format_user_prompt_futures_section_formatting():
    inputs = {
        "futures": [
            {"name": "S&P 500", "chg_pct": 0.35},
            {"name": "Nasdaq 100", "chg_pct": -1.2},
        ],
        "global_markets": [], "movers": [], "events": [],
        "regime_label": "—", "regime_rationale": "",
        "top_holdings": [], "news_headlines": [],
    }
    prompt = ps.format_user_prompt(inputs)
    assert "US futures: S&P 500 +0.35% · Nasdaq 100 -1.20%" in prompt


def test_format_user_prompt_flags_unverified_mover():
    inputs = {
        "futures": [], "global_markets": [],
        "movers": [
            {"ticker": "MSFT", "chg_pct": -8.11, "xcheck_ok": False},
            {"ticker": "LLY", "chg_pct": 1.2, "xcheck_ok": True},
        ],
        "events": [],
        "regime_label": "—", "regime_rationale": "",
        "top_holdings": [], "news_headlines": [],
    }
    prompt = ps.format_user_prompt(inputs)
    assert "MSFT -8.11% (⚠ unverified)" in prompt
    assert "LLY +1.20%" in prompt
    assert "LLY +1.20% (⚠ unverified)" not in prompt


def test_format_user_prompt_mover_without_xcheck_key_not_flagged():
    # xcheck_ok absent (e.g. cross-check disabled/no independent source) must
    # not be treated as a divergence -- only an explicit False flags it.
    inputs = {
        "futures": [], "global_markets": [],
        "movers": [{"ticker": "AAPL", "chg_pct": 1.0}],
        "events": [],
        "regime_label": "—", "regime_rationale": "",
        "top_holdings": [], "news_headlines": [],
    }
    prompt = ps.format_user_prompt(inputs)
    assert "AAPL +1.00%" in prompt
    assert "unverified" not in prompt


def test_format_user_prompt_sections_only_appear_when_truthy():
    inputs = {
        "futures": [], "global_markets": [], "movers": [],
        "events": [{"event": "CPI", "time": "8:30", "impact": "HIGH"}],
        "regime_label": "Neutral", "regime_rationale": "",
        "top_holdings": [], "news_headlines": [],
    }
    prompt = ps.format_user_prompt(inputs)
    assert "US futures:" not in prompt
    assert "Global overnight:" not in prompt
    assert "Today's macro events: CPI (8:30, impact: HIGH)" in prompt
    assert "Pre-market movers" not in prompt


# ─── parse_stance — empty/whitespace text ───────────────────────────────────

def test_parse_stance_empty_text_returns_neutral_default():
    result = ps.parse_stance("")
    assert result == {"narrative": "", "stance": "neutral", "stance_label": "Neutral at open"}


def test_parse_stance_whitespace_only_returns_neutral_default():
    result = ps.parse_stance("   \n  \n ")
    assert result["stance"] == "neutral"
    assert result["narrative"] == ""


# ─── parse_stance — proper verdict line at the end ──────────────────────────

def test_parse_stance_defensive_verdict_stripped_from_narrative():
    text = "Futures are down sharply overnight.\nStance: Defensive at open"
    result = ps.parse_stance(text)
    assert result["stance"] == "defensive"
    assert result["stance_label"] == "Defensive at open"
    assert "Stance:" not in result["narrative"]
    assert "Futures are down sharply overnight." in result["narrative"]


def test_parse_stance_constructive_verdict_stripped_from_narrative():
    text = "Markets rallying overnight on strong data.\nStance: Constructive at open"
    result = ps.parse_stance(text)
    assert result["stance"] == "constructive"
    assert result["stance_label"] == "Constructive at open"
    assert "Stance:" not in result["narrative"]


def test_parse_stance_malformed_verdict_falls_back_to_neutral():
    text = "Mixed signals across the tape.\nStance: Bullish somehow"
    result = ps.parse_stance(text)
    assert result["stance"] == "neutral"
    assert result["stance_label"] == "Neutral at open"
    assert "Stance:" not in result["narrative"]


# ─── parse_stance — reversed search order (from the end backward) ───────────

def test_parse_stance_finds_real_verdict_line_last_even_with_earlier_mention():
    # An earlier line contains "Stance:" mid-sentence too, but since the real
    # verdict line is the LAST line, the reversed-order search finds and
    # matches it FIRST -- the earlier mention never becomes the deciding line.
    text = (
        "The company's stance: cautious but stable heading into earnings.\n"
        "Futures point higher on cooling inflation data.\n"
        "Stance: Constructive at open"
    )
    result = ps.parse_stance(text)
    assert result["stance"] == "constructive"
    # Both lines containing "stance:"/"Stance:" are stripped from narrative,
    # per the source's narrative_lines filter (not just the matched one).
    assert "cautious but stable" not in result["narrative"]
    assert "Futures point higher" in result["narrative"]


def test_parse_stance_earlier_lowercase_substring_can_false_positive_when_last_line_has_no_verdict():
    # No real verdict line at the end. The ONLY line containing "stance:" is
    # an earlier, unrelated mid-sentence use -- the reversed-order search
    # still finds it (eventually) and treats it as the verdict, per the
    # source's plain substring check ("stance:" in ln). This documents the
    # actual (imperfect) behavior rather than assuming it's guarded against.
    text = (
        "The company's stance: cautious but stable heading into earnings.\n"
        "Overall sentiment remains mixed for today's open."
    )
    result = ps.parse_stance(text)
    assert result["stance"] == "neutral"  # "cautious" matches neither defensive nor constructive
    assert "cautious but stable" not in result["narrative"]
    assert "Overall sentiment remains mixed" in result["narrative"]


# ─── generate_stance — early-return guards only (no anthropic call) ─────────

def test_generate_stance_no_api_key_returns_none():
    assert ps.generate_stance({"regime_label": "Neutral"}, api_key=None) is None
    assert ps.generate_stance({"regime_label": "Neutral"}, api_key="") is None


def test_generate_stance_no_inputs_returns_none():
    assert ps.generate_stance({}, api_key="fake-key") is None
    assert ps.generate_stance(None, api_key="fake-key") is None
