"""Brief consolidation C1b (row SectorCapCardMerged, line + netting; spec
docs/plans/brief-consolidation-spec.md section 1b / section 4 Row 1).

`sector_cap_cover.decide(flag, cover)` reads the post-demotion Act Today view's sell cover
against the sector Hard Cap card's member holdings: full exits are netted out of the
Rebalance plan's target (owner Q2), a partial seller is named but never netted, and when the
exits alone reach SECTOR_ELEVATED the card says no other trim is needed (owner Q3).
"""
import copy
import math
import re
from pathlib import Path

import pytest

from stock_analyzer import act_sell_cover as asc
from stock_analyzer import sector_cap_cover as scc
from stock_analyzer.constants import SECTOR_ELEVATED
from stock_analyzer.portfolio import trim_allocation
from tests._ast_cache import parse_file_cached, source_segment

pytestmark = pytest.mark.fast

_APP = Path(__file__).resolve().parent.parent / "app.py"


def _flag(**kw):
    f = {
        "title": "Tech 40.0% — Hard Cap Breach", "recommendation": "Trim Tech.",
        "problem": "Tech 40%", "rec_type": "sector_concentration", "sector": "Tech",
        "sector_members": [
            {"ticker": "AAA", "market_value": 20_000.0},
            {"ticker": "BBB", "market_value": 10_000.0},
            {"ticker": "CCC", "market_value": 5_000.0},
        ],
        "trim_candidates": [
            {"ticker": "AAA", "score": 40.0, "weight": 20.0, "pnl_pct": 1.0,
             "market_value": 20_000.0, "price": 100.0},
            {"ticker": "BBB", "score": 50.0, "weight": 10.0, "pnl_pct": -2.0,
             "market_value": 10_000.0, "price": 50.0},
        ],
        "trim_excluded_recent": [{"ticker": "CCC", "score": 60.0, "weight": 5.0,
                                  "market_value": 5_000.0}],
        "redeploy_sectors": [{"sector": "Energy", "weight": 3.0}],
        "trim_target_pp": 15.0, "trim_target_dollar": 15_000, "trim_target_denom": 100_000.0,
    }
    f.update(kw)
    return f


def _exit(t):
    return {t: {"exit": True, "shares": None}}


def _sell(t, shares=None):
    return {t: {"exit": False, "shares": shares}}


def _cov(*maps):
    out = {}
    for m in maps:
        out.update(m)
    return out


# ── no line, plan unchanged ───────────────────────────────────────────────────

def test_cover_none_is_no_line_and_plan_unchanged():
    f = _flag()
    r = scc.decide(f, None)
    assert r["line"] is None and not r["netted"] and not r["covered"]
    assert r["exited"] == [] and r["also_selling"] == []
    assert r["candidates"] == f["trim_candidates"]
    assert r["net_target_dollar"] == 15_000 and r["net_target_pp"] == 15.0


def test_non_dict_cover_is_offline_like_none():
    assert scc.decide(_flag(), ["AAA"])["line"] is None


def test_missing_flag_is_inert():
    for fl in (None, {}, "x"):
        r = scc.decide(fl, _exit("AAA"))
        assert r["line"] is None and r["candidates"] == [] and not r["netted"]


def test_no_sold_members_no_line():
    f = _flag()
    r = scc.decide(f, {})
    assert r["line"] is None and r["candidates"] == f["trim_candidates"]
    assert r["net_target_dollar"] == 15_000


def test_sold_non_member_ignored():
    r = scc.decide(_flag(), _cov(_exit("ZZZ"), _sell("YYY", 3)))
    assert r["line"] is None and r["exited"] == [] and r["also_selling"] == []
    assert not r["netted"]


def test_recovered_breach_has_no_cover_entry_so_no_line():
    # act_sell_cover.view_sell_cover reads the post-demotion `active` bucket only: a stop
    # breach that recovered sits in `resolved`, so AAA has no entry and nothing is netted.
    view = {"state": "ok", "active": [], "resolved": [
        {"kind": "stop_breach", "ticker": "AAA"}]}
    cover = asc.view_sell_cover(view)
    assert cover == {}
    r = scc.decide(_flag(), cover)
    assert r["line"] is None and not r["netted"]
    assert r["net_target_dollar"] == 15_000


