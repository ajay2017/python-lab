"""CorrYoung C4 -- app.py wiring pins (no app.py import: source / AST only).

app.py is render-only here: every decision comes from corr_window.view_state,
the util / pair_add_gate notes and the block entries' "sample" tag. These tests
pin the wiring so a later edit cannot quietly bring back a pair card on a thin
sample, drop the Q2 coverage argument, or put a literal 20 in a text.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest
from tests._ast_cache import parse_file_cached

from stock_analyzer.constants import CORR_MIN_OBS_TRUSTED

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_APP = (_ROOT / "app.py").read_text(encoding="utf-8")
pytestmark = pytest.mark.xdist_group(name="corr_young_c4_wiring_ast")
_TREE_BOX: list = []


def _tree():
    if not _TREE_BOX:
        _TREE_BOX.append(parse_file_cached(_ROOT / "app.py"))
    return _TREE_BOX[0]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _call_name(call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


def _calls(name):
    return [n for n in ast.walk(_tree()) if isinstance(n, ast.Call) and _call_name(n) == name]


def _names_in(node) -> set:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _strings_in(node) -> list[str]:
    return [c.value for c in ast.walk(node)
            if isinstance(c, ast.Constant) and isinstance(c.value, str)]


def _page_block(label: str) -> ast.If:
    for n in ast.walk(_tree()):
        if (isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name) and n.test.left.id == "page"
                and len(n.test.comparators) == 1
                and isinstance(n.test.comparators[0], ast.Constant)
                and n.test.comparators[0].value == label):
            return n
    raise AssertionError(f"page block {label!r} not found")


def _if_with_state_test(root, local, value):
    for n in ast.walk(root):
        if (isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name) and n.test.left.id == local
                and len(n.test.ops) == 1 and isinstance(n.test.ops[0], ast.Eq)
                and isinstance(n.test.comparators[0], ast.Constant)
                and n.test.comparators[0].value == value):
            return n
    raise AssertionError(f"no `if {local} == {value!r}` found")


# -- 1. Brief wiring (Q2 live) ------------------------------------------------
def test_both_build_daily_briefing_calls_pass_the_correlation_coverage():
    calls = _calls("build_daily_briefing")
    assert len(calls) == 2, len(calls)
    for c in calls:
        kws = {k.arg: k.value for k in c.keywords}
        assert "corr_coverage" in kws
        assert isinstance(kws["corr_coverage"], ast.Name) and kws["corr_coverage"].id == "_corr_cov"


def test_corr_cov_is_assigned_on_both_the_memo_hit_and_miss_paths():
    # HIT restores it from the bundle (.get: a pre-bump bundle lacks the key);
    # MISS unpacks it from the correlation bundle.
    assert '_corr_cov          = _b.get("corr_coverage")' in _APP
    assert '_crb["risk_pairs"], _crb["div_label"], _crb["corr_coverage"], _crb["div_recs"]' in _APP


def test_synth_schema_version_keeps_the_corryoung_note_in_its_history():
    # CorrYoung C4 bumped 28 -> 29; DeferSizeBlind bumped it again (30) and BriefBreachRestore
    # again (31), BriefSkipSizeBlind again (32), B-C3 again (33), beta-gate C2 again (34), so the CorrYoung note still sits in the "was 29:" clause.
    m = re.search(r"_SYNTH_SCHEMA_VER = 35  # bumped: .*?; was 29: (.*?); was 28:", _APP)
    assert m and "CorrYoung" in m.group(1) and "left_out" in m.group(1)


# -- 2. Home "Adds held" rows -------------------------------------------------
def test_corr_unmeasured_adds_have_their_own_heading_and_split_by_check():
    assert "🔗 Adds held — correlation couldn't be measured" in _APP
    assert "📰 Adds held — entry check" in _APP
    assert '_eh.get("check") == "corr_unmeasured"' in _APP


def test_adds_held_line_strips_the_double_period_and_the_double_when():
    i = _APP.index('_eh_reason = str(_eh.get("reason") or "entry check")')
    seg = _APP[i:i + 900]
    assert 'endswith(".")' in seg and "_eh_reason[:-1]" in seg
    assert 'startswith("when ")' in seg and 'len("when ")' in seg


# -- 3. Home Structural banner ------------------------------------------------
def test_structural_banner_reads_the_entries_sample_tag_not_a_recomputed_state():
    i = _APP.index("_cb_thin_tickers = sorted({")
    seg = _APP[i - 900:i + 6200]
    assert '_e.get("sample") == "thin"' in seg
    # the thin wording: a "couldn't be measured" text, never a finding; the
    # floor is the coverage's own (need_of), not a literal
    assert "Correlation couldn't be measured this run" in seg
    assert "This is not a finding that they move together." in seg
    assert "corr_window.need_of(_corr_cov)" in seg


def test_standing_pair_caption_ignores_thin_pair_entries():
    i = _APP.index("_pair_blocks_trusted = None if _pair_add_blocks is None else {")
    seg = _APP[i:i + 900]
    assert 'not (isinstance(_e, dict) and _e.get("sample") == "thin")' in seg
    (c,) = _calls("split_scan_liftable")
    assert isinstance(c.args[1], ast.Name) and c.args[1].id == "_pair_blocks_trusted"


def test_new_pairing_lines_are_skipped_when_the_banner_is_thin_only():
    assert "for _struct_alert_c in ([] if _cb_thin_only else _struct_new_clusters):" in _APP


def _structural_block():
    i = _APP.index("with _alert_ph_structural.container():")
    return _APP[i:i + 9000].split("elif _struct_new_clusters is None:", 1)[0]


def test_thin_only_structural_hold_is_a_caption_tier_notice_not_a_warning_notice():
    seg = _structural_block()
    # the tier is chosen from _cb_thin_only, and the call comes after it is computed
    assert '_home_notice("structural", "caption" if _cb_thin_only else "warning")' in seg
    assert seg.index("_cb_thin_only = (") < seg.index('_home_notice("structural"')
    assert seg.count('_home_notice("structural"') == 1
    # "caption" is a recognised tier of the notices roll-up
    from stock_analyzer import home_notices
    assert "caption" in home_notices.TIERS


def test_thin_only_structural_hold_has_no_scan_button_and_reads_the_withheld_state():
    seg = _structural_block()
    assert 'if not _cb_thin_only and st.button(' in seg
    assert _norm('corr_window.view_state( corr_df, _corr_cov, _corr_unchecked)["state"] == "withheld"') in _norm(seg)
    # thin-only text covers the no-entry case without naming a ticker
    assert "adds on any pair that crossed the line are" in seg


# -- 4. Risk Analysis ---------------------------------------------------------
def _ra_chain():
    ra = _page_block("🔗 Risk Analysis")
    withheld = _if_with_state_test(ra, "_ra_vs", "withheld")
    return ra, withheld


def test_risk_analysis_withheld_branch_shows_only_notes():
    _ra, withheld = _ra_chain()
    body = ast.Module(body=withheld.body, type_ignores=[])
    names = _names_in(body)
    text = " ".join(_strings_in(body))
    # no tiles, pair cards, heatmap, classification, util unchecked note
    for banned in ("metric", "plotly_chart", "Heatmap", "correlation_unchecked_note",
                   "correlation_flat_note", "correlation_flat", "risk_pairs"):
        assert banned not in names, banned
    for banned in ("These positions move together", "Classification:", "Heatmap"):
        assert banned not in text, banned
    # it does render the notes and names a failed price download in plain words
    assert "warning" in names and "caption" in names
    assert "_ra_notes" in names and "_ra_rest" in names
    assert "Price history couldn't be loaded this run for" in text


def test_risk_analysis_pair_cards_and_heatmap_live_only_in_the_non_withheld_else():
    ra, withheld = _ra_chain()
    # the withheld If's orelse chain carries them; its own body must not
    wbody = ast.Module(body=withheld.body, type_ignores=[])
    assert "Heatmap" not in _names_in(wbody)
    rest = ast.Module(body=withheld.orelse, type_ignores=[])
    assert "Heatmap" in _names_in(rest)
    assert any("These positions move together" in t for t in _strings_in(rest))
    # one set of pair cards in the page, and it is in that else
    cards = [t for t in _strings_in(ra) if "These positions move together" in t]
    assert len(cards) == 1


def test_risk_analysis_none_state_says_the_reading_failed_when_nothing_was_published():
    ra, withheld = _ra_chain()
    seg = ast.dump(ast.Module(body=withheld.orelse, type_ignores=[]))
    text = " ".join(_strings_in(ast.Module(body=withheld.orelse, type_ignores=[])))
    assert "The correlation reading couldn't be built this run" in text
    assert "Need at least 2 holdings with price history to compute correlations." in text
    # the crash branch is decided on the producer's three outputs all being None
    assert "_corr_cov" in seg and "_corr_unchecked" in seg


def test_risk_analysis_coverage_line_is_pairwise_and_the_toward_zero_claim_is_gone():
    assert "toward zero" not in _APP
    assert "usually what caps the sample" not in _APP
    assert "shared by all" not in _APP
    assert "Computed on each pair's own shared days: the thinnest pair has " in _APP


def test_risk_analysis_partial_state_names_only_the_unchecked_rest():
    ra = _page_block("🔗 Risk Analysis")
    part = _if_with_state_test(ra, "_ra_vs", "partial")
    names = _names_in(ast.Module(body=part.body, type_ignores=[]))
    assert "correlation_unchecked_note" in names and "unchecked_rest" in " ".join(
        _strings_in(ast.Module(body=part.body, type_ignores=[])))


@pytest.mark.parametrize("fn", ["correlation_unchecked_note", "correlation_record_withheld_note"])
def test_every_note_call_in_app_passes_the_coverage(fn):
    calls = _calls(fn)
    assert calls
    for c in calls:
        kw = {k.arg for k in c.keywords}
        # unchecked note: (list, coverage); withheld note: (list, coverage, subject=)
        assert len(c.args) >= 2, (fn, c.lineno)
        assert "subject" in kw or fn == "correlation_unchecked_note"


# -- 5. Correlation Clusters --------------------------------------------------
def test_clusters_tab_withheld_branch_has_no_cluster_loop():
    pi = _page_block("🧩 Intelligence")
    withheld = [n for n in ast.walk(pi)
                if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Subscript)
                and isinstance(n.test.left.value, ast.Name) and n.test.left.value.id == "_pi_view"
                and isinstance(n.test.comparators[0], ast.Constant)
                and n.test.comparators[0].value == "withheld"]
    assert len(withheld) == 2   # the empty-matrix branch and the thin-sample branch
    for w in withheld:
        body = ast.Module(body=w.body, type_ignores=[])
        assert "_pi_clusters" not in _names_in(body)
        assert "_pi_c" not in _names_in(body)
        assert "warning" in _names_in(body)
    assert '_pi_view = corr_window.view_state(_pi_corr_df, _pi_cov, _pi_unchecked)' in _APP


# -- 6. other call sites ------------------------------------------------------
def test_unchecked_disclosure_gets_the_coverage():
    (c,) = _calls("unchecked_disclosure")
    kws = {k.arg: k.value for k in c.keywords}
    assert isinstance(kws.get("corr_coverage"), ast.Name) and kws["corr_coverage"].id == "_corr_cov"


def test_portfolio_health_renders_the_left_out_note():
    assert '_ph_subs["diversification"]["detail"].get("left_out_note")' in _APP


def test_analysis_matrix_uses_the_session_dated_builder_not_a_listwise_dropna():
    assert "corr = corr_window.corr_matrix(results)" in _APP
    assert "corr_window.corr_coverage(results)" in _APP
    assert "close_df.pct_change().dropna().corr()" not in _APP


def test_forward_sim_stop_note_names_a_left_out_stop_name():
    i = _APP.index("_fs_lo = [")
    seg = _APP[i:i + 900]
    assert "corr_window.left_out_items(_corr_cov)" in seg
    assert "correlation reading: " in seg
    assert "corr_window.unconfirmed_reasons(" in seg


def test_forward_sim_thin_line_uses_the_pairwise_meaning():
    assert "its thinnest pair rests on only" in _APP


# -- 7. User Guide ------------------------------------------------------------
def test_user_guide_floor_is_the_constant():
    sentence = ("A holding with fewer than {n} trading days since its first close is left out "
                "of the correlation pairs, clusters and diversification score and named under "
                "them; each other pair is measured on all the days both stocks traded.")
    assert sentence.format(n=CORR_MIN_OBS_TRUSTED) in _APP
    assert ("the diversification score only raises Home's banner when it rests on enough "
            "shared days") in _APP
    assert "A holding with fewer than {n} trading days of history is left out of the clusters".format(
        n=CORR_MIN_OBS_TRUSTED) in _APP
    assert "🔗 Adds held — correlation couldn't be measured" in _APP


# -- the render rules ---------------------------------------------------------
def test_no_new_compare_against_the_floor_and_no_literal_in_the_new_texts():
    for n in ast.walk(_tree()):
        if isinstance(n, ast.Compare):
            assert "CORR_MIN_OBS_TRUSTED" not in _names_in(n)


def test_new_corryoung_texts_are_plain():
    # No bold marker and no dollar sign inside the texts C4 added.
    for needle in (
        "Correlation couldn't be measured this run",
        "The correlation reading couldn't be built this run",
        "Correlations can't be measured: fewer than 2 of these tickers",
        "Computed on each pair's own shared days",
        "Price history couldn't be loaded this run for",
    ):
        i = _APP.index(needle)
        seg = _APP[i:i + 260]
        # the literal's own source lines (up to the closing paren of the call)
        seg = seg.split(")\n", 1)[0]
        assert "**" not in seg and "$" not in seg, needle


# -- review round: the partial state names the left-out holding once ------------
def test_no_pairs_note_wording_uses_the_unchecked_rest_in_the_partial_state():
    assert ('_ra_wn_list = _ra_view["unchecked_rest"] if _ra_vs == "partial" else _corr_unchecked'
            in _APP)
    i = _APP.index("_ra_wn_list = ")
    seg = _APP[i:i + 700]
    assert _norm("correlation_record_withheld_note( _ra_wn_list, _corr_cov,") in _norm(seg)
    # the verified boolean that picks success vs info stays on the raw list
    assert "_ra_corr_ok = correlation_claim_verified(_corr_unchecked, _corr_cov)" in _APP


def test_clusters_no_cluster_note_wording_uses_the_unchecked_rest_in_the_partial_state():
    assert _norm('_pi_view["unchecked_rest"] if _pi_view["state"] == "partial" else _pi_unchecked') in _norm(_APP)
    assert "_pi_corr_ok = correlation_claim_verified(_pi_unchecked, _pi_cov)" in _APP
    i = _APP.index("_pi_wn_list = (")
    assert _norm("correlation_record_withheld_note( _pi_wn_list, _pi_cov,") in _norm(_APP[i:i + 800])


def _count_note_renders(root, view):
    """How many `st.caption(<note>)` renders of the left-out note a page's
    partial path can reach: the notes loops."""
    return [n for n in ast.walk(root) if isinstance(n, ast.For)
            and view in _names_in(n.iter) and "notes" in _strings_in(n.iter)]


def test_risk_analysis_partial_renders_the_notes_in_one_place_only():
    ra = _page_block("🔗 Risk Analysis")
    # the measured branch renders view["notes"] once; the withheld branch reads
    # them through `_ra_notes` (a separate path, never both)
    loops = _count_note_renders(ra, "_ra_view")
    assert len(loops) == 1, len(loops)
    withheld = _if_with_state_test(ra, "_ra_vs", "withheld")
    wbody = ast.Module(body=withheld.body, type_ignores=[])
    assert not any(loops[0] is x for x in ast.walk(wbody))
    assert len([n for n in ast.walk(wbody) if isinstance(n, ast.For)
                and "_ra_notes" in _names_in(n.iter)]) == 1


def test_clusters_partial_renders_the_notes_once_per_path():
    pi = _page_block("🧩 Intelligence")
    loops = _count_note_renders(pi, "_pi_view")
    # empty-matrix withheld, thin withheld, no-clusters (not ok), clusters found
    assert len(loops) == 4, len(loops)


# -- review round: smaller sites -------------------------------------------------
def test_analysis_matrix_caption_has_no_score_tail():
    i = _APP.index("# Not left_out_note: its")
    seg = _APP[i:i + 900]
    assert "risk_window.holdings_phrase(" in seg and "with_size=False" in seg
    assert '"Not in this matrix: "' in seg
    assert "corr_window.left_out_note(" not in seg


def test_none_state_failure_message_needs_two_priced_holdings():
    i = _APP.index("_ra_priced = sum(")
    seg = _APP[i - 600:i + 900]
    assert "_ra_priced >= 2" in seg and "len(held_data) >= 2" not in seg
    assert 'Need at least 2 holdings with price history to compute correlations.' in _APP


def test_unused_floor_import_is_gone_from_app():
    assert "CORR_MIN_OBS_TRUSTED" not in _APP


def test_user_guide_says_the_pause_lives_in_grow_today():
    assert ("adds that crossed the line stay paused until correlation can be measured "
            "(the pause lives in Grow Today).") in _APP
