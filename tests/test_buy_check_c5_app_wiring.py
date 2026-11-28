"""Buy-check C5, chunk A2: the app.py wiring of a partly priced book
(docs/plans/entry-fail-closed-c5-spec.md section 3 items 9-13; owner QA2).

No test imports app.py, so the render-only wiring is pinned on its AST (parsed once
through tests._ast_cache, never ast.parse on app.py). The decisions themselves are
tested in test_buy_check_c5_partial_book.py and test_watchlist_fit_source.py.
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import buy_check_state as bcs, sector_unpriced as su
from tests._ast_cache import parse_file_cached, source_segment

pytestmark = pytest.mark.fast

_APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
_SRC = _APP_PATH.read_text(encoding="utf-8")
_TREE = parse_file_cached(_APP_PATH)


def _nodes(cls):
    return [n for n in ast.walk(_TREE) if isinstance(n, cls)]


def _str_consts(value):
    return [n for n in _nodes(ast.Constant) if n.value == value]


def _loads(name):
    return [n for n in _nodes(ast.Name) if n.id == name and isinstance(n.ctx, ast.Load)]


def _calls_to(dotted):
    out = []
    for n in _nodes(ast.Call):
        f = n.func
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f"{f.value.id}.{f.attr}" == dotted):
            out.append(n)
    return out


# ───────────────────── item 10: Grow Today render ────────────────────────────────
def test_fit_unpriced_picks_is_read_everywhere_beta_unknown_picks_is():
    # the four key reads (_grow_shown list, the bucket local, the held-out groups,
    # the drop-off buckets dict): one sibling read each
    assert len(_str_consts("beta_unknown_picks")) == len(_str_consts("fit_unpriced_picks")) >= 4


def test_the_picks_hold_group_renders_beside_the_beta_group():
    groups = [n for n in _nodes(ast.Tuple)
              if any(isinstance(e, ast.Constant) and e.value == "⏸ Sector weight incomplete"
                     for e in n.elts)]
    assert len(groups) == 1
    assert ast.unparse(groups[0].elts[1]) == "fit_unpriced_picks"
    beta = [n for n in _nodes(ast.Tuple)
            if any(isinstance(e, ast.Constant) and e.value == "⏸ Beta couldn't be checked"
                   for e in n.elts)]
    assert len(beta) == 1 and ast.unparse(beta[0].elts[1]) == "beta_unknown_picks"


def test_the_held_out_note_and_the_held_adds_group_name_the_new_hold():
    assert len(_str_consts("⏸ sector weight incomplete")) == 1       # cleared-the-bar note
    assert len(_str_consts("⏸ Adds held — sector weight incomplete")) == 1
    seg = _SRC.split("elif _eh.get(\"check\") == sector_unpriced.CHECK:", 1)[1][:900]
    # a full-sentence reason: its own wording, never the generic "held: …" wrapper
    assert "would qualify as an add, but" in seg and "_eh_fit_rows.append" in seg


def test_the_pick_line_does_not_double_the_clear_condition():
    seg = _SRC.split("if _bg.get(\"kind\") == sector_unpriced.CHECK:", 1)[1][:900]
    assert "would qualify as a pick, but" in seg and "Clears when " in seg
    # the beta rows keep their original "Clears {clears_when}." wording
    assert "elif _bg.get(\"clears_when\"):" in seg


# ───────────────────── item 9: Watchlist banner ──────────────────────────────────
def test_the_watchlist_passes_the_fit_states_unpriced_map_to_the_banner():
    calls = _calls_to("watchlist_fit_source.banner")
    assert len(calls) == 1
    kws = {k.arg: k.value for k in calls[0].keywords}
    assert {"source", "fit_state", "n_holdings", "n_priced", "unpriced"} <= set(kws)
    assert ast.unparse(kws["unpriced"]) == "_wl_fit_unpriced"
    assigns = [n for n in _nodes(ast.Assign)
               if any(isinstance(t, ast.Name) and t.id == "_wl_fit_unpriced" for t in n.targets)]
    assert len(assigns) == 1
    assert ast.unparse(assigns[0].value) == "_wl_fit_state.get('unpriced')"
    # the attrs reader is gone from the page and from the module
    assert "unpriced_tickers" not in _SRC


# ───────────────────── item 11: Analysis Gate checks tile ────────────────────────
def test_the_analysis_sector_tile_reads_the_pure_tile_and_keeps_the_size():
    calls = _calls_to("sector_unpriced.analysis_tile")
    assert len(calls) == 1
    seg = source_segment(_SRC, calls[0])
    assert "buy_check_state.unpriced_holdings(" in seg and "_gc_sec" in seg
    rows = [n for n in _nodes(ast.Tuple)
            if len(n.elts) == 3 and isinstance(n.elts[0], ast.Constant)
            and n.elts[0].value == "Sector cap"]
    assert len(rows) == 1
    assert ast.unparse(rows[0].elts[1]) == "_gate_sector_ok"
    assert ast.unparse(rows[0].elts[2]) == "_gate_sector_detail"


def test_the_pure_tile_reads_the_held_rows_and_the_frame_the_page_passes():
    frame = pd.DataFrame({"Ticker": ["AAA"], "Price": [10.0], "Shares": [1.0]})
    unpriced = bcs.unpriced_holdings(["AAA", "ANT"], frame)       # ticker strings, as
    assert list(unpriced) == ["ANT"]                              # _last_held_tickers
    sector = unpriced["ANT"]
    if sector != bcs.UNPRICED_ALL:
        assert su.analysis_tile(sector, unpriced) == (None, "Weight incomplete (ANT unpriced)")
    assert su.analysis_tile("", unpriced) is None                  # unclassified: not here
    assert su.analysis_tile("Technology", bcs.unpriced_holdings(None, frame)) is None


# ───────────────────── item 12: memo signature ───────────────────────────────────
def test_the_home_memo_signature_carries_the_unpriced_map():
    sig = [n for n in _nodes(ast.Assign)
           if any(isinstance(t, ast.Name) and t.id == "_synth_sig" for t in n.targets)]
    assert len(sig) == 1
    assert "_synth_unpriced" in ast.unparse(sig[0].value)
    feed = [n for n in _nodes(ast.Assign)
            if any(isinstance(t, ast.Name) and t.id == "_synth_unpriced" for t in n.targets)]
    assert len(feed) == 1
    assert "buy_check_state.unpriced_holdings(" in ast.unparse(feed[0].value)
    # an unreadable map (None) must stay distinct from "all priced" ({})
    assert "None if _synth_unpriced is None" in ast.unparse(sig[0].value)


def test_the_signature_element_is_hashable_for_both_map_states():
    for m in (None, {}, {"ANT": "Technology"}):
        el = None if m is None else frozenset(m.items())
        hash(el)


# ───────────────────── item 13: User Guide ───────────────────────────────────────
@pytest.mark.parametrize("needle", [
    "every Enter Now in its sector is held",                       # Watchlist
    '"⏸ Sector weight incomplete"',                                 # Grow Today
    'reads grey — with "Weight incomplete"',                       # Analysis
])
def test_the_user_guide_says_it(needle):
    guide = _SRC.split('elif page == "📖 User Guide":', 1)[1]
    assert needle in guide
    assert "reads low until a later load" not in guide
