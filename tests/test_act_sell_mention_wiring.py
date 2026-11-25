"""U6b C2 + C3 (docs/plans/signals-catalyst-act-trim-mention-u6b.md, sections 2.2, 4 and 7 items
7-9): the app.py wiring of `act_sell_mention` (render only; app.py has no unit tests).

* The shared `_verified_act_today_sells()` helper is EXECUTED here: its source is cut out of
  app.py by AST and run against a stubbed session, so the real gate legs (reduce cache state,
  session Brief offline, book epoch, Home's build date) are exercised, not just grepped.
* Grep / AST pins for the four call sites (📡 Signals, 🔔 Catalyst Watch, 🔗 Risk Analysis,
  ⚖️ Rebalancer), the `act_elsewhere` render branch, the card tags and lines, and that no
  new line is rendered through `unsafe_allow_html`.
* C3 (U6b-5 (a)): a Brief built on an earlier ET day no longer defers a Rebalancer trim or
  annotates a Risk Analysis card; the shared caption shows instead.
"""
import ast
from functools import lru_cache
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stock_analyzer import act_sell_mention as asm
from stock_analyzer import act_today_view as atv
from stock_analyzer import coord_freshness as cf
from stock_analyzer import held_trim_owner as hto
from stock_analyzer import rebalancer as reb
from stock_analyzer.constants import STOP_RECOVERY_MARGIN_PCT
from stock_analyzer.util import get_or_offline
from tests._ast_cache import parse_file_cached, source_segment
from tests.test_rebalancer import _drift_row

pytestmark = pytest.mark.fast

_APP = Path(__file__).resolve().parents[1] / "app.py"
_ET = ZoneInfo("America/New_York")
_TODAY = date(2026, 11, 25)
_NOW = datetime(2026, 11, 25, 21, 0, tzinfo=_ET)      # 21:00 ET = already 26 Nov in UTC


def _src() -> str:
    return _APP.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _func_src(name: str) -> str:
    tree = parse_file_cached(_APP)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return source_segment(_src(), node)


def _helper(session: dict, *, today=_TODAY, now=_NOW, view_fn=None):
    """`_verified_act_today_sells` cut out of app.py and bound to a stubbed session."""
    ns = {
        "st": SimpleNamespace(session_state=session),
        "_get_or_offline": get_or_offline,
        "act_today_view": view_fn or atv.act_today_view,
        "act_sell_mention": asm,
        "coord_freshness": cf,
        "_today_et": lambda: today,
        "_now_et": lambda: now,
    }
    for fn in ("_coord_cache_state", "_coord_epoch", "_verified_act_today_sells"):
        exec(compile(_func_src(fn), "<app.py>", "exec"), ns)
    return ns["_verified_act_today_sells"]


def _session(*, built=_TODAY, epoch=3, stamp=3, act=None, rev=None, reduce_calls=None,
             brief_offline=False, bundle_offline=False, memo=True):
    pe = pd.DataFrame([{"Ticker": "SNOW", "Gap to Stop (%)": 10.0, "Stop": 45.0}])
    brief = {"act_today": list(act or []), "review_list": list(rev or [])}
    s = {
        "_reduce_calls": {} if reduce_calls is None else reduce_calls,
        "_daily_brief_offline": brief_offline,
        "_coord_epoch": epoch,
        "_coord_stamps": {"_reduce_calls": stamp},
        "_port_df_enriched": pe,
        "_live_prices": {},
    }
    if memo:
        s["_home_synth_cache"] = {
            "date": None if built is None else built.isoformat(),
            "bundle": {"_daily_brief": brief, "_daily_brief_offline": bundle_offline},
        }
    return s


_SELL = [{"kind": "deterioration_trim", "ticker": "SNOW"}]


# -- the helper, executed -------------------------------------------------------------------

def test_helper_returns_the_act_sell_set_when_every_leg_holds():
    assert _helper(_session(act=_SELL))() == {"SNOW"}


def test_helper_checked_and_clean_is_the_empty_set_not_none():
    got = _helper(_session())()
    assert got == set() and got is not None


def test_helper_evening_et_build_is_today_even_though_utc_is_tomorrow():
    # 21:00 ET on the 25th; the memo date is the ET date, compared with the ET date.
    assert _helper(_session(act=_SELL), now=_NOW)() == {"SNOW"}