def test_view_offline_cover_none_end_to_end():
    assert asc.view_sell_cover({"state": "offline"}) is None
    assert scc.decide(_flag(), asc.view_sell_cover({"state": "offline"}))["line"] is None


# ── netting a full exit ───────────────────────────────────────────────────────

def test_exit_with_known_value_nets_target():
    f = _flag(trim_target_dollar=25_000, trim_target_pp=25.0)
    r = scc.decide(f, _exit("BBB"))
    assert r["exited"] == ["BBB"] and r["also_selling"] == []
    assert r["netted"] and not r["covered"]
    assert r["net_target_dollar"] == 15_000.0
    assert r["net_target_pp"] == pytest.approx(15.0)
    assert [c["ticker"] for c in r["candidates"]] == ["AAA"]
    assert r["line"] == (
        "🏠 Today's Act Today call already sells all of BBB (Tech). That sale counts toward "
        "this sector trim; the 🔎 Rebalance plan below sizes only the rest.")


def test_exit_equal_to_target_is_covered_but_cash_weight_is_stated():
    # target 10,000 / exit 10,000 / denom 100,000: kept as cash the sector is
    # (10,000 + 25,000 - 10,000) / 90,000 = 27.8%, NOT the 25% level.
    r = scc.decide(_flag(trim_target_dollar=10_000), _exit("BBB"))
    assert r["netted"] and r["covered"]
    assert r["net_target_dollar"] == 0.0
    assert r["net_target_pp"] == 0.0
    assert r["post_pct"] == pytest.approx(25_000 / 90_000 * 100)
    assert r["post_pct"] > SECTOR_ELEVATED
    assert r["line"] == (
        "🏠 Today's Act Today call already sells all of BBB, which covers this card's whole "
        "Tech trim target, so no other Tech trim is needed today. The target assumes the "
        "sale's proceeds go into other sectors (Redeploy in the 🔎 Rebalance plan); kept as "
        "cash, Tech would still be about 27.8% of your invested holdings.")
    assert "by itself" not in r["line"]


def test_covered_post_at_the_warn_level_uses_by_itself_wording():
    # target 15,000 / exit 20,000 / denom 100,000: (15,000 + 25,000 - 20,000) / 80,000
    # = exactly 25.0% -> at the boundary the strong wording is allowed.
    r = scc.decide(_flag(), _exit("AAA"))
    assert r["covered"] and r["post_pct"] == pytest.approx(SECTOR_ELEVATED)
    assert r["line"] == (
        f"🏠 Today's Act Today call already sells all of AAA, which by itself brings Tech "
        f"down to the {SECTOR_ELEVATED:.0f}% level. No other Tech trim is needed today.")


def test_covered_post_below_the_warn_level_uses_by_itself_wording():
    r = scc.decide(_flag(trim_target_dollar=10_000), _exit("AAA"))   # 18.75%
    assert r["covered"] and r["post_pct"] == pytest.approx(18.75)
    assert "which by itself brings Tech down to the" in r["line"]
    assert "kept as cash" not in r["line"]


def test_covered_just_above_the_warn_level_uses_cash_wording():
    f = _flag(trim_target_dollar=15_000)
    f["sector_members"][0]["market_value"] = 15_001.0    # (40,000 - X) / (100,000 - X) > 25%
    r = scc.decide(f, _exit("AAA"))
    assert r["covered"] and r["post_pct"] > SECTOR_ELEVATED
    assert "kept as cash" in r["line"] and "by itself" not in r["line"]


