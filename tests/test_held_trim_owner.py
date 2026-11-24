"""stock_analyzer/held_trim_owner.py (EW C1, docs/plans/earnings-week-trim-consistency.md
§3): which Brief sell "names" a held ticker, and what Tune-up / Risk Analysis / the
Rebalancer say when they defer to it. The trap this pins: an Act Today `risk` card's
ticker is an attribution (the largest holding of an over-ceiling sector), never a name
being sold."""
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
                 "text": "AAA's single-name size is handled by today's Act Today call."}


def test_tuneup_earnings_note_below_the_line_in_the_window():
    # N=10: floor == SINGLE_NAME_CEILING; a 14% name is under it, earnings in 3 days.
    floor = ts.earnings_overweight_floor(10)
    d = hto.tuneup_single_name_disposition("AAA", set(), {"AAA": 3}, floor - 1, 10, 100)
    assert d["state"] == "earnings_note"
    assert d["text"] == (f"Earnings in 3d: below the {floor:.1f}% earnings-week line, so nothing "
                         f"is due before the report; this is standing work.")


def test_tuneup_earnings_note_says_today_on_day_zero():
    d = hto.tuneup_single_name_disposition("AAA", set(), {"AAA": 0}, 14.0, 10, 100)
    assert d["state"] == "earnings_note" and d["text"].startswith("Earnings today: below the ")


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
