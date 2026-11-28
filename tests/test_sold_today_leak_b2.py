"""Buy-check C5 chunk B2 (docs/plans/entry-fail-closed-c5-spec.md section 4, items 8-12 and
section 7 "AST pins"; owner QB1-QB4 2026-11-27): the app.py wiring of SoldTodayLeak.

No test imports app.py, so these are source / AST pins: every buy surface reads the STRICT
trade journal (`_budget_strict_trades()`, None = couldn't be read) instead of
`st.session_state.trades_df` (a failed read is an empty frame there = "nothing sold today"),
and the unread journal reaches the check, the banner and the Brief memo.  B1's pure modules
are pinned in tests/test_sold_today_leak_b1.py.  No network, no DB.
"""
import ast
import pathlib

import pytest

from tests._ast_cache import parse_file_cached, source_segment

pytestmark = pytest.mark.fast

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = (ROOT / "app.py").read_text(encoding="utf-8")
TREE = parse_file_cached(ROOT / "app.py")


def _calls(name):
    return [n for n in ast.walk(TREE)
            if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id == name)
                 or (isinstance(n.func, ast.Attribute) and n.func.attr == name))]


def _kw(call, key):
    for k in call.keywords:
        if k.arg == key:
            return k.value
    return None


def _assigns(target_name):
    out = []
    for n in ast.walk(TREE):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == target_name:
                    out.append(n)
    return out


# ── tickers_sold_on never reads the session frame ─────────────────────────────
def test_no_app_tickers_sold_on_call_reads_the_session_trades_frame():
    calls = _calls("tickers_sold_on")
    assert len(calls) >= 2          # Watchlist + Analysis
    for c in calls:
        seg = source_segment(APP, c)
        assert '"trades_df"' not in seg and "'trades_df'" not in seg, seg
        # the argument is the strict read (directly or through the name bound to it)
        arg = c.args[0]
        if isinstance(arg, ast.Call):
            assert isinstance(arg.func, ast.Name) and arg.func.id == "_budget_strict_trades"
        else:
            assert isinstance(arg, ast.Name) and arg.id == "_wl_trades"


def test_watchlist_reads_the_strict_journal_once_and_passes_trades_read():
    (a,) = _assigns("_wl_trades")
    assert source_segment(APP, a.value) == "_budget_strict_trades()"
    (r,) = _assigns("_wl_trades_read")
    assert source_segment(APP, r.value) == "_wl_sold_today_raw is not None"


def test_analysis_reads_the_strict_journal_and_passes_trades_read():
    (a,) = _assigns("_an_sold")
    seg = source_segment(APP, a.value)
    assert "tickers_sold_on(_budget_strict_trades(), _today_et())" in seg


# ── both watchlist_run_ctx calls + both fallback dicts carry trades_read ───────
def test_both_run_ctx_calls_pass_trades_read():
    calls = _calls("watchlist_run_ctx")
    assert len(calls) == 2
    got = sorted(source_segment(APP, _kw(c, "trades_read")) for c in calls)
    assert got == ["_an_sold is not None", "_wl_trades_read"]


def test_both_run_ctx_fallback_dicts_carry_trades_read():
    found = {}
    for n in ast.walk(TREE):
        if not isinstance(n, ast.ExceptHandler):
            continue
        for stmt in n.body:
            if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Dict):
                continue
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id in ("_an_run_ctx", "_wl_run_ctx"):
                    keys = [k.value for k in stmt.value.keys if isinstance(k, ast.Constant)]
                    found[t.id] = keys
    assert set(found) == {"_an_run_ctx", "_wl_run_ctx"}
    for name, keys in found.items():
        assert "trades_read" in keys and "tone_state" in keys and "macro_map" in keys, name


def test_watchlist_banner_gets_trades_read_and_renders_the_unread_box_escaped():
    (c,) = _calls("watchlist_banner")
    assert source_segment(APP, _kw(c, "trades_read")) == "_wl_trades_read"
    i = APP.index('if _wl_banner["trades_unread"] is not None:')
    seg = APP[i:i + 1200]
    assert "_safe_html(_wl_tu['title'])" in seg and "_safe_html(_wl_tu['body'])" in seg
    assert "unsafe_allow_html=True" in seg
    # same amber box shape as the tone / macro unread boxes
    assert "border-left:4px solid #f59e0b" in seg


