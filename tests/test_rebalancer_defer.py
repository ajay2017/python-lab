"""EW C4 (docs/plans/earnings-week-trim-c4-spec.md): the ⚖️ Rebalancer defers a trim row to
today's Act Today sell (a deferred row: no share count of its own, out of every trim total,
listed under a visible banner), adds one earnings line to an in-window trim it keeps, owns the
priority / expanded decision that used to live in app.py, and prints its institutional-lens
rule from the constants. Plus the app.py wiring greps (app.py has no unit tests)."""
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer import rebalancer as reb
from stock_analyzer.constants import (
    COMPOSITE_HOLD,
    EARNINGS_IMMINENT_DAYS,
    SINGLE_NAME_CEILING,
    STOP_RECOVERY_MARGIN_PCT,
    TOLERANCE_OK,
    TOLERANCE_WATCH,
)
from tests.test_rebalancer import _drift_row

pytestmark = pytest.mark.fast

_TOTAL = 100_000.0

EXIT = {"exit": True, "shares": None}


def cov(shares):
    """A non-exit cover entry (an Act sale of `shares` whole shares, None = unknown)."""
    return {"exit": False, "shares": shares}


def _plan(rows, **kw):
    return reb.build_rebalance_plan(pd.DataFrame(rows), _TOTAL, **kw)


def _two_trims():
    # AAA is the sold name; BBB stays a trim. Both past the action band (status TRIM).
    return [
        _drift_row("AAA", "TRIM", 8.0, 8_000.0, price=100.0, current=18.0, target=10.0),
        _drift_row("BBB", "TRIM", 6.0, 6_000.0, price=100.0, current=16.0, target=10.0),
    ]


# -- the deferred row ----------------------------------------------------------------------

def test_a_sold_overweight_row_is_deferred_not_trimmed_and_out_of_every_total():
    base = _plan(_two_trims())
    plan = _plan(_two_trims(), act_sell_cover={"AAA": EXIT})
    assert [t["ticker"] for t in plan["trims"]] == ["BBB"]
    assert [d["ticker"] for d in plan["deferred_trims"]] == ["AAA"]
    assert plan["deferred_trims_count"] == 1
    d = plan["deferred_trims"][0]
    assert set(d) == {"ticker", "status", "current_pct", "target_pct", "drift_pp", "text"}
    assert d["text"] == "Today's Act Today call already sells this name (🏠 Home)."
    assert (d["current_pct"], d["target_pct"], d["drift_pp"], d["status"]) == (18.0, 10.0, 8.0, "TRIM")
    # the kept trim is exactly what it was; the totals count it alone
    assert plan["trims"][0] == next(t for t in base["trims"] if t["ticker"] == "BBB")
    assert plan["total_trim_value"] == 6_000.0
    assert plan["trim_pct"] == 6.0
    assert plan["rebalance_pct"] == 6.0           # kept trims + adds only
    assert plan["trim_action_needed"] is True     # BBB is still a TRIM with a share count
    assert base["total_trim_value"] == 14_000.0


def test_when_every_trim_is_deferred_there_is_no_trim_action_and_no_trim_total():
    plan = _plan(_two_trims(), act_sell_cover={"AAA": EXIT, "bbb": EXIT})
    assert plan["trims"] == [] and plan["deferred_trims_count"] == 2
    assert [d["ticker"] for d in plan["deferred_trims"]] == ["AAA", "BBB"]   # sorted
    assert plan["total_trim_value"] == 0 and plan["trim_pct"] == 0.0
    assert plan["trim_action_needed"] is False and plan["rebalance_pct"] == 0.0


def test_no_sell_set_means_no_deferral_and_the_plan_is_identical_to_today():
    plain = _plan(_two_trims())
    assert _plan(_two_trims(), act_sell_cover=None) == plain
    assert _plan(_two_trims(), act_sell_cover=None, earnings_days={}) == plain
    assert plain["deferred_trims"] == [] and plain["deferred_trims_count"] == 0
    assert [t["ticker"] for t in plain["trims"]] == ["AAA", "BBB"]
    # an empty set is a checked-clean sell list, not "couldn't check": nothing deferred either
    assert _plan(_two_trims(), act_sell_cover={}) == plain


def test_a_sold_ok_row_stays_in_ok_and_is_listed_as_deferred():
    rows = [_drift_row("AAA", "OK", 1.0, 500.0, current=11.0, target=10.0)]
    plan = _plan(rows, act_sell_cover={"AAA": EXIT})
    assert plan["ok"] == ["AAA"]
    assert [d["ticker"] for d in plan["deferred_trims"]] == ["AAA"]
    assert plan["trims"] == []