@pytest.mark.parametrize("denom", [None, float("nan"), 0, -1.0, 10_000.0, 5_000.0])
def test_covered_denom_unknown_or_not_above_exit_has_no_number(denom):
    # denom <= the exited value (10,000) or unusable: covered decision unchanged, no %.
    r = scc.decide(_flag(trim_target_dollar=10_000, trim_target_denom=denom), _exit("BBB"))
    assert r["netted"] and r["covered"] and r["post_pct"] is None
    assert r["line"] == (
        "🏠 Today's Act Today call already sells all of BBB, which covers this card's whole "
        "Tech trim target, so no other Tech trim is needed today.")


def test_covered_decision_unchanged_by_the_wording_split():
    for tgt, who in ((10_000, "BBB"), (15_000, "AAA"), (10_000, "AAA")):
        assert scc.decide(_flag(trim_target_dollar=tgt), _exit(who))["covered"]
    assert not scc.decide(_flag(trim_target_dollar=25_000), _exit("BBB"))["covered"]


def test_post_pct_none_when_not_netted():
    assert scc.decide(_flag(), None)["post_pct"] is None
    assert scc.decide(_flag(), _sell("BBB", 2))["post_pct"] is None


def test_held_back_drops_an_exited_member_case_insensitively():
    f = _flag(trim_target_dollar=25_000)
    f["trim_excluded_recent"] = [{"ticker": "ccc", "market_value": 5_000.0},
                                 {"ticker": "DDD", "market_value": 1_000.0}]
    r = scc.decide(f, _exit("CCC"))
    assert [x["ticker"] for x in r["held_back"]] == ["DDD"]
    r2 = scc.decide(f, _sell("CCC", 2))                 # a partial seller stays listed
    assert [x["ticker"] for x in r2["held_back"]] == ["ccc", "DDD"]
    assert [x["ticker"] for x in scc.decide(f, None)["held_back"]] == ["ccc", "DDD"]
    assert f["trim_excluded_recent"][0]["ticker"] == "ccc"   # input not mutated


def test_held_back_missing_key_is_empty():
    f = _flag()
    del f["trim_excluded_recent"]
    assert scc.decide(f, _exit("BBB"))["held_back"] == []


def test_exit_value_above_target_floors_at_zero():
    r = scc.decide(_flag(), _exit("AAA"))          # 20,000 exit vs 15,000 target
    assert r["netted"] and r["covered"]
    assert r["net_target_dollar"] == 0.0 and r["net_target_pp"] == 0.0


def test_two_exits_sum_their_values():
    f = _flag(trim_target_dollar=30_000)
    r = scc.decide(f, _cov(_exit("AAA"), _exit("BBB")))
    assert r["exited"] == ["AAA", "BBB"]
    assert r["netted"] and r["covered"]            # 30,000 - 30,000
    f2 = _flag(trim_target_dollar=40_000)
    r2 = scc.decide(f2, _cov(_exit("AAA"), _exit("BBB")))
    assert r2["netted"] and not r2["covered"] and r2["net_target_dollar"] == 10_000.0
    assert "all of AAA and BBB (Tech)" in r2["line"]


def test_three_exits_name_list_format():
    f = _flag(trim_target_dollar=90_000)
    r = scc.decide(f, _cov(_exit("AAA"), _exit("BBB"), _exit("CCC")))
    assert "all of AAA, BBB and CCC (Tech)" in r["line"]


def test_net_pp_none_when_denom_not_positive():
    for d in (0, -5.0, None, float("nan")):
        r = scc.decide(_flag(trim_target_dollar=25_000, trim_target_denom=d), _exit("BBB"))
        assert r["netted"] and r["net_target_dollar"] == 15_000.0
        assert r["net_target_pp"] is None


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0, 0.0, -1.0, "10000", True])
def test_exited_value_unknown_or_nonpositive_does_not_net(bad):
    f = _flag()
    f["sector_members"][1]["market_value"] = bad
    r = scc.decide(f, _exit("BBB"))
    assert r["exited"] == ["BBB"]
    assert not r["netted"] and not r["covered"]
    assert r["candidates"] == f["trim_candidates"]          # exited name NOT removed
    assert r["net_target_dollar"] == 15_000 and r["net_target_pp"] == 15.0
    assert r["line"] == (
        "🏠 Today's Act Today call already sells all of BBB (Tech). The 🔎 Rebalance plan "
        "below was sized before that sale; re-check it once the sale is logged.")


