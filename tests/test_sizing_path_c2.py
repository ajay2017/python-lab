"""S-D chunk C2: Analysis shows Grow Today's add verdict for a HELD name and
never decides an add itself (owner D2-A; D4: holdings unknown -> no size).

Pure helpers in `sizing_path` + `budget_view.scale_ps`, plus AST wiring pins on
app.py (which no test imports)."""
import ast
import datetime
import pathlib

import pandas as pd
import pytest

from stock_analyzer import budget_view as bv
from stock_analyzer import sizing_path as sp
from stock_analyzer.constants import NET_CAPITAL_POSITION_CAP_PCT, SINGLE_NAME_CEILING
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_APP = _ROOT / "app.py"


def _decision(**kw):
    d = {"offer_size": True, "hold": None, "holds": [], "unchecked": [], "passed": [],
         "reason_if_no_size": None, "entry_checked": False, "crashed": False}
    d.update(kw)
    return d


def _verdict(state, reason="r", gate_id=None, item=None):
    if state == "add" and item is None:
        item = {"ticker": "X", "sizing": {"shares": 3}}
    return {"state": state, "reason": reason, "gate_id": gate_id, "item": item}


# ── analysis_offer_held_add ───────────────────────────────────────────────────

def test_add_verdict_keeps_the_size_and_attaches_the_verdict():
    d, v = _decision(), _verdict("add")
    out = sp.analysis_offer_held_add(d, v)
    assert out["offer_size"] is True and out["held_add"] is v
    assert out is not d and "held_add" not in d          # a copy


@pytest.mark.parametrize("state", ["paused", "not_called", "unknown"])
def test_every_non_add_verdict_withholds_the_size_with_its_reason(state):
    v = _verdict(state, reason=f"because {state}")
    out = sp.analysis_offer_held_add(_decision(), v)
    assert out["offer_size"] is False and out["reason_if_no_size"] == f"because {state}"
    assert out["held_add"] is v


def test_missing_or_garbage_verdict_withholds_with_a_generic_reason():
    for v in (None, "add", 3, {"state": None}):
        out = sp.analysis_offer_held_add(_decision(), v)
        assert out["offer_size"] is False
        assert out["reason_if_no_size"]
    assert "Grow Today" in sp.analysis_offer_held_add(_decision(), None)["reason_if_no_size"]


def test_a_decision_that_already_offers_no_size_is_unchanged_plus_the_verdict():
    d = _decision(offer_size=False, reason_if_no_size="no size on a Hold verdict")
    out = sp.analysis_offer_held_add(d, _verdict("add"))
    assert out["offer_size"] is False
    assert out["reason_if_no_size"] == "no size on a Hold verdict"
    assert out["held_add"]["state"] == "add"


def test_offer_held_add_never_raises():
    class Boom(dict):
        def get(self, *a, **k):
            raise RuntimeError("x")
    out = sp.analysis_offer_held_add(_decision(), Boom(state="add"))
    assert out["offer_size"] is False


# ── holdings_state / D4 ───────────────────────────────────────────────────────

def test_holdings_state_three_values():
    df = pd.DataFrame({"Ticker": ["A"]})
    assert sp.holdings_state("ready", ["A"], df) == "ready"
    assert sp.holdings_state("missing", None, None) == "unknown"
    assert sp.holdings_state("offline", None, None) == "unknown"
    assert sp.holdings_state("missing", [], None) == "flat"           # definitely holds nothing
    assert sp.holdings_state("ready", None, pd.DataFrame()) == "unknown"
    assert sp.holdings_state("missing", ["A"], None) == "unknown"     # non-empty list is not "flat"


def test_unknown_holdings_force_no_size_and_say_why():
    out = sp.analysis_holdings_unknown(_decision(entry_checked=True))
    assert out["offer_size"] is False and out["holdings_unknown"] is True
    assert out["entry_checked"] is False
    assert "holdings couldn't be checked" in out["reason_if_no_size"]
    assert sp.analysis_holdings_unknown(None)["offer_size"] is False


# ── concentration_tile ────────────────────────────────────────────────────────