def test_a_sold_underweight_row_keeps_the_add_gating_and_is_not_deferred():
    rows = [_drift_row("AAA", "ADD", -6.0, -6_000.0, current=4.0, target=10.0)]
    blocked = _plan(rows, act_sell_cover={"AAA": EXIT}, reduce_call_set={"AAA"})
    assert [r["ticker"] for r in blocked["reduce_blocked_adds"]] == ["AAA"]
    assert blocked["deferred_trims"] == [] and blocked["adds"] == []
    # not in reduce_call_set: the add row is the same as without the new args
    free = _plan(rows, act_sell_cover={"AAA": EXIT})
    assert free["deferred_trims"] == []
    assert free == _plan(rows)


# -- the earnings line ---------------------------------------------------------------------

@pytest.mark.parametrize("days,when", [(0, "today"), (3, "in 3d"), (EARNINGS_IMMINENT_DAYS, f"in {EARNINGS_IMMINENT_DAYS}d")])
def test_an_in_window_trim_with_no_sell_gets_the_earnings_line(days, when):
    plan = _plan(_two_trims(), act_sell_cover={}, earnings_days={"AAA": days})
    aaa = next(t for t in plan["trims"] if t["ticker"] == "AAA")
    bbb = next(t for t in plan["trims"] if t["ticker"] == "BBB")
    assert aaa["earnings_note"] == f"Earnings {when}: check 🏠 Home's Act Today before trading this name."
    assert "earnings_note" not in bbb


@pytest.mark.parametrize("days", [-1, EARNINGS_IMMINENT_DAYS + 1])
def test_out_of_window_trim_gets_no_earnings_line(days):
    plan = _plan(_two_trims(), act_sell_cover={}, earnings_days={"AAA": days})
    assert all("earnings_note" not in t for t in plan["trims"])


def test_the_earnings_line_never_survives_on_a_deferred_row():
    plan = _plan(_two_trims(), act_sell_cover={"AAA": EXIT}, earnings_days={"AAA": 2})
    assert [t["ticker"] for t in plan["trims"]] == ["BBB"]
    assert plan["deferred_trims"][0]["text"] == "Today's Act Today call already sells this name (🏠 Home)."


# -- priority / expanded: the rule moved verbatim out of app.py ----------------------------

def _old_rule(t):
    return ("HIGH" if (t["urgency"] >= 40 or t["status"] == "TRIM") else "MEDIUM",
            t["urgency"] >= 40)


@pytest.mark.parametrize("signal,score,status,drift", [
    ("Hold", 60.0, "DRIFTING", 3.0),                     # urgency 10 -> MEDIUM, closed
    ("Hold", 60.0, "TRIM", 6.0),                         # urgency 30 -> HIGH via status, closed
    ("Hold", COMPOSITE_HOLD - 1, "DRIFTING", 3.0),       # urgency 30 -> MEDIUM, closed
    ("Sell", 60.0, "DRIFTING", TOLERANCE_OK - 0.5),      # urgency exactly 40 -> HIGH, open
    ("Sell", 60.0, "DRIFTING", 3.0),                     # 50
    ("Strong Sell", COMPOSITE_HOLD - 1, "TRIM", 6.0),    # 90
])
def test_priority_and_expanded_match_the_old_app_rule(signal, score, status, drift):
    plan = _plan([_drift_row("AAA", status, drift, drift * 1000.0, signal=signal, score=score)])
    t = plan["trims"][0]
    assert (t["priority"], t["expanded"]) == _old_rule(t)
    assert t["priority"] in ("HIGH", "MEDIUM")


def test_the_boundary_urgency_rows_read_as_expected():
    at40 = _plan([_drift_row("AAA", "DRIFTING", TOLERANCE_OK - 0.5, 1_500.0, signal="Sell")])["trims"][0]
    assert at40["urgency"] == 40 and at40["priority"] == "HIGH" and at40["expanded"] is True
    watch = _plan([_drift_row("AAA", "DRIFTING", 3.0, 3_000.0)])["trims"][0]
    assert watch["urgency"] < 40 and watch["priority"] == "MEDIUM" and watch["expanded"] is False
    trim = _plan([_drift_row("AAA", "TRIM", TOLERANCE_WATCH + 1, 6_000.0)])["trims"][0]
    assert trim["urgency"] < 40 and trim["priority"] == "HIGH" and trim["expanded"] is False


# -- the institutional lens prints the rule from the constants ------------------------------

