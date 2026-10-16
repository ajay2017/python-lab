"""Chunk 4 of docs/plans/daily-new-position-budget.md -- the in-app (Home /
Grow Today) wiring of the daily new-position budget.

`stock_analyzer.budget_view` is the pure post-pass; app.py is render-only
wiring and no test imports it, so its placement / storage rules are pinned
structurally (AST / source order) at the bottom of this file. Every
"withheld / unchanged" test has a firing control, and the mutations that were
run against each are listed in the build report.
"""
import ast
import copy
import datetime
import pathlib
import re

import pandas as pd
import pytest

from stock_analyzer import budget_view as bv
from stock_analyzer import coord_freshness as cf
from stock_analyzer import daily_budget as dbud
from stock_analyzer import hold_reasons as hr
from stock_analyzer.constants import DAILY_NEW_POSITION_BUDGET_PCT

pytestmark = pytest.mark.fast

_ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = datetime.date(2026, 10, 16)
NOW = datetime.datetime(2026, 10, 16, 13, 50, tzinfo=datetime.timezone.utc)  # 09:50 ET
_COLS = ["traded_at", "action", "ticker", "shares", "price"]
# net capital 10,000 -> budget 40% = 4,000; 24 sh x $100 = 24% of net capital.
INPUTS = {"gross_book": 14000.0, "net_capital": 10000.0, "basis_state": "levered",
          "cash_balance": -4000.0, "cash_as_of": "2026-10-16T13:45:00+00:00"}
NO_TRADES = pd.DataFrame(columns=_COLS)


def _sizing(shares=24, price=100.0):
    return {"shares": shares, "total_cost": round(shares * price, 2),
            "stop": 94.0, "stop_pct": 6.0, "port_pct": 17.1, "risk_budget": 100.0,
            "entry_lo": 99.0, "entry_hi": 101.0, "ceiling_capped": False,
            "uncapped_shares": shares, "portfolio_value": 14000.0,
            "sizing_version": 3, "capital_capped": False, "capital_pct": float(shares)}


def _pick(tk, comp, shares=24, price=100.0, etf=False):
    p = {"ticker": tk, "composite_score": comp, "score": 70.0, "price": price,
         "sector": "Tech", "thesis": "t", "sizing": _sizing(shares, price)}
    if etf:
        p["asset_type"] = "etf"
    return p


def _add(tk, score, shares=24, price=100.0):
    return {"ticker": tk, "score": score, "gap": 9.0, "sector": "Tech",
            "thesis": "t", "sizing": _sizing(shares, price)}


def _brief(picks=(), adds=()):
    return {"act_today": [], "review_list": [],
            "grow_today": {"tone": "bull", "new_picks": list(picks),
                           "add_positions": list(adds), "overflow_picks": []}}


def _run(brief, trades=NO_TRADES, allocs=None, inputs=INPUTS, price_map=None):
    allocs = {} if allocs is None else allocs
    return bv.postpass(brief, inputs, trades, allocs, TODAY, NOW, price_map)


def _shares(out, kind, tk):
    key = "new_picks" if kind == "pick" else "add_positions"
    for it in out["brief"]["grow_today"][key]:
        if it["ticker"] == tk:
            return it["sizing"].get("shares")
    raise AssertionError(tk)


def _sz(out, kind, tk):
    key = "new_picks" if kind == "pick" else "add_positions"
    return next(it["sizing"] for it in out["brief"]["grow_today"][key]
                if it["ticker"] == tk)


# ------------------------------------------------------------ binding day
def test_binding_day_second_24pct_pick_shrinks_to_16pct_and_rec_row_state():
    out = _run(_brief([_pick("AAA", 80), _pick("BBB", 70)]))
    assert _shares(out, "pick", "AAA") == 24
    assert _shares(out, "pick", "BBB") == 16          # 4,000 - 2,400 = 1,600 left
    b = _sz(out, "pick", "BBB")
    assert b["total_cost"] == 1600.0 and b["capital_pct"] == 16.0
    assert b["budget_capped"] is True and b["budget_uncapped_shares"] == 24
    assert b["sizing_version"] == 4
    # the rec row app.py writes reads exactly these keys off the shown sizing
    assert bv.rec_budget_state(b) == hr.BUDGET_PARTIAL
    assert bv.rec_budget_state(_sz(out, "pick", "AAA")) is None
    assert out["cache"]["state"] == "ok"
    assert out["cache"]["used_list"] == pytest.approx(4000.0)
    assert out["cache"]["left"] == pytest.approx(0.0)