# ── Home Brief: one strict read feeds the signature and both builds ────────────
def test_both_brief_builds_pass_trades_df_and_trades_unread_from_the_same_name():
    calls = _calls("build_daily_briefing")
    assert len(calls) == 2
    for c in calls:
        df = _kw(c, "trades_df")
        unread = _kw(c, "trades_unread")
        assert isinstance(df, ast.Name) and df.id == "_brief_trades"
        assert source_segment(APP, unread) == "_brief_trades is None"


def test_brief_trades_is_the_strict_read_and_is_in_the_memo_signature():
    (a,) = _assigns("_brief_trades")
    # one strict read per Home run (Opus SoldTodayLeak review note 1): the held-lot read
    (h,) = _assigns("_hd_trades")
    assert source_segment(APP, h.value) == "_budget_strict_trades()"
    assert source_segment(APP, a.value) == "_hd_trades"
    assert h.lineno < a.lineno
    (sig,) = _assigns("_synth_sig")
    assert "_brief_trades is None" in source_segment(APP, sig.value)
    # read BEFORE the signature that uses it
    assert a.lineno < sig.lineno


def test_home_held_lot_read_is_the_strict_read():
    (a,) = _assigns("_hd_trades")
    assert source_segment(APP, a) == "_hd_trades = _budget_strict_trades()"


def test_synth_schema_version_is_bumped_for_the_journal_hold():
    i = APP.index("_SYNTH_SCHEMA_VER = ")
    line = APP[i:APP.index("\n", i)]
    n = int(line.split("=")[1].split("#")[0])
    assert n >= 44
    assert "trades_unread" in line and "SoldTodayLeak" in line
    # the history is kept, newest first
    assert "was 43:" in line and "was 14:" in line


# ── Grow Today render, Analysis, Trade Journal ─────────────────────────────────
def test_grow_today_render_shows_the_journal_retry_caption_inside_the_hold_block():
    fn = next(n for n in ast.walk(TREE)
              if isinstance(n, ast.FunctionDef) and n.name == "_render_grow_today")
    src = source_segment(APP, fn)
    block = src[src.index('grow.get("entry_hold")'):src.index("if bear_msg:")]
    assert "journal_read.retry_caption(_entry_hold_ui)" in block
    assert block.index("st.info(") < block.index("journal_read.retry_caption(")
    assert block.index("journal_read.retry_caption(") < block.index(
        "_render_grow_pacing_disclosures")


def test_analysis_marks_a_not_held_name_sold_today_after_the_held_branch():
    i = APP.index("_an_entry[_t] = _sizing_path.analysis_offer_held_add(")
    seg = APP[i:i + 900]
    assert "elif _an_sold and str(_t).strip().upper() in _an_sold:" in seg
    assert "journal_read.analysis_sold_today(" in seg


def test_trade_journal_buy_confirm_shows_the_same_day_sell_note_as_a_caption():
    i = APP.index("_pb_sold_note = journal_read.same_day_sell_note(")
    seg = APP[i:i + 500]
    assert "_budget_strict_trades()" in seg
    assert '_pending_buy.get("traded_at")' in seg
    assert "today=_today_et()" in seg
    # a caption with `$` escaped, never a block
    assert 'st.caption(_pb_sold_note.replace("$", "\\\\$"))' in seg
    assert "disabled" not in seg


def test_journal_read_is_imported_by_the_app():
    assert "from stock_analyzer import journal_read" in APP


# ── User Guide ─────────────────────────────────────────────────────────────────
def test_user_guide_describes_the_journal_hold_on_the_four_surfaces():
    guide = APP[APP.index('elif page == "📖 User Guide":'):]
    assert "**When the trade journal can't be read (since 2026-11-28).**" in guide   # Grow Today
    assert "amber **Trade journal couldn't be read** box" in guide                    # Watchlist
    assert "*held: you sold it today*" in guide                                       # Analysis
    assert "the note never blocks the entry" in guide                                 # Trade Journal