def test_one_exited_value_unknown_blocks_netting_of_all():
    f = _flag(trim_target_dollar=40_000)
    f["sector_members"][0]["market_value"] = float("nan")
    r = scc.decide(f, _cov(_exit("AAA"), _exit("BBB")))
    assert not r["netted"] and r["net_target_dollar"] == 40_000
    assert [c["ticker"] for c in r["candidates"]] == ["AAA", "BBB"]


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "x"])
def test_target_unknown_does_not_net(bad):
    r = scc.decide(_flag(trim_target_dollar=bad), _exit("BBB"))
    assert not r["netted"] and not r["covered"]
    assert "sized before that sale" in r["line"]


# ── partial seller ────────────────────────────────────────────────────────────

def test_partial_seller_named_not_netted():
    f = _flag()
    r = scc.decide(f, _sell("BBB", 3))
    assert r["exited"] == [] and r["also_selling"] == ["BBB"]
    assert not r["netted"] and not r["covered"]
    assert r["candidates"] == f["trim_candidates"]
    assert r["net_target_dollar"] == 15_000 and r["net_target_pp"] == 15.0
    assert r["line"] == (
        "🏠 BBB also has its own sell in today's Act Today call; the 🔎 Rebalance plan was "
        "sized before that sale and shrinks once it is logged; for that name, sell the larger "
        "of the two amounts, not the two added together.")


def test_two_partial_sellers_plural():
    r = scc.decide(_flag(), _cov(_sell("AAA", 1), _sell("BBB")))
    assert r["also_selling"] == ["AAA", "BBB"]
    assert r["line"] == (
        "🏠 AAA and BBB also have their own sell in today's Act Today call; the 🔎 Rebalance "
        "plan was sized before those sales and shrinks once they are logged; for those names, "
        "sell the larger of the two amounts, not the two added together.")


def test_exit_plus_partial_one_line_second_sentence_without_house():
    f = _flag(trim_target_dollar=25_000)
    r = scc.decide(f, _cov(_exit("BBB"), _sell("CCC", 2)))
    assert r["exited"] == ["BBB"] and r["also_selling"] == ["CCC"]
    assert r["netted"] and r["net_target_dollar"] == 15_000.0
    assert r["line"].count("🏠") == 1 and r["line"].startswith("🏠 ")
    assert r["line"].endswith(
        "CCC also has its own sell in today's Act Today call; the 🔎 Rebalance plan was "
        "sized before that sale and shrinks once it is logged; for that name, sell the larger "
        "of the two amounts, not the two added together.")
    assert "\n" not in r["line"]


def test_malformed_cover_entry_reads_as_partial_unknown_size():
    r = scc.decide(_flag(), {"BBB": "weird"})
    assert r["also_selling"] == ["BBB"] and not r["netted"]


# ── membership / case / legacy ────────────────────────────────────────────────

def test_case_insensitive_members_and_cover():
    f = _flag(trim_target_dollar=25_000)
    f["sector_members"] = [{"ticker": "bbb", "market_value": 10_000.0},
                           {"ticker": " aaa ", "market_value": 20_000.0}]
    r = scc.decide(f, {"BbB": {"exit": True, "shares": None}})
    assert r["exited"] == ["BBB"] and r["netted"]
    assert r["net_target_dollar"] == 15_000.0
    assert [c["ticker"] for c in r["candidates"]] == ["AAA"]


def test_candidate_removal_is_case_insensitive():
    f = _flag(trim_target_dollar=25_000)
    f["trim_candidates"][1]["ticker"] = "bbb"
    r = scc.decide(f, _exit("BBB"))
    assert [c["ticker"] for c in r["candidates"]] == ["AAA"]


