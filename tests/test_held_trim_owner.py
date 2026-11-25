"""stock_analyzer/held_trim_owner.py (EW C1, docs/plans/earnings-week-trim-consistency.md
§3): which Brief sell "names" a held ticker, and what Tune-up / Risk Analysis / the
Rebalancer say when they defer to it. The trap this pins: an Act Today `risk` card's
ticker is an attribution (the largest holding of an over-ceiling sector), never a name
being sold."""
import pandas as pd
import pytest

from stock_analyzer import held_trim_owner as hto
from stock_analyzer import trim_sizing as ts
from stock_analyzer.constants import (
    EARNINGS_IMMINENT_DAYS,
    SINGLE_NAME_CEILING,
    SINGLE_NAME_TRIM_TRIGGER,
)
from stock_analyzer.decision_bucket import EXIT_KINDS

pytestmark = pytest.mark.fast


def _act(kind, ticker="AAA"):
    return {"kind": kind, "ticker": ticker, "directive": "x"}


def _rev(atype, ticker="AAA", **action):
    return {"icon": "📅", "ticker": ticker, "action": {"type": atype, **action}}


# -- brief_sell_names_ticker --------------------------------------------------

@pytest.mark.parametrize("kind", sorted(EXIT_KINDS) + ["deterioration_trim", "risk_off_derisk"])
def test_act_sell_kinds_name_their_ticker(kind):
    assert hto.brief_sell_names_ticker(_act(kind), "AAA") is True
    assert hto.brief_sell_names_ticker(_act(kind), "aaa") is True
    assert hto.brief_sell_names_ticker(_act(kind, "aaa"), "AAA") is True
    assert hto.brief_sell_names_ticker(_act(kind), "BBB") is False


@pytest.mark.parametrize("kind", [
    "risk", "critical_news", "premortem_triggered", "macro", "macro_event",
    "macro_trim", "sector_concentration", "",
])
def test_non_sell_kinds_never_name_a_ticker(kind):
    # `risk` is the attribution card; critical_news / premortem are not sells.
    assert hto.brief_sell_names_ticker(_act(kind), "AAA") is False


@pytest.mark.parametrize("atype", ["TRIM_TO_TARGET", "TRIM_AND_TIGHTEN", "PROTECTIVE_TRIM"])
def test_review_trims_name_ticker_or_trim_ticker(atype):
    assert hto.brief_sell_names_ticker(_rev(atype), "AAA") is True
    assert hto.brief_sell_names_ticker(_rev(atype), "aaa") is True
    # a macro PROTECTIVE_TRIM carries ticker=None and its subject in trim_ticker
    macro = {"icon": "🌐", "ticker": None, "action": {"type": atype, "trim_ticker": "ccc"}}
    assert hto.brief_sell_names_ticker(macro, "CCC") is True
    assert hto.brief_sell_names_ticker(macro, "AAA") is False


@pytest.mark.parametrize("atype", ["WATCH", "HOLD", "TIGHTEN_ONLY", "DETERIORATION_WATCH", ""])
def test_review_non_sells_never_name_a_ticker(atype):
    assert hto.brief_sell_names_ticker(_rev(atype), "AAA") is False


@pytest.mark.parametrize("bad_item", [None, "AAA", 5, [], {}, {"kind": "stop_breach"},
                                       {"kind": "stop_breach", "ticker": ""},
                                       {"kind": "stop_breach", "ticker": None},
                                       {"action": "TRIM", "ticker": "AAA"}])
def test_garbage_and_blank_tickers_are_false(bad_item):
    assert hto.brief_sell_names_ticker(bad_item, "AAA") is False


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_blank_query_ticker_is_false(blank):
    assert hto.brief_sell_names_ticker(_act("stop_breach", "AAA"), blank) is False
    assert hto.brief_sell_names_ticker(_act("stop_breach", ""), blank) is False


# -- brief_sell_tickers -------------------------------------------------------