def test_tile_equality_case_displays_pre_trade_pct_and_is_not_a_breach():
    t = sp.concentration_tile(existing_value=500.0, add_cost=1000.0,
                              gross_book=10_000.0, net_capital=None)
    assert t["state"] is True
    assert "15.0% of book" in t["detail"] and t["detail"].startswith("5.0% → 15.0% of book")


def test_tile_gross_breach_uses_the_post_trade_denominator():
    # (ex+add)/(gb+add) = 1,700 / 10,200 = 16.7% -> breach even though it equals 17.0% pre-trade
    t = sp.concentration_tile(existing_value=700.0, add_cost=1000.0,
                              gross_book=10_000.0, net_capital=None)
    assert t["state"] is False


def test_tile_not_held_buy_has_no_zero_arrow():
    t = sp.concentration_tile(existing_value=0.0, add_cost=950.0, gross_book=10_000.0,
                              net_capital=3_860.0)
    assert t["state"] is True
    assert "0.0%" not in t["detail"] and "→" not in t["detail"]
    assert t["detail"] == "9.5% of book · 24.6% of net capital after the buy"


def test_tile_not_held_buy_over_the_capital_cap_is_a_breach():
    t = sp.concentration_tile(existing_value=0.0, add_cost=1000.0, gross_book=100_000.0,
                              net_capital=3_000.0)
    assert t["state"] is False and t["detail"].endswith("after the buy")


# ── add_call_box ──────────────────────────────────────────────────────────────

_PS = {"shares": 2, "room_capped": True, "room_binding": "capital", "existing_value": 1400.0,
       "net_capital": 7000.0, "post_capital_pct": 24.7, "portfolio_value": 100_000.0,
       "post_portfolio_pct": 1.8}


def test_box_add_with_size():
    v = _verdict("add", item={"ticker": "NVDA", "score": 78.4})
    b = sp.add_call_box(v, ps=_PS, same_size=True, built_at="09:52")
    assert b["tone"] == "green" and b["title"] == "➕ Add — Grow Today's call today: 2 shares"
    assert b["body"].startswith("Capped by what you already hold — 20.0% → 24.7% of net capital")
    assert b["source"] == ("Grow Today's call, built 09:52 ET · composite there 78. "
                           "Same size as Grow Today's card.")
    assert not b["show_home"]


def test_box_add_same_size_sentence_only_when_the_size_matches():
    b = sp.add_call_box(_verdict("add"), ps=_PS, same_size=False, built_at="09:52")
    assert "Same size" not in b["source"]


def test_box_add_without_size_uses_the_supplied_text():
    b = sp.add_call_box(_verdict("add"), ps=None, size_text="No room left")
    assert b["title"] == "➕ Add — Grow Today's call today" and b["body"] == "No room left"
    assert b["tone"] == "green"


def test_box_paused_has_reason_and_gate():
    v = _verdict("paused", reason="NEW — added 5d ago", gate_id="G-24",
                 item={"score": 81.0})
    b = sp.add_call_box(v, built_at="09:52")
    assert b["tone"] == "amber" and b["title"] == "➕ Add — paused in Grow Today today"
    assert b["body"] == "NEW — added 5d ago"
    assert b["source"] == "Grow Today's call, built 09:52 ET · composite there 81 · gate G-24."


def test_box_missing_fragments_are_dropped_never_printed_as_none():
    b = sp.add_call_box(_verdict("paused", reason="r", gate_id=None, item=None))
    assert b["source"] == "Grow Today's call."
    assert "None" not in str(b)
    b2 = sp.add_call_box(_verdict("add"), ps=_PS)
    assert "None" not in str(b2) and "built" not in b2["source"]


def test_box_not_called_and_unknown():
    nc = sp.add_call_box(_verdict("not_called", reason="up days only"))
    assert nc["tone"] == "grey" and nc["title"] == "➕ No add call today"
    assert nc["body"] == "up days only" and nc["source"] is None and not nc["show_home"]
    un = sp.add_call_box(_verdict("unknown", reason="open 🏠 Home"))
    assert un["title"] == "➕ Add call not available" and un["show_home"] is True
    assert sp.add_call_box(None)["show_home"] is True