def test_legacy_flag_without_sector_members_falls_back_to_candidates_and_recent():
    f = _flag(trim_target_dollar=25_000)
    del f["sector_members"]
    r = scc.decide(f, _exit("BBB"))                     # BBB is a trim candidate
    assert r["exited"] == ["BBB"] and r["netted"] and r["net_target_dollar"] == 15_000.0
    r2 = scc.decide(f, _exit("CCC"))                    # CCC is only in trim_excluded_recent
    assert r2["exited"] == ["CCC"] and r2["netted"] and r2["net_target_dollar"] == 20_000.0
    assert r2["candidates"] == f["trim_candidates"]     # CCC was never a candidate
    r3 = scc.decide(f, _exit("ZZZ"))
    assert r3["line"] is None


def test_legacy_flag_without_either_list_is_inert():
    f = _flag()
    del f["sector_members"], f["trim_candidates"], f["trim_excluded_recent"]
    r = scc.decide(f, _exit("AAA"))
    assert r["line"] is None and r["candidates"] == []


def test_members_dedup_and_skip_junk_entries():
    f = _flag()
    f["sector_members"] = [{"ticker": "AAA", "market_value": 20_000.0}, "junk", None,
                           {"ticker": "aaa", "market_value": 1.0}, {"ticker": ""},
                           {"ticker": None}]
    r = scc.decide(f, _exit("AAA"))
    assert r["exited"] == ["AAA"] and r["net_target_dollar"] == 0.0


def test_missing_sector_name_has_a_neutral_fallback():
    f = _flag(trim_target_dollar=25_000)
    del f["sector"]
    r = scc.decide(f, _exit("BBB"))
    assert "(sector)" in r["line"] and "None" not in r["line"]


# ── text rules ────────────────────────────────────────────────────────────────

def _all_lines():
    outs = []
    for tgt in (25_000, 10_000, 15_000):
        for cover in (_exit("BBB"), _sell("BBB", 12), _cov(_exit("BBB"), _sell("CCC", 7)),
                      _cov(_exit("AAA"), _exit("BBB"), _exit("CCC")),
                      _cov(_sell("AAA", 5), _sell("BBB", 5))):
            outs.append(scc.decide(_flag(trim_target_dollar=tgt), cover)["line"])
    f = _flag()
    f["sector_members"][1]["market_value"] = None
    outs.append(scc.decide(f, _exit("BBB"))["line"])
    return [o for o in outs if o]


def test_line_has_no_dollar_no_bold_no_share_count_digits():
    lines = _all_lines()
    assert len(lines) >= 10
    for ln in lines:
        assert "$" not in ln and "**" not in ln
        # only percentages (the SECTOR_ELEVATED level, the kept-as-cash weight)
        assert not re.search(r"\d", re.sub(r"\d+(?:\.\d)?%", "", ln)), ln
        assert "shares" not in ln


def test_covered_line_reads_constant_not_literal():
    r = scc.decide(_flag(), _exit("AAA"))               # post-sale weight at the warn level
    assert f"{SECTOR_ELEVATED:.0f}%" in r["line"]


# ── plan arithmetic ───────────────────────────────────────────────────────────

def test_trim_allocation_on_net_totals_the_net():
    f = _flag(trim_target_dollar=25_000, trim_target_pp=25.0)
    r = scc.decide(f, _exit("BBB"))
    alloc = trim_allocation(r["candidates"], r["net_target_dollar"], f["trim_target_denom"])
    assert alloc["target"] == pytest.approx(15_000.0)
    assert alloc["total_allocated"] == pytest.approx(15_000.0)
    assert alloc["shortfall"] == 0
    assert [row["ticker"] for row in alloc["rows"]] == ["AAA"]
    assert sum(row["cut_pp"] for row in alloc["rows"]) == pytest.approx(r["net_target_pp"])


def test_net_zero_allocates_nothing_so_app_must_not_fall_to_legacy_list():
    r = scc.decide(_flag(), _exit("AAA"))
    assert r["covered"]
    alloc = trim_allocation(r["candidates"], r["net_target_dollar"], 100_000.0)
    assert alloc["rows"] == [] and alloc["total_allocated"] == 0.0