def test_control_non_binding_day_keeps_f255_sizes():
    out = _run(_brief([_pick("AAA", 80, shares=10), _pick("BBB", 70, shares=10)]))
    assert (_shares(out, "pick", "AAA"), _shares(out, "pick", "BBB")) == (10, 10)
    assert "budget_capped" not in _sz(out, "pick", "BBB")
    assert bv.rec_budget_state(_sz(out, "pick", "BBB")) is None


def test_exhausted_pick_keeps_its_call_but_loses_its_size():
    out = _run(_brief([_pick("AAA", 80, shares=40), _pick("BBB", 70, shares=24)]))
    assert _shares(out, "pick", "AAA") == 40            # 4,000 = the whole budget
    b = _sz(out, "pick", "BBB")
    assert "shares" not in b and b["budget_exhausted"] is True
    assert [p["ticker"] for p in out["brief"]["grow_today"]["new_picks"]] == ["AAA", "BBB"]
    assert bv.rec_budget_state(b) == hr.BUDGET_EXHAUSTED
    assert b["sizing_version"] == 4


# ------------------------------------------------ emailed (reserved) sizes
def test_emailed_allocation_is_reserved_at_locked_size_and_consumes_nothing_new():
    allocs = {"AAA": {"shares": 20, "dollars": 2000.0}}
    out = _run(_brief([_pick("AAA", 80, shares=24), _pick("BBB", 70, shares=24)]),
               allocs=allocs)
    a = _sz(out, "pick", "AAA")
    assert a["shares"] == 20 and a["budget_reserved"] is True   # the emailed size
    assert bv.rec_budget_state(a) == hr.BUDGET_RESERVED
    assert _shares(out, "pick", "BBB") == 20          # 4,000 - 2,000 reserved
    c = out["cache"]
    assert c["used_allocated"] == pytest.approx(2000.0)
    assert c["used_list"] == pytest.approx(2000.0)    # only BBB's NEW spend


def test_control_without_the_allocation_both_picks_size_independently():
    out = _run(_brief([_pick("AAA", 80, shares=24), _pick("BBB", 70, shares=24)]))
    assert (_shares(out, "pick", "AAA"), _shares(out, "pick", "BBB")) == (24, 16)
    assert "budget_reserved" not in _sz(out, "pick", "AAA")


def test_reserved_size_never_exceeds_the_current_f255_size():
    allocs = {"AAA": {"shares": 30, "dollars": 3000.0}}
    out = _run(_brief([_pick("AAA", 80, shares=24)]), allocs=allocs)
    assert _shares(out, "pick", "AAA") == 24


# ------------------------------------------------------------ logged buys
def _buy_today(tk, shares, price):
    return pd.DataFrame(
        [["2026-10-16T13:00:00+00:00", "BUY", tk, shares, price]], columns=_COLS)


def test_a_logged_buy_today_reduces_what_is_left_strict_read():
    out = _run(_brief([_pick("AAA", 80, shares=24)]),
               trades=_buy_today("ZZZ", 30, 100.0))       # $3,000 logged
    assert _shares(out, "pick", "AAA") == 10             # 4,000 - 3,000 = 1,000
    assert out["cache"]["used_logged"] == pytest.approx(3000.0)


def test_control_no_logged_buy_sizes_in_full():
    out = _run(_brief([_pick("AAA", 80, shares=24)]))
    assert _shares(out, "pick", "AAA") == 24


def test_logged_buys_alone_over_budget_sets_the_over_line():
    out = _run(_brief([_pick("AAA", 80)]), trades=_buy_today("ZZZ", 50, 100.0))
    assert out["cache"]["over_line_pct"] == pytest.approx(50.0)
    assert "50.0%" in bv.over_line_text(out["cache"])
    assert "nothing new sized today" in bv.over_line_text(out["cache"])
    assert bv.over_line_text(_run(_brief([_pick("AAA", 80)]))["cache"]) is None