def test_institutional_lens_interpolates_the_constants():
    lens = _plan(_two_trims())["trims"][0]["institutional_lens"]
    assert f"no single name above {SINGLE_NAME_CEILING:.0f}% of portfolio, regardless" in lens
    assert f"by {TOLERANCE_WATCH:.0f}pp or more" in lens
    src = Path(reb.__file__).read_text(encoding="utf-8")
    assert "above 15% of portfolio" not in src and "by 5pp or more" not in src


# -- integration: the real Brief + the real Act Today view ---------------------------------

def _book_plan(book, sells, days=None):
    drift = reb.compute_drift(book, reb.equal_weights(book), _TOTAL)
    return reb.build_rebalance_plan(
        drift, _TOTAL, act_sell_cover=sells,
        earnings_days=({"AAA": days} if days is not None else None))


def test_an_earnings_trim_on_a_name_defers_that_name_on_the_rebalancer():
    from stock_analyzer import act_sell_cover
    from stock_analyzer.act_today_view import act_today_view
    from stock_analyzer.daily_briefing import build_daily_briefing
    from tests.test_earnings_week_consistency import (
        _book, _held, _sn_rec, _PNLS, _TODAY)

    # A book whose share count matches its weight (19% of 100k at 50 = 380 shares), so the
    # Rebalancer's own sizing (to the 12.5% equal-weight target: ~130 shares) is comparable
    # with the earnings-week sale (to 10%: ~180 shares): the sale covers the row (EW example A).
    book = _book(19.0, 8, _PNLS[1], shares=380)
    book["Market Value"] = 19_000.0
    brief = build_daily_briefing(
        port_df=book, alert_list=[], risk_recs=[_sn_rec(19.0)], news_items=[],
        macro_events=[], held_data=_held(3), scanner_results=None,
        portfolio_value=100_000.0, today=_TODAY, market_context={"tone": "bull"})
    sells = act_sell_cover.view_sell_cover(
        act_today_view(brief, False, book, {}, STOP_RECOVERY_MARGIN_PCT))
    assert set(sells) == {"AAA"}
    plan = _book_plan(book, sells, days=3)
    assert [d["ticker"] for d in plan["deferred_trims"]] == ["AAA"]
    assert all(t["ticker"] != "AAA" for t in plan["trims"])
    # the same book with an unverified view (None) still shows AAA's own trim
    shown = _book_plan(book, None, days=3)
    assert shown["deferred_trims"] == []
    assert any(t["ticker"] == "AAA" and "earnings_note" in t for t in shown["trims"])


def test_a_recovered_stop_breach_is_not_deferred():
    from stock_analyzer import act_sell_cover
    from tests.test_earnings_week_consistency import _breach_book, _breach_brief, _live_view

    brief = _breach_brief()
    active = act_sell_cover.view_sell_cover(_live_view(brief, -11.0))
    assert set(active) == {"AAA"}
    assert [d["ticker"] for d in _book_plan(_breach_book(), active)["deferred_trims"]] == ["AAA"]
    recovered = act_sell_cover.view_sell_cover(_live_view(brief, STOP_RECOVERY_MARGIN_PCT + 5.0))
    assert recovered == {}
    plan = _book_plan(_breach_book(), recovered)
    assert plan["deferred_trims"] == []
    assert any(t["ticker"] == "AAA" for t in plan["trims"])


def test_a_risk_attribution_item_alone_does_not_defer_its_ticker():
    from stock_analyzer import act_sell_cover
    from tests.test_earnings_week_consistency import _book, _PNLS

    view = {"state": "act", "resolved": [], "aware": [], "n_active": 1,
            "active": [{"kind": "risk", "ticker": "AAA"}]}
    sells = act_sell_cover.view_sell_cover(view)
    assert sells == {}
    plan = _book_plan(_book(19.0, 8, _PNLS[1]), sells)
    assert plan["deferred_trims"] == []
    assert any(t["ticker"] == "AAA" for t in plan["trims"])


# -- app.py wiring (render only) -----------------------------------------------------------

def _app_src():
    return Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")


def _rebalancer_seg():
    src = _app_src()
    start = src.index("_rb_reduce_verified = (")
    return src[start:src.index("# ── Sentiment Velocity", start)]