# ── scale_ps keeps the room fields honest ─────────────────────────────────────

def test_scale_ps_recomputes_post_pct_and_drops_the_room_claim():
    ps = {"shares": 7, "total_cost": 700.0, "actual_risk": 70.0, "portfolio_pct": 0.7,
          "capital_pct": 10.0, "risk_pct_actual": 0.07, "room_capped": True,
          "room_binding": "capital", "existing_value": 1000.0, "net_capital": 7000.0,
          "portfolio_value": 100_000.0, "post_capital_pct": 24.3, "post_portfolio_pct": 1.7}
    out = bv.scale_ps(ps, 2)
    assert out["shares"] == 2 and out["total_cost"] == 200.0
    assert out["post_capital_pct"] == 17.1 and out["post_portfolio_pct"] == 1.2
    assert out["room_capped"] is False and out["room_binding"] is None
    assert sp.room_note(out) is None
    assert ps["room_capped"] is True                          # input not mutated
    assert bv.scale_ps(ps, 7)["room_capped"] is True          # unchanged size: untouched


# ── the budget mirror applies to a held ADD on Grow Today's list ─────────────

_INPUTS = {"gross_book": 14000.0, "net_capital": 10000.0, "basis_state": "levered",
           "cash_balance": -4000.0, "cash_as_of": "2026-10-16T13:45:00+00:00"}
_TODAY = datetime.date(2026, 10, 16)
_NOW = datetime.datetime(2026, 10, 16, 15, 0, tzinfo=datetime.timezone.utc)


def _ps(shares):
    return {"shares": shares, "risk_budget": 100.0, "actual_risk": shares * 4.0,
            "risk_per_share": 4.0, "total_cost": shares * 100.0,
            "portfolio_pct": round(shares * 100.0 / 14000.0 * 100, 1),
            "risk_pct_actual": 0.1, "ceiling_pct": 15.0, "ceiling_capped": False,
            "uncapped_shares": shares, "uncapped_pct": 1.0,
            "capital_capped": False, "capital_pct": float(shares)}


def test_a_held_add_on_grow_todays_list_mirrors_grow_todays_shares_on_analysis():
    add = {"ticker": "NVDA", "composite_score": 80.0, "score": 80.0, "price": 100.0,
           "sector": "Tech", "sizing": _ps(3)}
    brief = {"grow_today": {"tone": "bull", "new_picks": [], "add_positions": [add],
                            "overflow_picks": []}}
    cache = bv.postpass(brief, _INPUTS, pd.DataFrame(
        columns=["traded_at", "action", "ticker", "shares", "price"]), {}, _TODAY, _NOW)["cache"]
    assert cache is not None and "NVDA" in cache["grow_tickers"]
    # Analysis's own (whole-position) size is LARGER than Grow Today's 3 -> mirror 3
    ask = {"ticker": "NVDA", "composite": 80.0, "price": 100.0, "ps": _ps(9)}
    plan = bv.plan_downstream(_INPUTS, pd.DataFrame(
        columns=["traded_at", "action", "ticker", "shares", "price"]), {}, cache, _TODAY, _NOW,
        [ask], mirror_gt=True, independent=True)
    r = plan["results"]["NVDA"]
    assert r["ps"]["shares"] == 3 and r["note"] == bv.GROW_TODAY_MIRROR_NOTE


# ── app.py wiring (AST) ──────────────────────────────────────────────────────

def _calls(tree, attr, base=None):
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == attr
                and (base is None or (isinstance(n.func.value, ast.Name)
                                      and n.func.value.id == base))):
            out.append(n)
    return out


def _kw(call, name):
    return next((k.value for k in call.keywords if k.arg == name), None)


def test_analysis_reads_the_verdict_and_applies_it():
    tree = parse_file_cached(_APP)
    assert _calls(tree, "held_add_verdict", "_sizing_path")
    assert _calls(tree, "analysis_offer_held_add", "_sizing_path")
    v = _calls(tree, "held_add_verdict", "_sizing_path")[0]
    for k in ("brief_date", "today", "brief_epoch", "epoch"):
        assert _kw(v, k) is not None, k