def test_brief_sell_tickers_over_both_lists_uppercased():
    act = [_act("stop_breach", "aaa"), _act("risk", "BBB"), _act("critical_news", "CCC"),
           _act("deterioration_trim", "ddd"), _act("risk_off_derisk", "EEE")]
    rev = [_rev("TRIM_TO_TARGET", "fff"), _rev("WATCH", "GGG"),
           {"ticker": None, "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "hhh"}},
           {"ticker": "III", "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "jjj"}}]
    assert hto.brief_sell_tickers(act, rev) == {"AAA", "DDD", "EEE", "FFF", "HHH", "III", "JJJ"}


def test_brief_sell_tickers_none_and_garbage_inputs():
    assert hto.brief_sell_tickers(None, None) == set()
    assert hto.brief_sell_tickers([], []) == set()
    assert hto.brief_sell_tickers([None, "x", 3], [None, {}]) == set()


# -- tuneup_single_name_disposition -------------------------------------------

def test_tuneup_brief_owns_when_a_sell_names_it():
    d = hto.tuneup_single_name_disposition("aaa", {"AAA"}, {}, 19.0, 8, 100)
    assert d == {"state": "brief_owns",
                 "text": ("AAA already has a sell in today's Act Today call; its single-name "
                          f"ceiling card returns if AAA is still over {SINGLE_NAME_CEILING:.0f}% "
                          "after that.")}


def test_tuneup_earnings_note_below_the_line_in_the_window():
    # N=10: floor == SINGLE_NAME_CEILING; a 14% name is under it, earnings in 3 days.
    floor = ts.earnings_overweight_floor(10)
    d = hto.tuneup_single_name_disposition("AAA", set(), {"AAA": 3}, floor - 1, 10, 100)
    assert d["state"] == "earnings_note"
    assert d["text"] == (f"Earnings in 3d: at or under the {floor:.1f}% earnings-week line, so the "
                         f"earnings-week rule sizes nothing before the report; "
                         f"this card is standing work.")


def test_tuneup_earnings_note_says_today_on_day_zero():
    d = hto.tuneup_single_name_disposition("AAA", set(), {"AAA": 0}, 14.0, 10, 100)
    assert d["state"] == "earnings_note" and d["text"].startswith("Earnings today: at or under the ")


def test_tuneup_shows_when_there_is_an_in_window_decision_but_no_brief_sell():
    # decision exists (above the line) but the Brief did not sell it: fail toward the sell
    d = hto.tuneup_single_name_disposition("AAA", set(), {"AAA": 3}, 19.0, 15, 100)
    assert d == {"state": "show", "text": None}
    # zero-share decision still counts as a decision
    d0 = hto.tuneup_single_name_disposition("AAA", set(), {"AAA": 3}, 19.0, 15, 0)
    assert d0 == {"state": "show", "text": None}


@pytest.mark.parametrize("days", [None, -1, EARNINGS_IMMINENT_DAYS + 1])
def test_tuneup_shows_outside_the_window(days):
    ed = {} if days is None else {"AAA": days}
    assert hto.tuneup_single_name_disposition("AAA", set(), ed, 14.0, 10, 100) == {
        "state": "show", "text": None}


def test_tuneup_none_position_count_does_not_raise():
    # R2-N3: n_positions=None reads as the no-N floor, like the other surfaces.
    floor = ts.earnings_overweight_floor(0)
    d = hto.tuneup_single_name_disposition("AAA", set(), {"AAA": 3}, floor - 1, None, 100)
    assert d["state"] == "earnings_note" and f"{floor:.1f}%" in d["text"]


@pytest.mark.parametrize("w", [None, float("nan"), float("inf"), "x"])
def test_tuneup_unknown_weight_shows_never_claims_under_the_line(w):
    # EW C3: an unread weight can't be called "under the line"
    d = hto.tuneup_single_name_disposition("AAA", set(), {"AAA": 3}, w, 10, 100)
    assert d == {"state": "show", "text": None}


def test_tuneup_unknown_weight_still_defers_to_a_brief_sell():
    d = hto.tuneup_single_name_disposition("AAA", {"AAA"}, {"AAA": 3}, None, 10, 100)
    assert d["state"] == "brief_owns"


def test_tuneup_none_inputs_are_safe():
    assert hto.tuneup_single_name_disposition("AAA", None, None, 16.0, 10, 100)["state"] == "show"


# -- rebalancer_trim_disposition ----------------------------------------------

def test_rebalancer_defers_only_when_reduce_calls_are_ok():
    d = hto.rebalancer_trim_disposition("AAA", {"AAA"}, True, {}, "Trim 19.0% → 10.0%.")
    assert d == {"state": "brief_owns", "text": "Today's Act Today call: Trim 19.0% → 10.0%."}
    for flag in (False, None):
        d = hto.rebalancer_trim_disposition("AAA", {"AAA"}, flag, {}, "Trim")
        assert d == {"state": "show", "text": None}


@pytest.mark.parametrize("blank", [None, "", "   ", 5])
def test_rebalancer_blank_directive_uses_the_plain_line(blank):
    d = hto.rebalancer_trim_disposition("aaa", {"AAA"}, True, {}, blank)
    assert d == {"state": "brief_owns", "text": "Sized by today's Act Today call (🏠 Home)."}


def test_rebalancer_unverified_but_in_window_gets_the_earnings_line_not_a_deferral():
    d = hto.rebalancer_trim_disposition("AAA", {"AAA"}, False, {"AAA": 3}, "x")
    assert d == {"state": "earnings_note",
                 "text": "Earnings in 3d: check 🏠 Home's Act Today before trading this name."}


def test_rebalancer_earnings_line_day_zero_and_edges():
    assert hto.rebalancer_trim_disposition("AAA", set(), True, {"AAA": 0}, None)["text"] == (
        "Earnings today: check 🏠 Home's Act Today before trading this name.")
    assert hto.rebalancer_trim_disposition(
        "AAA", set(), True, {"AAA": EARNINGS_IMMINENT_DAYS}, None)["state"] == "earnings_note"
    for days in (-1, EARNINGS_IMMINENT_DAYS + 1):
        assert hto.rebalancer_trim_disposition(
            "AAA", set(), True, {"AAA": days}, None) == {"state": "show", "text": None}


def test_rebalancer_a_brief_sell_beats_the_earnings_line():
    d = hto.rebalancer_trim_disposition("AAA", {"AAA"}, True, {"AAA": 3}, "Trim now")
    assert d["state"] == "brief_owns"


def test_constants_used_here_exist_for_the_texts():
    # the dispositions' wording never embeds a policy number except the floor, which
    # comes from trim_sizing; keep the imports honest.
    assert SINGLE_NAME_CEILING < SINGLE_NAME_TRIM_TRIGGER


# -- book_inputs (EW C3) ------------------------------------------------------

def _df(rows):
    return pd.DataFrame(rows)


def _row(t, w, shares=10, exact=None):
    r = {"Ticker": t, "Weight (%)": w, "Shares": shares}
    if exact is not None:
        r["Shares Exact"] = exact
    return r


def test_book_inputs_found_and_case_insensitive():
    df = _df([_row("AAA", 16.5, 12, exact=12.25), _row("BBB", 5.0)])
    for q in ("AAA", "aaa", " Aaa "):
        assert hto.book_inputs(df, q) == {"weight_pct": 16.5, "n_positions": 2, "shares": 12.25}
    assert hto.book_inputs(df, "BBB") == {"weight_pct": 5.0, "n_positions": 2, "shares": 10.0}


def test_book_inputs_missing_ticker_keeps_the_count():
    df = _df([_row("AAA", 16.5), _row("BBB", 5.0)])
    assert hto.book_inputs(df, "ZZZ") == {"weight_pct": None, "n_positions": 2, "shares": None}
    assert hto.book_inputs(df, None) == {"weight_pct": None, "n_positions": 2, "shares": None}


def test_book_inputs_nan_weight_is_none():
    df = _df([_row("AAA", float("nan"))])
    assert hto.book_inputs(df, "AAA") == {"weight_pct": None, "n_positions": 1, "shares": 10.0}


@pytest.mark.parametrize("df", [None, pd.DataFrame()])
def test_book_inputs_none_and_empty_book(df):
    assert hto.book_inputs(df, "AAA") == {"weight_pct": None, "n_positions": 0, "shares": None}


def test_book_inputs_no_weight_column_is_none_not_a_raise():
    df = pd.DataFrame([{"Ticker": "AAA", "Shares": 3}])
    assert hto.book_inputs(df, "AAA") == {"weight_pct": None, "n_positions": 1, "shares": 3.0}


# -- view_sell_tickers (EW C3 round 2, R3-B1) --------------------------------

def _view(active, state="act", resolved=()):
    return {"state": state, "active": list(active), "resolved": list(resolved),
            "aware": [], "n_active": len(active)}


@pytest.mark.parametrize("bad", [None, "x", 3, [], {"state": "offline", "active": [_act("stop_breach")]}])
def test_view_sell_tickers_none_when_the_view_is_unusable_or_offline(bad):
    assert hto.view_sell_tickers(bad) is None


def test_view_sell_tickers_reads_active_only_both_lanes():
    active = [
        _act("risk", "AAA"),                                        # attribution: not a sell of AAA
        {**_rev("TRIM_TO_TARGET", "bbb"), "_source": "review"},     # review lane keeps its shape
        {**_act("stop_breach", "CCC"), "_source": "act", "_breach_state": "active"},
        _act("deterioration_trim", "DDD"),
        _act("risk_off_derisk", "EEE"),
        _rev("WATCH", "FFF"),
        _act("critical_news", "GGG"),
        {"ticker": None, "action": {"type": "PROTECTIVE_TRIM", "trim_ticker": "hhh"}},
    ]
    resolved = [_act("stop_breach", "ZZZ")]
    assert hto.view_sell_tickers(_view(active, resolved=resolved)) == {
        "BBB", "CCC", "DDD", "EEE", "HHH"}


def test_view_sell_tickers_empty_active_is_an_empty_set_not_none():
    assert hto.view_sell_tickers(_view([], state="clear")) == set()
    assert hto.view_sell_tickers({"state": "clear"}) == set()


def test_view_sell_tickers_a_risk_attribution_and_a_review_trim_on_one_ticker():
    # the old first-reduce-item-per-ticker gap: the `risk` item must not hide the review trim
    active = [_act("risk", "AAA"), _rev("TRIM_TO_TARGET", "AAA")]
    assert hto.view_sell_tickers(_view(active)) == {"AAA"}


def test_view_sell_tickers_through_the_real_act_today_view_demotes_a_recovered_breach():
    import pandas as pd
    from stock_analyzer.act_today_view import act_today_view
    from stock_analyzer.constants import STOP_RECOVERY_MARGIN_PCT
    brief = {"act_today": [_act("stop_breach", "AAA")], "review_list": [
        _rev("TRIM_TO_TARGET", "BBB")]}
    recovered = pd.DataFrame([{"Ticker": "AAA", "Gap to Stop (%)": STOP_RECOVERY_MARGIN_PCT + 5.0,
                               "Stop": 9.0}])
    still = pd.DataFrame([{"Ticker": "AAA", "Gap to Stop (%)": -1.0, "Stop": 9.0}])
    v1 = act_today_view(brief, False, recovered, {}, STOP_RECOVERY_MARGIN_PCT)
    assert hto.view_sell_tickers(v1) == {"BBB"}                       # AAA's breach resolved
    v2 = act_today_view(brief, False, still, {}, STOP_RECOVERY_MARGIN_PCT)
    assert hto.view_sell_tickers(v2) == {"AAA", "BBB"}
    v3 = act_today_view(brief, True, still, {}, STOP_RECOVERY_MARGIN_PCT)   # Brief offline
    assert hto.view_sell_tickers(v3) is None


# -- reconcile_tuneup (EW C3 round 2, R3-B1) ----------------------------------

def _card(t, **extra):
    return {"type": "single_name_concentration", "tickers": [t], **extra}


def _payload(t, card=None):
    return {"text": f"{t} deferred text", "card": card if card is not None else _card(t)}


def test_reconcile_stands_only_for_names_the_view_still_sells():
    base = [{"type": "beta", "tickers": ["X"]}]
    deferred = {"BBB": _payload("BBB"), "AAA": _payload("AAA")}
    out = hto.reconcile_tuneup(base, deferred, {"AAA"})
    assert out["deferred_texts"] == {"AAA": "AAA deferred text"}
    assert out["cards"] == base + [_card("BBB")]


def test_reconcile_view_unknown_restores_everything_and_shows_no_caption():
    deferred = {"BBB": _payload("BBB"), "AAA": _payload("AAA")}
    out = hto.reconcile_tuneup([], deferred, None)
    assert out["deferred_texts"] == {}
    assert out["cards"] == [_card("AAA"), _card("BBB")]                  # ticker order


def test_reconcile_restores_the_card_exactly_with_its_earnings_note():
    card = _card("AAA", earnings_note="Earnings in 3d: ...")
    out = hto.reconcile_tuneup([], {"AAA": _payload("AAA", card)}, set())
    assert out == {"cards": [card], "deferred_texts": {}}


def test_reconcile_old_shape_text_only_kept_while_the_sell_is_live():
    old = {"AAA": "old text"}
    assert hto.reconcile_tuneup([], old, {"AAA"}) == {
        "cards": [], "deferred_texts": {"AAA": "old text"}}
    assert hto.reconcile_tuneup([], old, set()) == {"cards": [], "deferred_texts": {}}
    assert hto.reconcile_tuneup([], old, None) == {"cards": [], "deferred_texts": {}}


def test_reconcile_mixed_shapes_and_case():
    deferred = {"AAA": "old text", "bbb": _payload("bbb")}
    out = hto.reconcile_tuneup([], deferred, {"aaa"})
    assert out["deferred_texts"] == {"AAA": "old text"}
    assert out["cards"] == [_card("bbb")]


@pytest.mark.parametrize("cards, deferred", [
    (None, None), ([], None), (None, {}), ([_card("X")], "junk"), ([_card("X")], []),
])
def test_reconcile_non_dict_or_none_inputs(cards, deferred):
    out = hto.reconcile_tuneup(cards, deferred, {"AAA"})
    assert out == {"cards": list(cards or []), "deferred_texts": {}}


def test_reconcile_does_not_mutate_its_inputs():
    base = [_card("X")]
    deferred = {"BBB": _payload("BBB")}
    hto.reconcile_tuneup(base, deferred, set())
    assert base == [_card("X")] and list(deferred) == ["BBB"]


# -- risk_analysis_single_name_line (EW C3) -----------------------------------

def _line(sells, days=None, w=14.0, n=10, shares=100, ticker="AAA"):
    return hto.risk_analysis_single_name_line(
        ticker, sells, {} if days is None else {"AAA": days}, w, n, shares)


def test_risk_line_unverified_when_the_cross_check_could_not_run():
    d = _line(None)
    assert d["state"] == "unverified" and d["tier"] == "caption"
    assert d["text"] == ("⚠️ Today's Act Today calls couldn't be checked against this card "
                         "(🏠 Home's Act Today isn't available for the current book: not built "
                         "yet, failed, or out of date), so it may repeat a sell Act Today "
                         "already sizes for AAA.")


def test_risk_line_unverified_beats_everything_even_in_window():
    assert _line(None, days=3)["state"] == "unverified"


def test_risk_line_brief_owns():
    d = _line({"AAA"}, days=3)
    assert d["state"] == "brief_owns" and d["tier"] == "info"
    assert d["text"] == ("AAA already has a sell in today's Act Today call (🏠 Home). "
                         "Follow that call first; this ceiling trim is not an extra sell on top "
                         f"of it today, and applies again only if AAA is still over "
                         f"{SINGLE_NAME_CEILING:.0f}% afterwards.")
    assert _line({"aaa"}, ticker="aaa")["state"] == "brief_owns"


def test_risk_line_earnings_note_when_in_window_with_no_decision():
    floor = ts.earnings_overweight_floor(10)
    d = _line(set(), days=3, w=floor - 1)
    assert d["state"] == "earnings_note" and d["tier"] == "caption"
    assert d["text"] == (f"Earnings in 3d: at or under the {floor:.1f}% earnings-week line, so the "
                         f"earnings-week rule sizes nothing before the report; "
                         f"this card is standing work.")


@pytest.mark.parametrize("kw", [
    {"days": None},                          # no earnings date
    {"days": -1},
    {"days": EARNINGS_IMMINENT_DAYS + 1},
    {"days": 3, "w": 19.0, "n": 15},         # a decision exists: the Brief didn't sell, show
    {"days": 3, "w": None},                  # unknown weight
])
def test_risk_line_show_otherwise(kw):
    assert _line(set(), **kw) == {"state": "show", "tier": None, "text": None}


def test_risk_line_texts_carry_no_dollar_or_bold_markers():
    floor = ts.earnings_overweight_floor(10)
    for d in (_line(None), _line({"AAA"}), _line(set(), days=0, w=floor - 1),
              _line(set(), days=3, w=floor - 1)):
        assert d["text"] and "$" not in d["text"] and "**" not in d["text"]