def test_the_rebalancer_builds_its_sells_from_the_act_today_view_behind_the_verified_check():
    # U6b C3: the Act Today view + the liveness / date gate moved into the shared
    # `_verified_act_today_sells()` helper (pinned in test_act_sell_mention_wiring.py);
    # the Rebalancer only calls it and shows the shared caption.
    seg = _rebalancer_seg()
    assert "act_sell_cover=_rb_sells" in seg and "earnings_days=_rb_earn_days" in seg
    assert "_rb_sells = _verified_act_today_sell_cover()" in seg
    assert "held_trim_owner.view_sell_tickers" not in seg and "act_today_view(" not in seg
    # never the raw `_reduce_calls` keys for the deferral (the ADD gate keeps using them)
    assert seg.count('session_state.get("_reduce_calls")') == 1
    assert '_coord_cache_state("_reduce_calls") == "ready"' in seg
    assert '"_daily_brief_offline"' in seg
    # unverified => None (a caption says so), never an empty set
    assert "if _rb_sells is None:" in seg
    assert 'act_sell_mention.unverified_caption("rebalancer")' in seg
    assert "_trim_sizing.earnings_days_by_ticker(held_data, _today_et())" in seg


def test_the_rebalancer_banner_and_trim_rows_read_the_plan_not_a_local_decision():
    seg = _rebalancer_seg()
    assert "_tr_pri  = _tr[\"priority\"]" in seg and '_tr_exp  = _tr["expanded"]' in seg
    assert "_tr_pri  = \"HIGH\"" not in _app_src()
    assert '_tr["urgency"]' not in seg
    assert "🏠 Today's Act Today call already sells these names, so no trim is sized for them here today; a row comes back if it is still over target after that sell:" in seg
    assert '_rb_plan["trims"] or _rb_plan["deferred_trims"]' in seg
    assert '_tr.get("earnings_note")' in seg
    # DeferSizeBlind: the help text names both the covered (not counted) and partial rows
    assert "Rows today's Act Today sell fully covers aren't counted; " in seg
    assert "a row it only partly covers, or whose size couldn't be " in seg
    assert "compared, is counted at its own size." in seg
    assert '_rb_plan["act_partial_count"] > 0' in seg
    # the "all within tolerance" success must not fire while a deferred row is listed
    assert '_rb_plan["deferred_trims"]):' in seg


def test_the_drift_chart_legend_interpolates_the_tolerance_constants():
    seg = _rebalancer_seg()
    assert "overweight >{TOLERANCE_WATCH:.0f}pp (trim)" in seg
    assert "overweight {TOLERANCE_OK:.0f}–{TOLERANCE_WATCH:.0f}pp (watch)" in seg
    assert "underweight >{TOLERANCE_OK:.0f}pp (drift; adds are Grow Today's call)" in seg
    assert "within ±{TOLERANCE_OK:.0f}pp tolerance" in seg
    assert "overweight >5pp" not in seg and "2–5pp" not in seg and "±2pp" not in seg


# -- DeferSizeBlind: a sell smaller than the row does not hide it ---------------------------

def test_a_covering_sized_sell_defers_the_row_equality_covers():
    # AAA's row is 80 shares (8,000 / 100): a sale of 80 covers it, 79 does not
    plan = _plan(_two_trims(), act_sell_cover={"AAA": cov(80)})
    assert [d["ticker"] for d in plan["deferred_trims"]] == ["AAA"]
    assert [t["ticker"] for t in plan["trims"]] == ["BBB"] and plan["act_partial_count"] == 0
    plan = _plan(_two_trims(), act_sell_cover={"AAA": cov(79)})
    assert plan["deferred_trims"] == [] and plan["deferred_trims_count"] == 0
    assert [t["ticker"] for t in plan["trims"]] == ["AAA", "BBB"]


def test_a_partial_row_stays_at_full_size_counts_in_every_total_and_carries_the_note():
    from stock_analyzer.act_sell_cover import cover_note
    base = _plan(_two_trims())
    plan = _plan(_two_trims(), act_sell_cover={"AAA": cov(1)})
    aaa = next(t for t in plan["trims"] if t["ticker"] == "AAA")
    base_aaa = next(t for t in base["trims"] if t["ticker"] == "AAA")
    assert aaa["shares_delta"] == 80 == base_aaa["shares_delta"]
    assert aaa["act_cover_note"] == cover_note("partial", "AAA", "row") and aaa["act_partial"] is True
    # priority / expanded / urgency are the row's own, untouched by the cover
    for k in ("priority", "expanded", "urgency", "action_detail", "drift_val"):
        assert aaa[k] == base_aaa[k], k
    bbb = next(t for t in plan["trims"] if t["ticker"] == "BBB")
    assert "act_partial" not in bbb and "act_cover_note" not in bbb
    assert plan["act_partial_count"] == 1
    # the partial row counts in the totals, like a plain row
    for k in ("total_trim_value", "trim_pct", "rebalance_pct", "trim_action_needed"):
        assert plan[k] == base[k], k
    assert plan["deferred_trims"] == []