# ------------------------------------------------------- can't-check states
def test_unreadable_trades_withhold_every_size_but_keep_the_buy_calls():
    out = _run(_brief([_pick("AAA", 80), _pick("BBB", 70)],
                      [_add("CCC", 85)]), trades=None)
    for kind, tk in (("pick", "AAA"), ("pick", "BBB"), ("add", "CCC")):
        s = _sz(out, kind, tk)
        assert "shares" not in s and s["budget_unknown"] is True
        assert "trade log" in s["budget_reason"]
        assert bv.rec_budget_state(s) == hr.BUDGET_UNKNOWN
    assert [p["ticker"] for p in out["brief"]["grow_today"]["new_picks"]] == ["AAA", "BBB"]
    assert out["cache"] is None                          # offline sentinel, never {}


def test_unreadable_allocations_withhold_every_size():
    out = _run(_brief([_pick("AAA", 80)]), allocs=None) \
        if False else bv.postpass(_brief([_pick("AAA", 80)]), INPUTS, NO_TRADES,
                                  None, TODAY, NOW)
    s = _sz(out, "pick", "AAA")
    assert "shares" not in s and "emailed sizes" in s["budget_reason"]
    assert out["cache"] is None


def test_no_cash_record_or_unknown_basis_withholds_sizes():
    for inputs in (bv.budget_inputs(14000.0, None, "unlevered", None),
                   bv.budget_inputs(14000.0, None, "stale", {"cash_balance": -1.0,
                                                             "updated_at": "2026-01-01"}),
                   bv.budget_inputs(14000.0, None, "unknown", None)):
        out = _run(_brief([_pick("AAA", 80)]), inputs=inputs)
        assert _sz(out, "pick", "AAA").get("budget_unknown") is True
        assert out["cache"] is None


def test_called_margin_state_sizes_nothing_and_says_so():
    inp = dict(INPUTS, net_capital=-500.0, basis_state="called")
    out = _run(_brief([_pick("AAA", 80)]), inputs=inp)
    s = _sz(out, "pick", "AAA")
    assert "shares" not in s and s["budget_exhausted"] is True
    assert out["cache"]["state"] == "called"
    note = bv.card_note(s, "pick", called=True)
    assert "net capital is at or below zero" in note
    assert "One of today's picks" not in note
    assert bv.status_line_text(out["cache"]) is None     # no line on a called book


def test_no_size_markers_pass_through_and_consume_nothing():
    marker = {"capital_infeasible": True, "one_share_capital_pct": 30.0,
              "sizing_version": 3}
    p = _pick("AAA", 80)
    p["sizing"] = dict(marker)
    out = _run(_brief([p, _pick("BBB", 70, shares=40)]))
    assert "shares" not in _sz(out, "pick", "AAA")
    assert _sz(out, "pick", "AAA")["capital_infeasible"] is True
    assert _shares(out, "pick", "BBB") == 40            # whole budget still there


def test_crash_withholds_sizes_and_publishes_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(dbud, "allocate", boom)
    out = _run(_brief([_pick("AAA", 80)]))
    assert out["cache"] is None and out["crashed"].startswith("RuntimeError")
    s = _sz(out, "pick", "AAA")
    assert "shares" not in s and s["budget_unknown"] is True


def test_cache_is_a_dict_or_none_never_empty():
    for out in (_run(_brief([_pick("AAA", 80)])),
                _run(_brief([_pick("AAA", 80)]), trades=None)):
        assert out["cache"] is None or (isinstance(out["cache"], dict) and out["cache"])


# -------------------------------------------- composite order / adds / ETFs
def test_adds_count_toward_the_budget_and_rank_by_their_score():
    out = _run(_brief([_pick("PCK", 70, shares=24)], [_add("ADD", 85, shares=24)]),
               price_map={"ADD": 100.0})
    assert _shares(out, "add", "ADD") == 24              # composite 85 goes first
    assert _shares(out, "pick", "PCK") == 16             # adds consumed budget


def test_etf_is_sized_after_every_stock_regardless_of_composite():
    out = _run(_brief([_pick("ETF1", 95, shares=24, etf=True),
                       _pick("STK", 66, shares=24)]))
    assert _shares(out, "pick", "STK") == 24
    assert _shares(out, "pick", "ETF1") == 16


def test_input_order_does_not_change_the_result():
    a = _run(_brief([_pick("AAA", 80), _pick("BBB", 70)]))
    b = _run(_brief([_pick("BBB", 70), _pick("AAA", 80)]))
    assert _shares(a, "pick", "BBB") == _shares(b, "pick", "BBB") == 16


