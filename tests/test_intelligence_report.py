"""
Tests for stock_analyzer/intelligence_report.py — F-4 Monthly Intelligence
Report: percent/band-line formatting helpers, the data-package builder (which
reuses recommendations_history's rule-based pipeline), prompt formatting, and
response section parsing. Zero coverage before this batch.
`recommendations_history.match_recs_to_trades`/`compute_outcomes`/
`report_viz_snapshot`/`summary_stats`/`by_composite_band`/`by_verdict` are
imported INSIDE build_report_package() at call time, so they are monkeypatched
directly on the stock_analyzer.recommendations_history module object.
`generate_report`'s real Anthropic call is exercised via a fake
`sys.modules["anthropic"]` module for a full-header round trip; its guard
clauses return before `import anthropic` runs and need no mocking.
"""
import sys
import types

import pandas as pd
import pytest

import stock_analyzer.recommendations_history as rh
from stock_analyzer import intelligence_report as ir


# ─── fake anthropic module helper ────────────────────────────────────────────

class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    def create(self, **kwargs):
        return _FakeResponse(self._response_text)


class _FakeClient:
    def __init__(self, response_text, **kwargs):
        self.messages = _FakeMessages(response_text)


def _install_fake_anthropic(response_text):
    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = lambda **kwargs: _FakeClient(response_text)
    sys.modules["anthropic"] = fake_mod


@pytest.fixture(autouse=True)
def _cleanup_fake_anthropic():
    yield
    sys.modules.pop("anthropic", None)


# ─── _pct ──────────────────────────────────────────────────────────────────────

def test_pct_none_returns_placeholder():
    assert ir._pct(None) == "—"


def test_pct_nan_returns_placeholder():
    assert ir._pct(float("nan")) == "—"


def test_pct_normal_float_signed_one_decimal():
    assert ir._pct(3.14159) == "+3.1%"
    assert ir._pct(-2.5) == "-2.5%"


def test_pct_non_numeric_returns_placeholder():
    assert ir._pct("not-a-number") == "—"


# ─── _band_line ────────────────────────────────────────────────────────────────

def test_band_line_full_row_joins_with_dot():
    row = {"band": "Strong Buy", "n_total": 5, "action_rate": 80.0, "n_priced": 4, "avg_alpha": 2.5}
    line = ir._band_line(row)
    assert "Strong Buy: 5 rec(s)" in line
    assert "acted 80%" in line
    assert "4 matured" in line
    assert "engine alpha +2.5%" in line
    assert " · " in line


def test_band_line_action_rate_and_avg_alpha_omitted_when_none():
    row = {"band": "Hold zone", "n_total": 2, "action_rate": None, "n_priced": 0, "avg_alpha": None}
    line = ir._band_line(row)
    assert "acted" not in line
    assert "matured" not in line
    assert "engine alpha" not in line


def test_band_line_avg_alpha_omitted_when_n_priced_falsy_even_if_present():
    row = {"band": "Buy", "n_total": 3, "action_rate": 50.0, "n_priced": 0, "avg_alpha": 9.9}
    line = ir._band_line(row)
    assert "matured" not in line
    assert "engine alpha" not in line


# ─── build_report_package ─────────────────────────────────────────────────────

def test_build_report_package_none_recs_df_returns_default_no_data():
    result = ir.build_report_package("2026-01-01", "2026-01-31", None, pd.DataFrame())
    assert result["has_data"] is False
    assert result["n_total"] == 0


def test_build_report_package_empty_recs_df_returns_default_no_data():
    result = ir.build_report_package("2026-01-01", "2026-01-31", pd.DataFrame(), pd.DataFrame())
    assert result["has_data"] is False


def test_build_report_package_compute_outcomes_empty_returns_default(monkeypatch):
    monkeypatch.setattr(rh, "match_recs_to_trades", lambda recs_df, trades_df: "matched")
    monkeypatch.setattr(rh, "compute_outcomes", lambda *a, **k: [])
    recs_df = pd.DataFrame([{"ticker": "AAA"}])
    result = ir.build_report_package("2026-01-01", "2026-01-31", recs_df, pd.DataFrame())
    assert result["has_data"] is False
    assert result["n_total"] == 0


def test_build_report_package_flow_n_total_zero_returns_before_stats(monkeypatch):
    monkeypatch.setattr(rh, "match_recs_to_trades", lambda recs_df, trades_df: "matched")
    monkeypatch.setattr(rh, "compute_outcomes", lambda *a, **k: [{"ticker": "AAA", "rec_type": "new_pick"}])
    monkeypatch.setattr(rh, "report_viz_snapshot", lambda enriched, rec_types=None: {
        "flow": {"n_total": 0, "n_acted": 0, "n_missed": 0},
        "bands": [], "missed": [], "missed_split": {},
    })
    recs_df = pd.DataFrame([{"ticker": "AAA"}])
    result = ir.build_report_package("2026-01-01", "2026-01-31", recs_df, pd.DataFrame())
    assert result["has_data"] is False
    assert result["n_total"] == 0
    assert result["action_rate"] is None
    assert result["n_graded"] == 0  # never reached summary_stats


