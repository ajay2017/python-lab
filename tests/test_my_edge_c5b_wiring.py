"""C5b wiring pins: 🎯 My Edge's Self Track Record passes rec_events to both
classifiers, shows the stop_unlogged disclosure + the matched-call coverage
caption and the dated note; the Investigator bundle carries rec_events_df.
app.py isn't importable in tests, so these read its source (AST)."""
import ast
from pathlib import Path

from tests._ast_cache import parse_file_cached, source_segment

_SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
_TREE = parse_file_cached(Path(__file__).resolve().parent.parent / "app.py")


def _calls(attr):
    return [n for n in ast.walk(_TREE)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


def _kw(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


def test_both_classifiers_receive_rec_events_df():
    for fn in ("classify_buys", "classify_sells"):
        calls = _calls(fn)
        assert len(calls) == 1, fn
        v = _kw(calls[0], "rec_events_df")
        assert isinstance(v, ast.Name) and v.id == "_stv_rec_events_df", fn


def test_rec_events_loader_returns_none_on_failure():
    fn = next(n for n in ast.walk(_TREE)
              if isinstance(n, ast.FunctionDef) and n.name == "_rec_events_frame_or_none")
    src = source_segment(_SRC, fn)
    assert "db.load_rec_events()" in src
    assert "return None" in src
    assert "or []" not in src and "or {}" not in src


def test_stop_unlogged_disclosure_renders():
    assert "_sts_summary.get(\"n_stop_unlogged\")" in _SRC
    assert "tagged STOP_HIT with" in _SRC
    assert "stop sales aren't logged by the app" in _SRC


def test_coverage_and_unreadable_captions_sit_above_both_sub_tabs():
    tabs = _SRC.index('st.tabs(["📈 Buy-Side", "📉 Sell-Side"])')
    for needle in ("_stv.matched_call_coverage_caption(",
                   "Rebalance / beta-trim / diversify calls couldn't be read"):
        i = _SRC.index(needle)
        assert i < tabs and tabs - i < 3000, needle


def test_dated_note_precedes_the_sub_tabs_and_names_the_stop_rule():
    note = _SRC.index("📅 Since 2026-10-20 a buy also counts as app-aligned")
    tabs = _SRC.index('st.tabs(["📈 Buy-Side", "📉 Sell-Side"])')
    assert 0 < tabs - note < 3000
    assert "tagged as a stop-hit with no matching app call is no longer" in _SRC


def test_head_to_head_help_names_every_exit_call():
    assert "An active EXIT/TRIM signal was on file" not in _SRC
    assert "No active EXIT/TRIM signal on file" not in _SRC
    assert "An app exit call (EXIT, TRIM, risk-off, rebalance or beta" in _SRC


def test_unreadable_rec_events_is_disclosed():
    assert "Rebalance / beta-trim / diversify calls couldn't be read" in _SRC


def test_investigator_bundle_carries_rec_events_df():
    assert '"rec_events_df": _b_rec_events_df' in _SRC