def test_add_price_comes_from_the_book_then_from_the_sizing():
    # 700 left (3,300 logged). The sizing implies $100/sh (10 sh = $1,000 > 700
    # -> partial 7); the BOOK says $50/sh (10 sh = $500 <= 700 -> full 10).
    trades = _buy_today("ZZZ", 33, 100.0)
    book = _run(_brief(adds=[_add("ADD", 85, shares=10, price=100.0)]),
                trades=trades, price_map={"ADD": 50.0})
    assert _shares(book, "add", "ADD") == 10          # book price used
    fallback = _run(_brief(adds=[_add("ADD", 85, shares=10, price=100.0)]),
                    trades=trades)                     # no book -> total_cost/shares
    assert _shares(fallback, "add", "ADD") == 7


# -------------------------------------------------------- never mutates
def test_the_raw_brief_is_never_mutated_and_the_copy_is_distinct():
    brief = _brief([_pick("AAA", 80), _pick("BBB", 70)], [_add("CCC", 85)])
    before = copy.deepcopy(brief)
    out = _run(brief)
    assert brief == before                                # nothing written back
    assert out["brief"] is not brief
    assert out["brief"]["grow_today"] is not brief["grow_today"]
    assert brief["grow_today"]["new_picks"][1]["sizing"]["shares"] == 24   # raw still full
    # control: the copy IS adjusted (add 85 -> 24, AAA 80 -> 16 left, BBB 70 -> none)
    assert _shares(out, "pick", "AAA") == 16
    assert "shares" not in _sz(out, "pick", "BBB")


def test_a_budget_free_brief_stays_budget_free_across_two_renders():
    brief = _brief([_pick("AAA", 80), _pick("BBB", 70)])
    _run(brief, trades=_buy_today("ZZZ", 30, 100.0))
    out = _run(brief)                                     # next render, nothing logged
    assert _shares(out, "pick", "BBB") == 16
    assert "budget_capped" not in brief["grow_today"]["new_picks"][1]["sizing"]


def test_none_and_offline_stub_briefs_still_publish_the_budget():
    for b in (None, {"act_today": [], "review_list": [], "_offline": True}):
        out = _run(b)
        assert out["brief"] is b
        assert out["cache"]["state"] == "ok"
        assert out["cache"]["left"] == pytest.approx(4000.0)