@pytest.mark.parametrize("name,kw", [
    ("memo from yesterday", dict(built=_TODAY - timedelta(days=1))),
    ("memo from a Friday seen on a Saturday", dict(built=date(2026, 11, 20))),
    ("memo with no date", dict(built=None)),
    ("no memo at all", dict(memo=False)),
    ("a trade logged after the build (epoch moved on)", dict(epoch=4, stamp=3)),
    ("the reduce cache was never stamped", dict(stamp=None)),
    ("the session's Brief failed", dict(brief_offline=True)),
    ("the memo's bundle is the offline stub", dict(bundle_offline=True)),
])
def test_helper_returns_none_when_it_could_not_check(name, kw):
    s = _session(act=_SELL, **kw)
    if kw.get("stamp", 0) is None:
        s["_coord_stamps"] = {}
    assert _helper(s)() is None, name


def test_helper_returns_none_when_the_reduce_cache_is_missing_or_offline():
    s = _session(act=_SELL)
    del s["_reduce_calls"]
    assert _helper(s)() is None
    s = _session(act=_SELL)
    s["_reduce_calls"] = None
    assert _helper(s)() is None


def test_helper_returns_none_and_never_raises_when_the_view_build_fails(caplog):
    def boom(**_kw):
        raise RuntimeError("view build failed")
    with caplog.at_level("WARNING"):
        got = _helper(_session(act=_SELL), view_fn=boom)()
    assert got is None
    assert any("Act Today sell cross-check failed" in r.getMessage() for r in caplog.records)


def test_helper_ignores_a_risk_attribution_card_and_a_covered_loser():
    s = _session(act=[{"kind": "risk", "ticker": "SNOW", "action": "RISK"}],
                 rev=[{"icon": "📅", "ticker": "SNOW",
                       "action": {"type": "WATCH", "covered_by": "SNOW"}}],
                 reduce_calls={"SNOW": {}})
    assert _helper(s)() == set()


# -- helper wiring pins (grep / AST) --------------------------------------------------------

def test_helper_reads_the_documented_inputs_and_is_defined_before_its_first_caller():
    h = _func_src("_verified_act_today_sells")
    assert '_coord_cache_state("_reduce_calls")' in h
    assert '"_daily_brief_offline"' in h
    assert "coord_freshness.classify(" in h and '"_reduce_calls"' in h
    assert '_get_or_offline(_vs_memo, "date")' in h
    assert "_today_et()" in h and "act_sell_mention.verified_sells(" in h
    assert "act_today_view(" in h
    assert "except Exception as _vs_err:" in h and "getLogger(__name__).warning(" in h
    assert h.rstrip().endswith("return None")
    src = _src()
    def_at = src.index("def _verified_act_today_sells():")
    calls = [i for i in range(len(src)) if src.startswith("_verified_act_today_sells()", i)]
    assert len(calls) == 5          # def + Signals + Catalyst + Risk Analysis + Rebalancer
    assert calls[0] == def_at + len("def ")
    assert all(c > def_at for c in calls)


def test_the_four_surfaces_call_the_shared_helper():
    src = _src()
    assert "_sa_sells = _verified_act_today_sells()" in src
    assert "_pb_sells = _verified_act_today_sells()" in src
    assert "_sn_sell = _verified_act_today_sells()" in src
    assert "_rb_sells = _verified_act_today_sells()" in src


def _signals_tab_segment() -> str:
    src = _src()
    start = src.index("    with _sa_tab1:")
    return src[start:src.index("    with _sa_tab2:", start)]


def test_signals_computes_the_sells_once_before_and_independent_of_the_offline_branch():
    seg = _signals_tab_segment()
    assert seg.count("_verified_act_today_sells()") == 1
    assert seg.index("_sa_sells = _verified_act_today_sells()") < seg.index("if _sa_offline:")
    # the unverified caption shows when the sells are None; offline pages still name the sells
    assert "if _sa_sells is None:" in seg
    assert 'st.caption(act_sell_mention.unverified_caption("signals"))' in seg
    assert "elif _sa_offline:" in seg