def _fake_pipeline(monkeypatch, n_priced, min_graded_check_value=None):
    monkeypatch.setattr(rh, "match_recs_to_trades", lambda recs_df, trades_df: "matched")
    enriched = [{"ticker": "AAA", "rec_type": "new_pick"}, {"ticker": "BBB", "rec_type": "new_pick"}]
    monkeypatch.setattr(rh, "compute_outcomes", lambda *a, **k: enriched)
    monkeypatch.setattr(rh, "report_viz_snapshot", lambda e, rec_types=None: {
        "flow": {"n_total": 2, "n_acted": 1, "n_missed": 1},
        "bands": [], "missed": [], "missed_split": {},
    })
    monkeypatch.setattr(rh, "summary_stats", lambda scoped: {
        "n_priced": n_priced,
        "avg_acted_alpha": 4.5, "avg_acted_pct": 6.0, "avg_missed_pct": -1.0,
        "missed_alpha": -3.0, "avg_missed_alpha": -2.0,
        "best": {"ticker": "AAA", "outcome_pct": 10.0, "alpha_pct": 5.0, "acted_on": True},
        "worst": {"ticker": "BBB", "outcome_pct": -5.0, "alpha_pct": -3.0, "acted_on": False},
    })
    monkeypatch.setattr(rh, "by_composite_band", lambda scoped: [
        {"band": "Buy", "n_total": 2, "action_rate": 50.0, "n_priced": 2, "avg_alpha": 1.0},
    ])
    monkeypatch.setattr(rh, "by_verdict", lambda scoped: [
        {"verdict": "Confirmed", "n_total": 2, "action_rate": 50.0, "n_priced": 2, "avg_alpha": 1.0},
    ])


def test_build_report_package_fully_populated_pipeline(monkeypatch):
    _fake_pipeline(monkeypatch, n_priced=5)
    recs_df = pd.DataFrame([{"ticker": "AAA"}])
    weekly_rows = [{"week_ending": "2026-01-25", "performance_pct": 2.0, "spy_pct": 1.0, "alpha_pct": 1.0}]
    result = ir.build_report_package("2026-01-01", "2026-01-31", recs_df, pd.DataFrame(),
                                      weekly_rows=weekly_rows, min_graded=5)
    assert result["n_total"] == 2
    assert result["n_acted"] == 1
    assert result["n_missed"] == 1
    assert result["action_rate"] == 50.0
    assert result["engine_alpha_pct"] == 4.5
    assert result["q0_ready"] is True  # 5 >= min_graded(5)
    assert len(result["weekly_trajectory"]) == 1
    assert result["weekly_trajectory"][0]["week_ending"] == "2026-01-25"


def test_build_report_package_q0_ready_boundary(monkeypatch):
    _fake_pipeline(monkeypatch, n_priced=4)
    recs_df = pd.DataFrame([{"ticker": "AAA"}])
    result = ir.build_report_package("2026-01-01", "2026-01-31", recs_df, pd.DataFrame(), min_graded=5)
    assert result["q0_ready"] is False  # 4 < min_graded(5)


# ─── _format_prompt ────────────────────────────────────────────────────────────

def _base_package(**overrides):
    package = {
        "period_start": "2026-01-01", "period_end": "2026-01-31",
        "n_total": 10, "n_acted": 6, "n_missed": 4, "action_rate": 60.0,
        "q0_ready": True, "min_graded": 5,
        "avg_acted_pct": 5.0, "avg_missed_pct": -1.0,
        "avg_acted_alpha": 3.0, "avg_missed_alpha": -2.0,
        "missed_alpha": -5.0, "best": None, "worst": None,
        "band_rows": [], "verdict_rows": [], "weekly_trajectory": [],
    }
    package.update(overrides)
    return package


def test_format_prompt_count_discipline_and_field_lines():
    text = ir._format_prompt(_base_package())
    assert "COUNT DISCIPLINE" in text
    assert "surfaced = 10, acted on = 6, not acted on = 4." in text


def test_format_prompt_q0_not_ready_shows_note_line():
    text = ir._format_prompt(_base_package(q0_ready=False))
    assert "too few" in text
    assert "matured" in text


def test_format_prompt_q0_ready_omits_note_line():
    text = ir._format_prompt(_base_package(q0_ready=True))
    assert "too few" not in text


def test_format_prompt_best_worst_only_when_truthy():
    text = ir._format_prompt(_base_package())
    assert "Best outcome" not in text
    assert "Worst outcome" not in text

    package = _base_package(
        best={"ticker": "AAA", "outcome_pct": 10.0, "alpha_pct": 5.0, "acted_on": True},
        worst={"ticker": "BBB", "outcome_pct": -5.0, "alpha_pct": -3.0, "acted_on": False},
    )
    text2 = ir._format_prompt(package)
    assert "Best outcome: AAA" in text2
    assert "Worst outcome: BBB" in text2