# ------------------------------------------------------- wording / markers
def _emitted_flags_from_source():
    """Budget flags apply_to_sizing can put on a sizing dict, derived from its
    own source (producer end)."""
    tree = ast.parse((_ROOT / "stock_analyzer" / "daily_budget.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "apply_to_sizing")
    flags = {n.value for n in ast.walk(fn)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and re.fullmatch(r"budget_(capped|reserved|exhausted|unknown)", n.value)}
    # exhausted/unknown are written through `out[status]` from module constants
    mod = ast.parse((_ROOT / "stock_analyzer" / "daily_budget.py").read_text(encoding="utf-8"))
    for n in mod.body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) \
                and n.targets[0].id in ("BUDGET_EXHAUSTED", "BUDGET_UNKNOWN"):
            flags.add(n.value.value)
    return flags


def test_every_flag_the_producer_can_emit_has_a_card_note():
    flags = _emitted_flags_from_source()
    assert flags == {"budget_capped", "budget_reserved", "budget_exhausted",
                     "budget_unknown"}                    # derivation not vacuous
    assert flags == set(bv.BUDGET_CARD_FLAGS)
    for f in flags:
        sz = {"shares": 5, "budget_uncapped_shares": 9} if f in (
            "budget_capped", "budget_reserved") else {}
        sz[f] = True
        for kind in ("pick", "add"):
            note = bv.card_note(sz, kind)
            assert note and note.strip(), (f, kind)


def test_card_note_wording_by_state_and_kind():
    assert bv.card_note({"shares": 16, "budget_capped": True,
                         "budget_uncapped_shares": 24}, "pick") \
        == "Sized from what's left of today's budget — 16 of 24 shares."
    ex_pick = bv.card_note({"budget_exhausted": True}, "pick")
    ex_add = bv.card_note({"budget_exhausted": True}, "add")
    assert ex_pick.startswith("One of today's picks — not sized")
    assert ex_add.startswith("Not sized: today's new-position budget")
    assert f"{DAILY_NEW_POSITION_BUDGET_PCT:g}%" in ex_pick
    assert bv.card_note({"shares": 5, "budget_reserved": True}, "pick") \
        == "Reserved by today's email run"
    unk = bv.card_note({"budget_unknown": True, "budget_reason": "the trade log couldn't be read"}, "add")
    assert unk == ("Size withheld — today's new-position budget couldn't be "
                   "checked (the trade log couldn't be read).")
    assert bv.card_note({"shares": 5}, "pick") is None       # budget left it alone
    assert bv.card_note(None, "pick") is None


def test_status_line_text_and_privacy_mask():
    out = _run(_brief([_pick("AAA", 80), _pick("BBB", 70)]))
    line = bv.status_line_text(out["cache"])
    assert line.startswith("New positions today: 40.0% of net capital")
    assert "this list $4,000" in line and "$0 left" in line
    masked = bv.status_line_text(out["cache"], mask=True)
    assert "$" not in masked and "••••••" in masked and "40.0%" in masked
    assert bv.status_line_text(None) is None


def test_rec_budget_state_matches_the_cron_mapping():
    import cron_runner as cr
    for flag in bv.BUDGET_CARD_FLAGS + ("none",):
        sz = {flag: True}
        assert bv.rec_budget_state(sz) == cr._rec_budget_state(sz), flag
    assert bv.rec_budget_state(None) is None


# --------------------------------------------------------- registry wiring
def test_the_budget_cache_is_a_registered_gate_tier_home_key():
    k = "_new_position_budget_cache"
    assert k in cf.PORTFOLIO_DEPENDENT_KEYS
    assert cf.tier_of(k) == cf.TIER_GATE
    assert k in cf.keys_for_producer("home")
    assert k in cf._DIMENSION_LABELS


def test_a_none_budget_cache_is_never_stamped_fresh():
    stamps = cf.apply_stamps({"_new_position_budget_cache": 3},
                             {"_new_position_budget_cache": None},
                             ["_new_position_budget_cache"], 4)
    assert "_new_position_budget_cache" not in stamps
    ok = cf.apply_stamps({}, {"_new_position_budget_cache": {"state": "ok"}},
                         ["_new_position_budget_cache"], 4)
    assert ok["_new_position_budget_cache"] == 4


# ------------------------------------------- app.py wiring (structural pins)
_SRC = (_ROOT / "app.py").read_text(encoding="utf-8")


def _func_src(name):
    m = re.search(rf"^def {name}\(", _SRC, re.M)
    assert m, name
    nxt = re.search(r"^def ", _SRC[m.end():], re.M)
    return _SRC[m.start(): m.end() + (nxt.start() if nxt else len(_SRC))]


def test_postpass_runs_on_both_home_paths_before_the_rec_log_capture():
    calls = [m.start() for m in re.finditer(r"_publish_new_position_budget\(\s*\n?\s*_daily_brief_raw", _SRC)]
    assert len(calls) == 2, "one post-pass on the memo-HIT path, one on the MISS path"
    save = _SRC.index("_rec_save_result = db.save_recommendations(_rec_rows)")
    assert calls[1] < save, "MISS-path post-pass must precede the rec-log capture"
    hit = _SRC.index("if _synth_hit:")
    miss_else = _SRC.index("Computed once, pure/no-LLM")
    assert hit < calls[0] < miss_else < calls[1]
    # HIT path: the post-pass must come AFTER the composite-freshness rebuild,
    # or the rebuilt (raw) brief would be rendered un-budgeted.
    assert _SRC.index("# Composite freshness check") < calls[0]


def test_the_memo_and_the_lock_store_the_raw_brief_not_the_budgeted_one():
    assert re.search(r'"_daily_brief":\s*_daily_brief_raw,', _SRC)
    assert not re.search(r'"_daily_brief":\s*_daily_brief,', _SRC), \
        "the memoized brief must stay budget-free"
    assert re.search(r'\["_brief_locked_snapshot"\]\s*=\s*_daily_brief_raw\b', _SRC)
    assert not re.search(r'\["_brief_locked_snapshot"\]\s*=\s*_daily_brief\b(?!_)', _SRC)


def test_rec_rows_carry_the_budget_state_of_the_shown_sizing():
    body = _SRC[_SRC.index("def _rec_sizing_cols"):]
    body = body[:body.index("_rec_rows: list[dict] = []")]
    assert "_budget_view.rec_budget_state(_s)" in body
    assert '_cols["rec_budget_state"]' in body


def test_both_grow_cards_render_the_budget_note_through_card_note():
    assert '_budget_view.card_note(_sz, "pick", called=_bud_called)' in _SRC
    assert '_budget_view.card_note(_sz, "add", called=_bud_called)' in _SRC


def test_status_line_escapes_dollars_and_is_masked_in_privacy_mode():
    body = _SRC[_SRC.index("def _budget_status_captions"):]
    body = body[:body.index("_bo = _budget_view.over_line_text")]
    assert 'replace("$", "\\\\$")' in body
    assert 'mask=st.session_state.get("_privacy", True)' in body


def test_strict_trades_read_is_not_the_lenient_session_frame_and_keys_on_epoch():
    body = _func_src("_budget_strict_trades")
    assert "db.load_trades_or_none()" in body
    assert 'session_state.get("trades_df")' not in body
    assert "session_state.trades_df" not in body.replace("`st.session_state.trades_df`", "")
    assert "_coord_epoch()" in body
    # 5-minute bucket: bounds the paths no pop can see (save_trade ok but
    # save_holdings failed -> no refresh/epoch bump; a trade from another device)
    assert "_n.hour * 12 + _n.minute // 5" in body
    assert "_today_et().isoformat()" in body
    # a failed read must not be cached
    assert "if _df is not None:" in body


def test_the_post_trade_republisher_recounts_and_stamps_the_budget():
    body = _func_src("_refresh_portfolio_cache_after_trade")
    assert "_publish_new_position_budget(" in body
    assert '_stamp_coord(["_acct_gate_cache", "_new_position_budget_cache"])' in body
    assert 'pop("_budget_trades_cache"' in body
    # republisher budget failure publishes None, not a stale dict
    assert 'st.session_state["_new_position_budget_cache"] = None' in body


def test_publish_helper_sets_the_cache_from_the_pure_result_only():
    body = _func_src("_publish_new_position_budget")
    assert "_budget_view.postpass(" in body
    assert 'st.session_state["_new_position_budget_cache"] = _out["cache"]' in body
    assert "or {}" not in body


def test_every_trade_write_site_drops_the_strict_trades_cache_right_after():
    sites = [m.start() for m in re.finditer(r"^[^#\n]*?db\.(save_trade|delete_trade)\(", _SRC, re.M)]
    assert len(sites) >= 6, "expected the SPLIT, SELL, BUY, delete and two import sites"
    for at in sites:
        # The pop must come right after the write -- before any holdings write
        # that follows it (a failed holdings save skips the post-trade refresh,
        # so a pop placed after it would be skipped too). Bounded at 1500 chars.
        window = _SRC[at: at + 1500]
        pop_at = window.find('pop("_budget_trades_cache"')
        assert pop_at != -1, \
            "a trade write that does not drop _budget_trades_cache: " + _SRC[at: at + 60]
        hold_at = window.find("save_holdings(")
        assert hold_at == -1 or pop_at < hold_at, \
            "_budget_trades_cache is dropped only after the holdings write: " + _SRC[at: at + 60]


def test_republisher_outage_and_crash_publish_none_and_clear_the_stamp():
    body = _func_src("_refresh_portfolio_cache_after_trade")
    outage = body[body.index('if _outcome == "outage":'): body.index("# Atomic commit")]
    assert 'st.session_state["_new_position_budget_cache"] = None' in outage
    assert '_stamp_coord(["_new_position_budget_cache"])' in outage
    crash = body[body.index("except Exception as _exc:"):]
    assert 'st.session_state["_new_position_budget_cache"] = None' in crash
    assert '_stamp_coord(["_new_position_budget_cache"])' in crash


def test_republisher_budgets_only_a_todays_memo_brief():
    body = _func_src("_refresh_portfolio_cache_after_trade")
    assert '_get_or_offline(_rb_memo, "date") == _today_et().isoformat()' in body
    assert "_rb_brief" in body


def test_price_map_guards_each_row_and_postpass_crash_is_logged():
    pm = _func_src("_budget_price_map")
    assert "for _t, _p in zip(" in pm and "except (TypeError, ValueError):" in pm
    assert "continue" in pm
    pub = _func_src("_publish_new_position_budget")
    assert 'logging.warning("Daily budget post-pass crashed' in pub


def test_budget_view_is_a_margin_isolated_gate_module():
    src = (_ROOT / "tests" / "test_margin.py").read_text(encoding="utf-8")
    assert '"stock_analyzer.budget_view"' in src