def test_signals_folds_the_sells_into_the_headline_after_decide():
    seg = _signals_tab_segment()
    assert seg.index("signals_headline.decide(") < seg.index("act_sell_mention.signals_headline(")
    # card_tickers = the rendered action cards; breach_named = breached alerts the Brief's
    # sell clause already names (active_breach is empty unless the Brief is built)
    assert 'act_sell_mention.signals_headline(\n                _sa_head, _sa_sells,' in seg
    assert '[_a_act["ticker"] for _a_act in actions]' in seg
    assert '_a_al.get("breached")} & set(_sa_active_br))' in seg
    assert '_sa_head = _sa_mention["head"]' in seg


def test_act_elsewhere_is_rendered_with_st_info_and_the_line_with_st_caption():
    tree = parse_file_cached(_APP)
    hit = [n for n in ast.walk(tree) if isinstance(n, ast.If)
           and "['kind'] == 'act_elsewhere'" in ast.unparse(n.test)
           and "st.info(" in ast.unparse(n.body[0])]
    assert len(hit) == 1
    seg = _signals_tab_segment()
    assert 'st.caption(_sa_mention["line"])' in seg
    assert "st.caption(_sa_off_line)" in seg


def test_signals_cards_carry_the_tag_and_the_line_under_what_triggered_this():
    seg = _signals_tab_segment()
    assert "{act_sell_mention.card_tag(ticker, _sa_sells)}" in seg
    trig = seg.index("WHAT TRIGGERED THIS")
    line_at = seg.index("act_sell_mention.signals_card_line(")
    assert trig < line_at < seg.index("ev_col, check_col = st.columns")
    assert 'ticker, act["type"], _sa_sells)' in seg
    assert "st.info(_sa_card_line)" in seg


def _catalyst_segment() -> str:
    src = _src()
    start = src.index("def _render_holdings_earnings(port_df, held_data):")
    return src[start:src.index('if st.session_state.get("_readonly"):', start)]


def test_catalyst_computes_once_and_shows_the_caption_above_the_playbook():
    seg = _catalyst_segment()
    assert seg.count("_verified_act_today_sells()") == 1
    sells_at = seg.index("_pb_sells = _verified_act_today_sells()")
    assert seg.index("st.markdown(\"#### 📋 Pre-Earnings Playbook\")") < sells_at
    assert sells_at < seg.index("for _pb in _playbook:")
    assert 'st.caption(act_sell_mention.unverified_caption("catalyst"))' in seg
    # the caption is inside the non-empty-playbook block
    assert seg.index("if _playbook:") < sells_at


def test_catalyst_header_label_is_unchanged_and_only_gains_the_tag():
    seg = _catalyst_segment()
    assert '_act_label = "Watch" if _action == "MONITOR" else _action' in seg
    assert "{act_sell_mention.card_tag(_pb['ticker'], _pb_sells)}" in seg
    action_box = seg.index("Pre-Earnings Action:")
    line_at = seg.index("act_sell_mention.catalyst_line(")
    assert action_box < line_at < seg.index("# Watch flags ride along")
    assert '_pb["ticker"], _action, _pb_sells)' in seg
    assert "st.info(_pb_sell_line)" in seg
    # the MONITOR re-evaluate text is not vetoed by the mention
    assert "be prepared to exit at the open" in seg


def test_every_expander_label_that_takes_a_tag_is_a_card_header():
    tree = parse_file_cached(_APP)
    tagged = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute) and n.func.attr == "expander"
              and n.args and "act_sell_mention.card_tag(" in ast.unparse(n.args[0])]
    assert len(tagged) == 2        # Signals action cards + Catalyst playbook cards


def test_no_act_sell_mention_text_is_rendered_through_unsafe_allow_html():
    tree = parse_file_cached(_APP)
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and any(
                k.arg == "unsafe_allow_html" for k in n.keywords)):
            continue
        for sub in ast.walk(n):
            assert not (isinstance(sub, ast.Name) and sub.id == "act_sell_mention"), \
                f"act_sell_mention text inside unsafe_allow_html at line {n.lineno}"
            assert not (isinstance(sub, ast.Name) and sub.id in (
                "_sa_card_line", "_pb_sell_line", "_sa_mention", "_sa_off_line")), n.lineno