def test_format_prompt_band_verdict_weekly_sections_only_when_nonempty():
    text = ir._format_prompt(_base_package())
    assert "By composite band" not in text
    assert "By cross-check verdict" not in text
    assert "Weekly performance trajectory" not in text

    package = _base_package(
        band_rows=[{"band": "Buy", "n_total": 2, "action_rate": 50.0, "n_priced": 2, "avg_alpha": 1.0}],
        verdict_rows=[{"verdict": "Confirmed", "n_total": 2, "action_rate": 50.0, "n_priced": 2, "avg_alpha": 1.0}],
        weekly_trajectory=[{"week_ending": "2026-01-25", "performance_pct": 1.0, "spy_pct": 0.5, "alpha_pct": 0.5}],
    )
    text2 = ir._format_prompt(package)
    assert "By composite band" in text2
    assert "By cross-check verdict" in text2
    assert "Weekly performance trajectory" in text2


# ─── _parse_response ──────────────────────────────────────────────────────────

def test_parse_response_canonical_headers_split_correctly():
    # Body text deliberately avoids starting with the word "pattern" -- the
    # bare "pattern" alternate-header spelling below would otherwise match a
    # body line starting with that word and wrongly restart a new section.
    text = (
        "**Entry quality**\nEntry text here.\n\n"
        "**Signal discipline**\nSignal text here.\n\n"
        "**Pattern & focus**\nFocus text here."
    )
    sections = ir._parse_response(text)
    assert sections["section_entry_quality"] == "Entry text here."
    assert sections["section_signal_discipline"] == "Signal text here."
    assert sections["section_patterns"] == "Focus text here."


def test_parse_response_alternate_pattern_header_spellings():
    text1 = "**Pattern and focus**\nAlt text 1."
    text2 = "**Pattern**\nAlt text 2."
    assert ir._parse_response(text1)["section_patterns"] == "Alt text 1."
    assert ir._parse_response(text2)["section_patterns"] == "Alt text 2."


def test_parse_response_discards_text_before_first_header():
    text = "Preamble that should be discarded.\n**Entry quality**\nReal text."
    sections = ir._parse_response(text)
    assert "Preamble" not in sections["section_entry_quality"]
    assert sections["section_entry_quality"] == "Real text."


def test_parse_response_missing_section_stays_empty():
    text = "**Entry quality**\nOnly this section."
    sections = ir._parse_response(text)
    assert sections["section_signal_discipline"] == ""
    assert sections["section_patterns"] == ""


# ─── generate_report ───────────────────────────────────────────────────────────

def test_generate_report_no_api_key_returns_none():
    assert ir.generate_report({"has_data": True}, api_key="") is None


def test_generate_report_has_data_false_returns_none():
    assert ir.generate_report({"has_data": False}, api_key="fake-key") is None


def test_generate_report_valid_response_round_trip():
    package = _base_package()
    package["has_data"] = True
    package["viz"] = {"flow": {}}
    package["n_acted"] = 6
    package["n_missed"] = 4
    package["engine_alpha_pct"] = 3.0
    raw = (
        "**Entry quality**\nEntry text.\n\n"
        "**Signal discipline**\nSignal text.\n\n"
        "**Pattern & focus**\nFocus text."
    )
    _install_fake_anthropic(raw)
    result = ir.generate_report(package, api_key="fake-key")
    assert result["section_entry_quality"] == "Entry text."
    assert result["section_signal_discipline"] == "Signal text."
    assert result["section_patterns"] == "Focus text."
    assert result["section_thesis"] is None
    assert result["email_sent"] is False
    assert result["viz_json"] == package["viz"]
    assert result["period_start"] == package["period_start"]
    assert result["period_end"] == package["period_end"]
    assert "generated_at" in result


# ─── classify_recommendations_read ───────────────────────────────────────────
# 2026-08-30: extracted from app.py's "Generate Monthly Report" button so the
# outage-vs-empty-vs-ready decision is unit-tested rather than only reachable
# via a screenshot (app.py has no test coverage of its own). Also closes a
# latent app.py bug: the old inline check was `if _mr_recs is None or
# _mr_recs.empty`, but load_recommendations() never actually returns None,
# so that branch was dead code and a DB outage rendered identically to a
# genuine "nothing recorded yet" quiet period.

def test_classify_recommendations_read_outage_when_read_is_none():
    assert ir.classify_recommendations_read(None) == "outage"


def test_classify_recommendations_read_empty_when_genuinely_zero_rows():
    assert ir.classify_recommendations_read(pd.DataFrame()) == "empty"


def test_classify_recommendations_read_ready_when_rows_present():
    df = pd.DataFrame({"ticker": ["AAPL"], "rec_date": ["2026-08-24"]})
    assert ir.classify_recommendations_read(df) == "ready"


def test_classify_recommendations_read_outage_never_confused_with_empty():
    """The failure mode this function exists to close: None and a genuinely
    empty DataFrame must classify DIFFERENTLY."""
    assert ir.classify_recommendations_read(None) != ir.classify_recommendations_read(pd.DataFrame())
