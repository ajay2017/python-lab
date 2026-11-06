"""RbPartial: hold on mismatch.

The four app.py sites that replay the trade journal and save the result over
holdings may only auto-save when the journal explains the book (current holdings
+/- exactly the trades just deleted / imported == the replay, for every ticker).

Pure tests of `journal_rebuild.replay_check` / `diff_holdings` / `hold_message` /
`same_holdings`, plus source/AST pins on app.py (no test imports it).
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import journal_rebuild as jr
from tests._ast_cache import parse_file_cached, source_segment

_APP = Path(__file__).resolve().parents[1] / "app.py"


def _h(**kw):
    """Holdings frame from {TICKER: shares}."""
    return pd.DataFrame([{"Ticker": k, "Shares": v, "Avg Cost ($)": 10.0}
                         for k, v in kw.items()],
                        columns=["Ticker", "Shares", "Avg Cost ($)"])


def _t(ticker, action, shares):
    return {"ticker": ticker, "action": action, "shares": shares}


# ── replay_check ────────────────────────────────────────────────────────────

def test_delete_mistaken_buy_is_safe():
    # current AAPL 10 includes the mistaken BUY of 4; replay without it is 6.
    r = jr.replay_check(_h(AAPL=10), _h(AAPL=6), [_t("AAPL", "BUY", 4)], mode="delete")
    assert r == {"safe": True, "reason": None, "mismatches": []}


def test_delete_sell_whose_ticker_has_no_buy_is_unsafe():
    r = jr.replay_check(_h(X=5), _h(), [_t("X", "SELL", 3)], mode="delete")
    assert r["safe"] is False and r["reason"] == "mismatch"
    assert r["mismatches"] == [{"ticker": "X", "held": 5.0, "expected": 8.0, "replay": 0.0}]


def test_untouched_ticker_missing_from_replay_is_unsafe():
    r = jr.replay_check(_h(AAPL=10, MSFT=7), _h(AAPL=6), [_t("AAPL", "BUY", 4)], mode="delete")
    assert r["safe"] is False
    assert [m["ticker"] for m in r["mismatches"]] == ["MSFT"]
    assert r["mismatches"][0] == {"ticker": "MSFT", "held": 7.0, "expected": 7.0, "replay": 0.0}


def test_untouched_ticker_with_different_shares_is_unsafe():
    # manual holdings edit: held 9, journal says 7
    r = jr.replay_check(_h(AAPL=10, MSFT=9), _h(AAPL=6, MSFT=7),
                        [_t("AAPL", "BUY", 4)], mode="delete")
    assert r["safe"] is False
    assert r["mismatches"] == [{"ticker": "MSFT", "held": 9.0, "expected": 9.0, "replay": 7.0}]


def test_import_buy_of_new_ticker_is_safe():
    r = jr.replay_check(_h(AAPL=10), _h(AAPL=10, NVDA=3), [_t("NVDA", "BUY", 3)], mode="import")
    assert r["safe"] is True and r["mismatches"] == []


def test_import_sell_reduces_expected():
    r = jr.replay_check(_h(AAPL=10), _h(AAPL=6), [_t("AAPL", "SELL", 4)], mode="import")
    assert r["safe"] is True


def test_import_the_journal_cannot_reconcile_is_unsafe():
    # imported SELL of NVDA with no BUY anywhere: replay has no NVDA, book has 5.
    r = jr.replay_check(_h(NVDA=5), _h(), [_t("NVDA", "SELL", 2)], mode="import")
    assert r["safe"] is False
    assert r["mismatches"] == [{"ticker": "NVDA", "held": 5.0, "expected": 3.0, "replay": 0.0}]


def test_float_noise_is_safe():
    r = jr.replay_check(_h(AAPL=0.1 + 0.2), _h(AAPL=0.3), [], mode="import")
    assert r["safe"] is True
    r2 = jr.replay_check(_h(AAPL=0.3), _h(AAPL=0.1), [_t("AAPL", "BUY", 0.2)], mode="delete")
    assert r2["safe"] is True


@pytest.mark.parametrize("action", ["SPLIT", "DIVIDEND", ""])
def test_non_buy_sell_row_is_unpredictable(action):
    r = jr.replay_check(_h(AAPL=10), _h(AAPL=10), [_t("AAPL", action, 20)], mode="delete")
    assert r == {"safe": False, "reason": "unpredictable_trade", "mismatches": []}


@pytest.mark.parametrize("bad", [None, "abc", float("nan")])
def test_unparseable_shares_is_unreadable_trade(bad):
    r = jr.replay_check(_h(AAPL=10), _h(AAPL=10), [_t("AAPL", "BUY", bad)], mode="import")
    assert r == {"safe": False, "reason": "unreadable_trade", "mismatches": []}


@pytest.mark.parametrize("tk", ["", "   ", None])
def test_blank_ticker_is_unreadable_trade(tk):
    r = jr.replay_check(_h(AAPL=10), _h(AAPL=10), [_t(tk, "BUY", 1)], mode="delete")
    assert r == {"safe": False, "reason": "unreadable_trade", "mismatches": []}


def test_split_with_unusable_shares_stays_unpredictable():
    # the action is checked first: a SPLIT is a split whatever its share field says.
    r = jr.replay_check(_h(AAPL=10), _h(AAPL=10), [_t("AAPL", "SPLIT", None)], mode="delete")
    assert r["reason"] == "unpredictable_trade"


def test_bad_mode_raises():
    with pytest.raises(ValueError):
        jr.replay_check(_h(AAPL=1), _h(AAPL=1), [], mode="rebuild")


def test_current_none_is_holdings_unreadable():
    r = jr.replay_check(None, _h(AAPL=1), [], mode="delete")
    assert r == {"safe": False, "reason": "holdings_unreadable", "mismatches": []}


def test_tickers_match_case_insensitively_and_stripped():
    cur = pd.DataFrame([{"Ticker": " aapl ", "Shares": 10.0}])
    rep = pd.DataFrame([{"Ticker": "AAPL", "Shares": 6.0}])
    r = jr.replay_check(cur, rep, [{"ticker": "Aapl ", "action": "buy", "shares": 4}], mode="delete")
    assert r["safe"] is True


def test_changed_trades_may_be_a_dataframe():
    df = pd.DataFrame([_t("AAPL", "BUY", 4)])
    assert jr.replay_check(_h(AAPL=10), _h(AAPL=6), df, mode="delete")["safe"] is True


def test_mismatches_sorted_by_ticker():
    r = jr.replay_check(_h(ZZZ=1, AAA=2), _h(), [], mode="import")
    assert [m["ticker"] for m in r["mismatches"]] == ["AAA", "ZZZ"]


def test_empty_current_and_replay_with_no_trades_is_safe():
    assert jr.replay_check(_h(), _h(), [], mode="delete")["safe"] is True


# ── hold_message ────────────────────────────────────────────────────────────

def _mm(n):
    return {"safe": False, "reason": "mismatch",
            "mismatches": [{"ticker": f"T{i}", "held": 5.0, "expected": 5.0, "replay": 0.0}
                           for i in range(n)]}


def test_hold_message_mismatch_names_capped_at_five():
    m = jr.hold_message("Deleted 2 trades", _mm(8))
    assert "T0 (held 5; after this change it should be 5; the trade history gives 0)" in m
    assert "T4 (" in m
    assert "T5" not in m and "and 3 more" in m
    assert "not** rebuilt" in m and "Nothing was changed" in m
    assert "Run rebuild now" in m
    assert "<" not in m


def test_hold_message_exactly_five_has_no_more_suffix():
    m = jr.hold_message("Imported 1 trade(s)", _mm(5))
    assert "more" not in m and "T4 (" in m


def test_hold_message_shares_formatted_with_g():
    chk = {"safe": False, "reason": "mismatch",
           "mismatches": [{"ticker": "AAPL", "held": 2.5, "expected": 2.5, "replay": 1.0}]}
    assert ("AAPL (held 2.5; after this change it should be 2.5; "
            "the trade history gives 1)") in jr.hold_message("x", chk)


def test_hold_message_shows_expected_when_held_equals_replay():
    # Import that backfills pre-journal trades: held 50, should be 100, history gives 50.
    r = jr.replay_check(_h(AAPL=50), _h(AAPL=50), [_t("AAPL", "BUY", 50)], mode="import")
    assert r["mismatches"] == [{"ticker": "AAPL", "held": 50.0, "expected": 100.0, "replay": 50.0}]
    m = jr.hold_message("Imported 1 trade(s)", r)
    assert "AAPL (held 50; after this change it should be 100; the trade history gives 50)" in m
    assert "held 50, history gives 50" not in m


def test_hold_message_other_reasons():
    u = jr.hold_message("Deleted 1 trade", {"safe": False, "reason": "holdings_unreadable", "mismatches": []})
    assert "couldn't be read" in u and "Nothing was changed" in u and "<" not in u
    s = jr.hold_message("Deleted 1 trade", {"safe": False, "reason": "unpredictable_trade", "mismatches": []})
    assert "split" in s and "Nothing was changed" in s and "<" not in s
    ur = jr.hold_message("Deleted 1 trade", {"safe": False, "reason": "unreadable_trade", "mismatches": []})
    assert "no usable ticker or share count" in ur and "split" not in ur
    assert "Nothing was changed" in ur and "Run rebuild now" in ur and "<" not in ur
    cf = jr.hold_message("Deleted 1 trade", {"safe": False, "reason": "capture_failed", "mismatches": []})
    assert "couldn't be matched to the trade history on screen" in cf and "split" not in cf
    assert "Nothing was changed" in cf and "Run rebuild now" in cf and "<" not in cf
    assert len({u, s, ur, cf}) == 4


# ── diff_holdings ───────────────────────────────────────────────────────────

def test_diff_holdings_removed_added_changed():
    d = jr.diff_holdings(_h(AAPL=10, MSFT=5, ZZZ=1), _h(AAPL=6, MSFT=5, NVDA=2))
    assert d == {"removed": ["ZZZ"], "added": ["NVDA"],
                 "changed": [{"ticker": "AAPL", "held": 10.0, "replay": 6.0}]}


def test_diff_holdings_none_current_is_none():
    assert jr.diff_holdings(None, _h(AAPL=1)) is None


def test_diff_holdings_float_noise_not_changed():
    d = jr.diff_holdings(_h(AAPL=0.1 + 0.2), _h(AAPL=0.3))
    assert d == {"removed": [], "added": [], "changed": []}


# ── same_holdings ───────────────────────────────────────────────────────────

def test_same_holdings():
    assert jr.same_holdings(_h(AAPL=1, MSFT=2), _h(MSFT=2, AAPL=1))
    assert jr.same_holdings(_h(AAPL=0.1 + 0.2), _h(AAPL=0.3))
    assert jr.same_holdings(_h(), _h())
    assert not jr.same_holdings(_h(AAPL=1), _h(AAPL=2))
    assert not jr.same_holdings(_h(AAPL=1), _h(AAPL=1, MSFT=1))
    assert not jr.same_holdings(None, _h())
    assert jr.same_holdings(pd.DataFrame([{"Ticker": "aapl ", "Shares": 1.0}]), _h(AAPL=1))


# ── app.py source / AST pins ────────────────────────────────────────────────

def _src_lines():
    return _APP.read_text(encoding="utf-8").splitlines()


def _save_calls_with_arg(text):
    """(lineno) of every db.save_holdings( call whose first argument source is `text`."""
    src = _APP.read_text(encoding="utf-8")
    out = []
    for node in ast.walk(parse_file_cached(_APP)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "save_holdings" and node.args
                and source_segment(src, node.args[0]) == text):
            out.append(node.lineno)
    return out


@pytest.mark.parametrize("arg,guard", [
    ('recalc["holdings_df"]', "_journal_replay_check("),
    ('_bi_recalc["holdings_df"]', "_journal_replay_check("),
    ('_ss_recalc["holdings_df"]', "_journal_replay_check("),
    ("new_h2", "_journal_same_holdings("),
])
def test_replay_fed_save_is_guarded_just_before(arg, guard):
    calls = _save_calls_with_arg(arg)
    assert len(calls) == 1, f"expected one save_holdings({arg}) site, found {calls}"
    lines = _src_lines()
    window = "\n".join(lines[max(0, calls[0] - 61):calls[0] - 1])
    assert guard in window, f"{guard} not within 60 lines before the save at line {calls[0]}"


def test_no_other_save_holdings_is_fed_by_a_replay_unguarded():
    """Only the four known args may feed a replay result into save_holdings."""
    src = _APP.read_text(encoding="utf-8")
    seen = []
    for node in ast.walk(parse_file_cached(_APP)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "save_holdings" and node.args):
            seg = source_segment(src, node.args[0]) or ""
            if "recalc" in seg or seg in ("new_h", "new_h2"):
                seen.append(seg)
    assert sorted(seen) == sorted(['recalc["holdings_df"]', '_bi_recalc["holdings_df"]',
                                   '_ss_recalc["holdings_df"]', "new_h2"])


def _is_run_rebuild_button_if(node):
    if not isinstance(node, ast.If):
        return False
    t = node.test
    return (isinstance(t, ast.Call) and isinstance(t.func, ast.Attribute)
            and t.func.attr == "button" and t.args
            and isinstance(t.args[0], ast.Constant) and t.args[0].value == "Run rebuild now")


def test_run_rebuild_click_only_previews():
    found = [n for n in ast.walk(parse_file_cached(_APP)) if _is_run_rebuild_button_if(n)]
    assert len(found) == 1
    for sub in ast.walk(found[0]):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            assert sub.func.attr not in ("save_holdings", "update_trade_realized_pnl"), (
                f"'Run rebuild now' click writes ({sub.func.attr}, line {sub.lineno}); "
                "it must only preview")


def test_apply_and_cancel_buttons_exist():
    src = _APP.read_text(encoding="utf-8")
    assert 'key="_tj_rebuild_apply_btn"' in src
    assert 'key="_tj_rebuild_cancel_btn"' in src
    assert '"_tj_rebuild_preview"' in src


def _guard_if_nodes(tree, needle):
    """If nodes whose test source mentions `needle`."""
    src = _APP.read_text(encoding="utf-8")
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.If) and needle in (source_segment(src, n.test) or "")]


def _in_orelse(if_node, call_line):
    for stmt in if_node.orelse:
        for sub in ast.walk(stmt):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "save_holdings" and sub.lineno == call_line):
                return True
    return False


@pytest.mark.parametrize("arg,needle", [
    ('recalc["holdings_df"]', '["safe"]'),
    ('_bi_recalc["holdings_df"]', '["safe"]'),
    ('_ss_recalc["holdings_df"]', '["safe"]'),
    ("new_h2", "_journal_same_holdings"),
])
def test_replay_fed_save_sits_in_the_orelse_of_its_guard(arg, needle):
    """The save must be the elif/else of the guard `if`, so an `elif` turned
    into a bare `if` (which would save after a hold) fails this pin."""
    tree = parse_file_cached(_APP)
    (line,) = _save_calls_with_arg(arg)
    guards = _guard_if_nodes(tree, needle)
    assert guards, f"no `if` testing {needle} found"
    assert any(_in_orelse(g, line) for g in guards), (
        f"save_holdings({arg}) at line {line} is not in the orelse of an `if` testing {needle}")