def test_the_new_text_is_rendered_only_by_st_info_or_st_caption():
    tree = parse_file_cached(_APP)
    allowed = {"info", "caption"}
    bad = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "st"
                and n.func.attr not in allowed and n.func.attr != "expander"):
            if any(isinstance(s, ast.Name) and s.id in (
                    "_sa_card_line", "_pb_sell_line", "_sa_off_line") for s in ast.walk(n)):
                bad.append((n.lineno, n.func.attr))
    assert not bad, bad


def test_navigation_is_untouched_by_the_new_code():
    h = _func_src("_verified_act_today_sells")
    assert "nav_page" not in h and "_pending_page" not in h


# -- C3: Risk Analysis and the Rebalancer move onto the shared gate ----------------------------

def test_risk_analysis_and_rebalancer_no_longer_build_their_own_view():
    src = _src()
    ra = src[src.index('if _rtype == "single_name_concentration":'):]
    ra = ra[:ra.index("_left, _right = st.columns([1, 1])")]
    assert "act_today_view(" not in ra and "coord_freshness" not in ra
    rb = src[src.index("_rb_reduce_verified = ("):]
    rb = rb[:rb.index("# ── Sentiment Velocity")]
    assert "act_today_view(" not in rb
    assert "coord_freshness.classify(" not in rb


def _risk_state(sells):
    d = hto.risk_analysis_single_name_line("SNOW", sells, {}, 17.4, 8, 100)
    return d["state"]


def test_a_day_old_memo_gives_risk_analysis_no_brief_owns_and_the_shared_caption():
    old = _session(act=_SELL, built=_TODAY - timedelta(days=1))
    fresh = _session(act=_SELL)
    assert _risk_state(_helper(fresh)()) == "brief_owns"
    sells = _helper(old)()
    assert sells is None
    # the page then shows the unverified state, and app.py swaps in the shared caption
    assert _risk_state(sells) == "unverified"
    cap = asm.unverified_caption("risk", "SNOW")
    assert "built on an earlier day" in cap and "SNOW" in cap


def _two_trims():
    return [
        _drift_row("AAA", "TRIM", 8.0, 8_000.0, price=100.0, current=18.0, target=10.0),
        _drift_row("BBB", "TRIM", 6.0, 6_000.0, price=100.0, current=16.0, target=10.0),
    ]


def test_a_day_old_memo_defers_no_rebalancer_trim_and_the_shared_caption_shows():
    act = [{"kind": "deterioration_trim", "ticker": "AAA"}]
    # a book whose view really sells AAA
    s_ok = _session(act=act)
    s_ok["_port_df_enriched"] = pd.DataFrame(
        [{"Ticker": "AAA", "Gap to Stop (%)": 10.0, "Stop": 45.0}])
    s_old = _session(act=act, built=_TODAY - timedelta(days=1))
    s_old["_port_df_enriched"] = s_ok["_port_df_enriched"]
    fresh_sells = _helper(s_ok)()
    assert fresh_sells == {"AAA"}
    plan_fresh = reb.build_rebalance_plan(pd.DataFrame(_two_trims()), 100_000.0,
                                          sell_tickers=fresh_sells)
    assert [d["ticker"] for d in plan_fresh["deferred_trims"]] == ["AAA"]

    old_sells = _helper(s_old)()
    assert old_sells is None
    plan_old = reb.build_rebalance_plan(pd.DataFrame(_two_trims()), 100_000.0,
                                        sell_tickers=old_sells)
    assert plan_old["deferred_trims"] == [] and plan_old["deferred_trims_count"] == 0
    assert [t["ticker"] for t in plan_old["trims"]] == ["AAA", "BBB"]
    assert "built on an earlier day" in asm.unverified_caption("rebalancer")


def test_a_stale_book_still_withholds_both_surfaces():
    s = _session(act=_SELL, epoch=5, stamp=4)
    assert _helper(s)() is None


def test_the_helper_uses_the_stop_recovery_constant_not_a_literal():
    h = _func_src("_verified_act_today_sells")
    assert "STOP_RECOVERY_MARGIN_PCT" in h
    assert STOP_RECOVERY_MARGIN_PCT is not None