def test_net_shortfall_when_remaining_candidates_cannot_cover():
    f = _flag(trim_target_dollar=40_000)
    r = scc.decide(f, _exit("AAA"))                    # net 20,000; only BBB (10,000) left
    alloc = trim_allocation(r["candidates"], r["net_target_dollar"], 100_000.0)
    assert alloc["total_allocated"] == pytest.approx(10_000.0)
    assert alloc["shortfall"] == pytest.approx(10_000.0)


# ── purity ────────────────────────────────────────────────────────────────────

def test_inputs_never_mutated():
    f = _flag(trim_target_dollar=25_000)
    c = _cov(_exit("BBB"), _sell("CCC", 2))
    f0, c0 = copy.deepcopy(f), copy.deepcopy(c)
    scc.decide(f, c)
    assert f == f0 and c == c0


def test_returned_candidates_is_a_copy_of_the_list():
    f = _flag()
    r = scc.decide(f, {})
    r["candidates"].append({"ticker": "X"})
    assert len(f["trim_candidates"]) == 2


def test_result_keys_are_the_spec_shape():
    assert set(scc.decide(_flag(), None)) == {
        "exited", "also_selling", "netted", "covered", "candidates",
        "net_target_dollar", "net_target_pp", "post_pct", "held_back", "line"}


def test_net_value_is_finite_float():
    r = scc.decide(_flag(trim_target_dollar=25_000), _exit("BBB"))
    assert isinstance(r["net_target_dollar"], float) and math.isfinite(r["net_target_dollar"])


# ── app.py wiring pins (AST via the shared cache; the suite bans ast.parse on app.py) ─────

def _render_act_card_node():
    import ast
    tree = parse_file_cached(_APP)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_render_act_card":
            return n
    raise AssertionError("_render_act_card not found")


def test_render_act_card_calls_sector_cap_cover_decide_with_the_view_cover():
    import ast
    fn = _render_act_card_node()
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "decide"
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "sector_cap_cover"]
    assert len(calls) == 1
    args = calls[0].args
    assert [type(a).__name__ for a in args] == ["Name", "Name"]
    assert args[0].id == "_rebal_flag" and args[1].id == "_db_view_cover"


def test_render_act_card_reads_the_netted_plan_fields_not_the_flag_target():
    import ast
    fn = _render_act_card_node()
    keys = {n.slice.value for n in ast.walk(fn)
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
            and n.value.id == "_sc" and isinstance(n.slice, ast.Constant)}
    assert {"line", "candidates", "net_target_dollar", "net_target_pp", "covered"} <= keys
    # the plan no longer reads the flag's own target / candidates
    flag_gets = {n.args[0].value for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "_rebal_flag" and n.args
                 and isinstance(n.args[0], ast.Constant)}
    assert "trim_target_dollar" not in flag_gets
    assert "trim_target_pp" not in flag_gets
    assert "trim_candidates" not in flag_gets


def test_render_act_card_line_goes_through_md_bold_and_covered_skips_trim_block():
    import ast
    fn = _render_act_card_node()
    src = source_segment(_APP.read_text(encoding="utf-8"), fn)
    assert "_md_bold(_sc['line'])" in src
    assert 'if _sc["covered"]:' in src and 'elif _trim:' in src
    # the covered branch precedes the trim branch (an empty allocation never reaches the
    # legacy priority list)
    assert src.index('if _sc["covered"]:') < src.index("elif _trim:")


def test_app_imports_the_module_and_synth_schema_untouched_by_name():
    text = _APP.read_text(encoding="utf-8")
    assert "from stock_analyzer import sector_cap_cover" in text


def test_render_act_card_held_back_reads_the_decided_list_in_both_branches():
    fn = _render_act_card_node()
    src = source_segment(_APP.read_text(encoding="utf-8"), fn)
    assert '_sc["held_back"]' in src
    assert 'trim_excluded_recent' not in src
    # one def + one call in the "Trim first" branch + one in the nothing-left branch
    assert src.count("def _held_back_caption():") == 1
    assert src.count("_held_back_caption()") == 3