def test_held_sizing_goes_through_size_position_with_held_value():
    tree = parse_file_cached(_APP)
    sp_calls = _calls(tree, "size_position", "_sizing_path")
    assert len(sp_calls) == 1
    ev = _kw(sp_calls[0], "existing_value")
    assert isinstance(ev, ast.Call) and ev.func.attr == "held_value"


def test_not_held_sizing_call_is_unchanged():
    src = _APP.read_text(encoding="utf-8")
    old = ("position_sizing(\n"
           "                    portfolio_value, MODERATE_RISK_PCT, _ap, _ar[\"stop\"], SINGLE_NAME_CEILING,\n"
           "                    net_capital=_f255_net_cap, max_capital_pct=NET_CAPITAL_POSITION_CAP_PCT,\n"
           "                )")
    assert old in src


def test_both_memo_stamps_exist():
    src = _APP.read_text(encoding="utf-8")
    assert '"epoch": _coord_epoch(),' in src            # miss: written with the memo
    assert '"built_at": _now_et().strftime("%H:%M"),' in src
    assert '_synth_cache["epoch"] = _coord_epoch()' in src   # hit: re-stamped


def test_add_button_for_a_held_name_is_gated_on_the_verdict():
    src = _APP.read_text(encoding="utf-8")
    i = src.index("# S-D C2: an add is Grow Today's call. No call (or no size)")
    seg = src[i - 700:i + 1400]
    assert '_da_entry["held_add"].get("state") == "add"' in seg and "and ps" in seg
    assert "Add button hidden" in seg
    assert src.count("📒 Add button hidden") >= 3


def test_unknown_holdings_force_no_size_in_the_page():
    tree = parse_file_cached(_APP)
    assert _calls(tree, "analysis_holdings_unknown", "_sizing_path")
    assert _calls(tree, "holdings_state", "_sizing_path")
    src = _APP.read_text(encoding="utf-8")
    assert '_an_holdings_state == "unknown"' in src


def test_concentration_tile_comes_from_the_shared_function():
    tree = parse_file_cached(_APP)
    assert _calls(tree, "concentration_tile", "_sizing_path")
    src = _APP.read_text(encoding="utf-8")
    assert "_gate_conc_ok   = ((_gc_tw or 0) < SINGLE_NAME_CEILING)" not in src


def test_the_misleading_already_held_sentence_is_gone():
    src = _APP.read_text(encoding="utf-8")
    assert "Sizing below is for adding to your existing position." not in src
    assert "every row below shows a fresh" not in src


def test_cap_constants_are_what_the_box_text_names():
    # the box text pulls both numbers from the constants, never literals
    n = sp.room_note({"shares": 1, "room_capped": True, "room_binding": "capital",
                      "existing_value": 100.0, "net_capital": 1000.0, "post_capital_pct": 20.0})
    assert f"(cap {int(NET_CAPITAL_POSITION_CAP_PCT)}%)" in n
    b = sp.room_note({"shares": 1, "room_capped": True, "room_binding": "ceiling",
                      "existing_value": 100.0, "portfolio_value": 1000.0,
                      "post_portfolio_pct": 12.0})
    assert f"(cap {int(SINGLE_NAME_CEILING)}%)" in b


# ── review fixes: unpriced holding, G-01 reason, no-shares add ────────────────

def _book(*tickers):
    return pd.DataFrame({"Ticker": list(tickers), "Market Value": [1000.0] * len(tickers)})


def test_unpriced_holdings_are_the_known_held_tickers_missing_from_the_book():
    assert sp.unpriced_holdings(["AAA", "bbb", "CCC"], _book("AAA", "CCC")) == {"BBB"}
    assert sp.unpriced_holdings(None, _book("AAA")) == set()
    assert sp.unpriced_holdings(["AAA"], None) == {"AAA"}


