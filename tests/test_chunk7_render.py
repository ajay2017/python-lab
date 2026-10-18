"""Shared-entry chunk 7c -- source-text pins for Home's add-to-winners render
(app.py has no importing test suite) + the decision_bucket label preference."""
import ast
import re
from pathlib import Path

from stock_analyzer import decision_bucket as db

_SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)


def test_add_card_news_checked_caption_from_entry_passed():
    assert 'for _ga_pn in ([] if _ga_passed is None else _ga_passed)' in _SRC
    assert '📰 News checked: {' in _SRC


def test_also_in_review_line_uses_the_constant_not_a_literal():
    assert "📰 Also in Review: " in _SRC
    assert 'review_news_note' in _SRC
    assert "NEWS_SENTIMENT_NEGATIVE," in _SRC.split("from stock_analyzer.constants import (", 1)[1].split(")", 1)[0]
    assert "{NEWS_SENTIMENT_NEGATIVE:+.2f} add bar, so the add stands." in _SRC
    assert "-0.15" not in _SRC.split("📰 Also in Review: ", 1)[1][:1500]
    assert "no news average to check" in _SRC


def test_adds_held_block_iterates_entry_held_adds_with_clears_when_and_pending_page():
    i = _SRC.index("📰 Adds held — entry check (")
    block = _SRC[i - 1800:i + 2600]
    assert "for _eh in entry_held_adds" in block
    assert "Clears when:" in block and "clears_when" in block
    assert "would qualify as an add; held:" in block
    assert "Also: " in block
    assert 'st.session_state["_pending_page"]    = "📈 Analysis"' in block
    assert "▶ Analyze {_eh_tk}" in block


def test_card_blocked_adds_caption():
    i = _SRC.index("Not suggested as adds — already have a call today: ")
    block = _SRC[i - 700:i + 200]
    assert "for _cb in card_blocked_adds" in block
    assert "card_label" in block


def test_grow_shown_lists_both_new_keys():
    i = _SRC.index("_grow_shown: set = set()")
    seg = _SRC[i:i + 1500]
    assert '"entry_held_adds"' in seg and '"card_blocked_adds"' in seg


def test_schema_version_bumped_to_18():
    assert re.search(r"_SYNTH_SCHEMA_VER = 18\b", _SRC)


def test_no_direct_nav_page_assignment_added():
    # The ONE legitimate assignment is the top-of-run _pending_page consumer;
    # nothing in the Grow Today render region may assign nav_page.
    lo = _SRC.index("_grow_shown: set = set()")
    hi = _SRC.index("Not suggested as adds")
    assert "nav_page" not in _SRC[lo:hi].replace("_pending_page", "")
    n = sum(
        1 for x in ast.walk(_TREE)
        if isinstance(x, ast.Assign)
        for t in x.targets
        if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
        and t.slice.value == "nav_page"
    )
    assert n == 1, n


def test_new_html_block_interpolates_only_a_count():
    i = _SRC.index("📰 Adds held — entry check (")
    seg = _SRC[i - 20:i + 80]          # the f-string literal itself
    assert "{_safe_html(str(len(_eh_rows)))}" in seg and "_eh_line" not in seg and "_eh_tk" not in seg


# -- decision_bucket label preference ---------------------------------------
def test_reduce_card_label_beats_an_earlier_critical_news_card():
    act = [
        {"ticker": "CRM", "kind": "critical_news"},
        {"ticker": "CRM", "kind": "deterioration_trim"},
    ]
    assert db.add_blocking_cards(act, [])["CRM"] == "Act Today · TRIM"


def test_news_card_label_kept_without_a_reduce_card():
    out = db.add_blocking_cards([{"ticker": "CRM", "kind": "critical_news"}], [])
    assert out["CRM"] == "Act Today · critical news"


def test_review_trim_also_beats_critical_news():
    review = [{"ticker": "CRM", "action": {"type": "TRIM_TO_TARGET"}, "headline": "x"}]
    out = db.add_blocking_cards([{"ticker": "CRM", "kind": "critical_news"}], review)
    assert out["CRM"] == "Act Today · TRIM"


def test_first_card_still_wins_otherwise():
    act = [{"ticker": "CRM", "kind": "premortem_triggered"},
           {"ticker": "CRM", "kind": "deterioration_trim"}]
    assert db.add_blocking_cards(act, [])["CRM"] == "Act Today · pre-mortem trigger"


def test_string_review_action_does_not_crash_and_still_prefers_the_trim():
    out = db.add_blocking_cards(
        [{"ticker": "CRM", "kind": "critical_news"}],
        [{"ticker": "CRM", "action": "TRIM_TO_TARGET"}],
    )
    # No crash, and the reduce card (not the news card) supplies the label. A
    # string action's suffix stays bare (golden-pinned _card_label behaviour).
    assert "critical news" not in out["CRM"]
    assert out["CRM"].startswith("Act Today")


def test_entry_unchecked_rendered_on_add_card_with_none_safe_read():
    assert '_ga_unchecked = _ga.get("entry_unchecked")' in _SRC
    assert "for _ga_un in ([] if _ga_unchecked is None else _ga_unchecked)" in _SRC


def test_news_checked_caption_skipped_when_also_in_review_has_the_average():
    assert "_ga_rn_has_avg" in _SRC
    assert 'and not _ga_rn_has_avg' in _SRC
    i = _SRC.index("_ga_rn_has_avg = ")
    assert 'news_avg' in _SRC[i:i + 200]


def test_held_analyze_buttons_laid_out_in_columns_rows():
    i = _SRC.index("📰 Adds held — entry check (")
    block = _SRC[i:i + 4000]
    assert "st.columns(len(_eh_chunk))" in block
    assert "_db_grow_held_add_{_eh_tk}" in block
    assert '_pending_page' in block