def test_an_unknown_size_sell_also_keeps_the_row_with_the_unknown_line():
    from stock_analyzer.act_sell_cover import cover_note
    plan = _plan(_two_trims(), act_sell_cover={"AAA": cov(None)})
    aaa = next(t for t in plan["trims"] if t["ticker"] == "AAA")
    assert aaa["act_cover_note"] == cover_note("unknown", "AAA", "row")
    assert plan["act_partial_count"] == 1 and plan["deferred_trims_count"] == 0


def test_the_cover_line_takes_precedence_over_the_earnings_line():
    plan = _plan(_two_trims(), act_sell_cover={"AAA": cov(1)}, earnings_days={"AAA": 2})
    aaa = next(t for t in plan["trims"] if t["ticker"] == "AAA")
    assert "earnings_note" not in aaa and aaa["act_partial"] is True


def test_a_row_with_no_usable_price_has_an_unknown_size_and_never_fully_defers():
    rows = [_drift_row("AAA", "TRIM", 8.0, 8_000.0, price=0.0, current=18.0, target=10.0)]
    plan = _plan(rows, act_sell_cover={"AAA": cov(500)})
    assert plan["deferred_trims"] == [] and plan["trims"][0]["shares_delta"] == 0
    assert plan["trims"][0]["act_partial"] is True
    # but an exit still covers it
    assert [d["ticker"] for d in _plan(rows, act_sell_cover={"AAA": EXIT})["deferred_trims"]] == ["AAA"]


def test_a_sub_share_row_sizes_no_trade_and_any_sell_covers_it():
    rows = [_drift_row("AAA", "TRIM", 6.0, 60.0, price=100.0, current=16.0, target=10.0)]
    plan = _plan(rows, act_sell_cover={"AAA": cov(None)})
    assert [d["ticker"] for d in plan["deferred_trims"]] == ["AAA"] and plan["trims"] == []


def test_an_ok_row_is_deferred_by_any_sell_and_never_partial():
    rows = [_drift_row("AAA", "OK", 1.0, 500.0, current=11.0, target=10.0)]
    for entry in (cov(1), cov(None), EXIT):
        plan = _plan(rows, act_sell_cover={"AAA": entry})
        assert [d["ticker"] for d in plan["deferred_trims"]] == ["AAA"]
        assert plan["act_partial_count"] == 0 and plan["trims"] == []


def test_the_empty_plan_has_the_new_count_and_no_cover_means_no_partial_rows():
    assert reb.build_rebalance_plan(pd.DataFrame(), _TOTAL)["act_partial_count"] == 0
    assert _plan(_two_trims())["act_partial_count"] == 0
    assert _plan(_two_trims(), act_sell_cover=None)["act_partial_count"] == 0


def test_the_defect_end_to_end_a_one_share_macro_trim_does_not_hide_a_thirty_percent_name():
    # A 30% holding, score >= 55, and a macro 1-share PROTECTIVE_TRIM on it: the Rebalancer's
    # row (to the ceiling, ~half the position) stays, in the totals, beside the "do that sale
    # first" line.
    from stock_analyzer import act_sell_cover
    review = [{"icon": "🌐", "ticker": None,
               "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "AAA", "trim_shares": 1}}]
    cover = act_sell_cover.sell_cover_summary([], review)
    assert cover == {"AAA": {"exit": False, "shares": 1}}
    rows = [_drift_row("AAA", "TRIM", 20.0, 20_000.0, price=100.0, current=30.0, target=10.0)]
    plan = _plan(rows, act_sell_cover=cover)
    assert plan["deferred_trims"] == [] and plan["deferred_trims_count"] == 0
    assert [t["ticker"] for t in plan["trims"]] == ["AAA"]
    assert plan["trims"][0]["shares_delta"] == 200 and plan["trims"][0]["act_partial"] is True
    assert plan["trim_pct"] == 20.0 and plan["trim_action_needed"] is True
    assert plan["act_partial_count"] == 1
    note = plan["trims"][0]["act_cover_note"]
    assert "$" not in note and "**" not in note and "200" not in note and "1 share" not in note


def test_the_rebalancer_header_tag_info_line_and_help_wiring():
    seg = _rebalancer_seg()
    assert '_tr.get("act_partial")' in seg and "act_sell_mention.card_tag(" in seg
    assert 'st.info(_tr["act_cover_note"])' in seg
    # the note is plain text through st.info: never raw HTML
    assert "_tr['act_cover_note']" not in seg