def test_held_value_fails_closed_for_a_known_holding_missing_from_the_book():
    df = _book("AAA")
    assert sp.held_value("BBB", df, "ready") == 0.0                       # old behaviour
    assert sp.held_value("BBB", df, "ready", held_tickers=["AAA", "BBB"]) is None
    assert sp.held_value("bbb", df, "ready", held_tickers=["AAA", "BBB"]) is None
    assert sp.held_value("AAA", df, "ready", held_tickers=["AAA", "BBB"]) == 1000.0
    assert sp.held_value("ZZZ", df, "ready", held_tickers=["AAA", "BBB"]) == 0.0
    assert sp.held_value("BBB", pd.DataFrame(), "flat", held_tickers=["BBB"]) is None


def test_unpriced_decision_says_which_holding_and_offers_no_size():
    out = sp.analysis_holdings_unknown(_decision(entry_checked=True), ticker="BBB", unpriced=True)
    assert out["offer_size"] is False and out["holdings_unknown"] is True
    assert out["entry_checked"] is False
    assert out["holdings_note"] == ("Your BBB holding couldn't be priced on 🏠 Home, "
                                    "so no share count is shown.")


def test_risk_blocked_add_reason_is_a_sentence_not_the_type_code():
    for code, label in (("beta", "high portfolio beta"), ("sharpe", "poor risk-adjusted return")):
        grow = {"tone": "bull", "risk_blocked_adds": [
            {"ticker": "MRVL", "reason": code, "title": "T", "gate_id": "G-01"}]}
        out = sp.held_add_verdict("MRVL", grow, brief_date="2026-10-20", today="2026-10-20",
                                  brief_epoch=1, epoch=1)
        assert out["state"] == "paused" and out["reason"] not in ("beta", "sharpe")
        assert label in out["reason"]
    odd = {"tone": "bull", "risk_blocked_adds": [{"ticker": "MRVL", "reason": "x", "title": "Trim X"}]}
    assert sp.held_add_verdict("MRVL", odd, brief_date="2026-10-20", today="2026-10-20",
                               brief_epoch=1, epoch=1)["reason"] == "Trim X"


def test_add_verdict_without_grow_todays_shares_offers_no_size():
    for sizing, text in (({"holding_unknown": True}, "couldn't read your holding"),
                         ({"budget_exhausted": True}, "no add size for this name"),
                         (None, "no add size for this name")):
        v = {"state": "add", "reason": "r", "gate_id": None,
             "item": {"ticker": "X", "sizing": sizing}}
        out = sp.analysis_offer_held_add(_decision(), v)
        assert out["offer_size"] is False and text in out["reason_if_no_size"]
    ok = {"state": "add", "reason": "r", "gate_id": None,
          "item": {"ticker": "X", "sizing": {"shares": 2}}}
    assert sp.analysis_offer_held_add(_decision(), ok)["offer_size"] is True


def test_app_wires_the_unpriced_holdings_and_escapes_the_export_line():
    src = _APP.read_text(encoding="utf-8")
    tree = parse_file_cached(_APP)
    assert _calls(tree, "unpriced_holdings", "_sizing_path")
    assert src.count("held_tickers=_an_last_held") == 2          # sizing + concentration tile
    assert "unpriced=True" in src and "ticker=str(_t).strip().upper()" in src
    seg = src.split("**Trade**: no add size today")[1][:200]
    assert "chr(92)" in seg                                      # $ escaped for LaTeX
    assert "_sizing_path.RISK_ADD_REASON_LABEL" in src
    assert "Grow Today never calls an add on a Hold verdict" in src


def test_unpriced_override_is_guarded_and_runs_before_the_sizing_loop():
    """C2 review (non-blocking 1): the override must key on _an_unpriced and
    run before the loop that builds the asks / sizes, or an unpriced holding
    is sized as a fresh buy again."""
    src = _APP.read_text(encoding="utf-8")
    guard = "if str(_t).strip().upper() in _an_unpriced:"
    assert src.count(guard) == 1
    g = src.index(guard)
    override = src.index("unpriced=True", g)
    assert override - g < 300                                  # the guard's own body
    loop = src.index("_an_ps_f255 = {}", g)                    # sizing starts after it
    assert g < override < loop
