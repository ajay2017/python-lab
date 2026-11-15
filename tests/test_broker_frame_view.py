"""stock_analyzer.broker_frame_view -- the 💰 Account "Net capital check" readout
of the Robinhood-positions net capital (U7 plan C2 + C3), the drift-banner sizing
clause and the cash-note helpers. Display only: strings and rows."""
import re

import pandas as pd
import pytest

from stock_analyzer import broker_frame as bf
from stock_analyzer import broker_frame_view as bfv
from stock_analyzer.constants import BROKER_FRAME_PAIR_MAX_MINUTES

pytestmark = pytest.mark.fast

NOW = pd.Timestamp("2026-11-13T20:00:00Z")
CAP = pd.Timestamp("2026-11-13T16:19:00Z")      # 11:19 AM EST
PX = {"A": 100.0, "B": 100.0}


def _frame(**over):
    f = {"state": "ok", "adjust": 0.0, "rows": [], "unpriced": [],
         "captured_at": CAP, "age_hours": 3.7}
    f.update(over)
    return f


def _row(tk, js, bs, price=100.0):
    return {"ticker": tk, "journal_shares": js, "broker_shares": bs,
            "diff": js - bs, "price": price, "dollars": (js - bs) * price}


def _unescaped_dollar(s):
    return re.search(r"(?<!\\)\$", s) is not None


# -- ok: same / gap lower / gap higher ---------------------------------------

def test_ok_same():
    v = bfv.shadow_lines(_frame(adjust=0.4), 10000.0, mask=False)
    assert v["caption"].startswith("🧪 Net capital check: ")
    assert "shadow" not in v["caption"]
    assert "journal \\$10,000 · Robinhood positions \\$10,000 · same" in v["caption"]
    assert "gap" not in v["caption"]
    assert v["detail_rows"] == [] and v["detail_title"] is None


def test_ok_same_negative_tiny_adjust():
    assert " · same" in bfv.shadow_lines(_frame(adjust=-0.99), 10000.0, mask=False)["caption"]


def test_ok_gap_lower():
    v = bfv.shadow_lines(_frame(adjust=1500.0), 10000.0, mask=False)
    assert ("journal \\$10,000 · Robinhood positions \\$8,500 · gap \\$1,500 "
            "(Robinhood lower — new-position sizes are capped at Robinhood's "
            "figure)") in v["caption"]
    assert "same" not in v["caption"]


def test_ok_gap_higher():
    v = bfv.shadow_lines(_frame(adjust=-2000.0), 10000.0, mask=False)
    assert ("journal \\$10,000 · Robinhood positions \\$12,000 · gap \\$2,000 "
            "(Robinhood higher — sizing keeps the journal figure)") in v["caption"]


def test_gap_of_exactly_one_dollar_is_a_gap():
    v = bfv.shadow_lines(_frame(adjust=1.0), 10000.0, mask=False)
    assert "gap \\$1 (Robinhood lower — new-position sizes are capped" in v["caption"]


def test_negative_journal_total_formats():
    v = bfv.shadow_lines(_frame(adjust=500.0), -1000.0, mask=False)
    assert "journal -\\$1,000 · Robinhood positions -\\$1,500" in v["caption"]


# -- positions synced time (ET) ------------------------------------------------

def test_synced_time_est():
    v = bfv.shadow_lines(_frame(), 1000.0, mask=False)
    assert v["caption"].endswith(" · positions synced Fri Nov 13, 11:19 AM ET")


def test_synced_time_edt_dst_date():
    # 2026-07-10 15:15Z = 11:15 AM EDT (UTC-4); in EST it would read 10:15.
    v = bfv.shadow_lines(_frame(captured_at=pd.Timestamp("2026-07-10T15:15:00Z")),
                         1000.0, mask=False)
    assert v["caption"].endswith(" · positions synced Fri Jul 10, 11:15 AM ET")


def test_synced_time_dst_change_day_uses_the_offset_in_force():
    # 2026-11-01 (fall back): 12:00Z is 7:00 AM EST. 2026-03-08 (spring
    # forward): 15:00Z is 11:00 AM EDT.
    for iso, want in (("2026-11-01T12:00:00Z", "Sun Nov 1, 7:00 AM ET"),
                      ("2026-03-08T15:00:00Z", "Sun Mar 8, 11:00 AM ET")):
        v = bfv.shadow_lines(_frame(captured_at=pd.Timestamp(iso)), 1000.0, mask=False)
        assert v["caption"].endswith("positions synced " + want), v["caption"]


def test_synced_time_pm_and_noon_midnight():
    cases = {"2026-11-13T17:00:00Z": "12:00 PM", "2026-11-13T21:15:00Z": "4:15 PM",
             "2026-11-13T05:05:00Z": "12:05 AM"}
    for iso, clock in cases.items():
        v = bfv.shadow_lines(_frame(captured_at=pd.Timestamp(iso)), 1000.0, mask=False)
        assert f", {clock} ET" in v["caption"], (iso, v["caption"])


def test_synced_time_string_stamp_and_missing():
    v = bfv.shadow_lines(_frame(captured_at="2026-11-13T11:19:00-05:00"), 1000.0, mask=False)
    assert v["caption"].endswith("positions synced Fri Nov 13, 11:19 AM ET")
    for missing in (None, "garbage"):
        v = bfv.shadow_lines(_frame(captured_at=missing), 1000.0, mask=False)
        assert "positions synced" not in v["caption"]


# -- not-ok states ---------------------------------------------------------------

_REASONS = {
    bf.STATE_NO_SNAPSHOT: "no positions snapshot saved yet",
    bf.STATE_UNREADABLE: "the positions snapshot couldn't be read",
    bf.STATE_NO_CASH: "no cash record",
    bf.STATE_NO_HOLDINGS: "holdings couldn't be read",
    bf.STATE_UNPAIRED: "positions and cash weren't saved by the same sync",
    bf.STATE_IMPLAUSIBLE: "Robinhood's snapshot looks incomplete",
    bf.STATE_NO_PRICES: "no prices for your holdings yet",
}


def test_every_non_ok_state_has_a_reason():
    states = {v for k, v in vars(bf).items() if k.startswith("STATE_")}
    assert states - {bf.STATE_OK} == set(_REASONS)


@pytest.mark.parametrize("state,reason", sorted(_REASONS.items()))
def test_non_ok_state_reason(state, reason):
    v = bfv.shadow_lines(_frame(state=state, adjust=None), 10000.0, mask=False)
    assert v["caption"] == ("🧪 Net capital check: Robinhood positions "
                            f"not compared — {reason} (sizing uses the journal figure)")
    assert v["detail_rows"] == [] and v["detail_title"] is None


def test_non_ok_never_shows_a_figure():
    v = bfv.shadow_lines(_frame(state=bf.STATE_UNPAIRED, adjust=None), 10000.0, mask=False)
    assert "$" not in v["caption"]
    assert not re.search(r"[0-9]", v["caption"])


def test_unpaired_hand_entered_cash():
    f = _frame(state=bf.STATE_UNPAIRED, adjust=None)
    assert "cash was entered by hand" in bfv.shadow_lines(
        f, 1.0, mask=False, cash_note="typed in")["caption"]
    assert "cash was entered by hand" in bfv.shadow_lines(
        f, 1.0, mask=False, cash_note=None)["caption"]
    # the sync's own note: pairing failed on timing, not on a hand edit
    assert "weren't saved by the same sync" in bfv.shadow_lines(
        f, 1.0, mask=False, cash_note=bf.BROKER_CASH_NOTE)["caption"]
    # note not supplied: the generic reason
    assert "weren't saved by the same sync" in bfv.shadow_lines(f, 1.0, mask=False)["caption"]


def test_hand_cash_note_only_affects_unpaired():
    f = _frame(state=bf.STATE_NO_CASH, adjust=None)
    assert "no cash record" in bfv.shadow_lines(f, 1.0, mask=False, cash_note="typed")["caption"]


def test_odd_frames_do_not_raise():
    assert "no check was available" in bfv.shadow_lines(None, 1.0, mask=False)["caption"]
    assert "unexpected result" in bfv.shadow_lines({"state": "weird"}, 1.0, mask=False)["caption"]
    assert "unexpected result" in bfv.shadow_lines(
        _frame(adjust=None), 1.0, mask=False)["caption"]
    assert "unexpected result" in bfv.shadow_lines(
        _frame(adjust=float("nan")), 1.0, mask=False)["caption"]
    assert "journal total was unavailable" in bfv.shadow_lines(
        _frame(), None, mask=False)["caption"]
    assert "journal total was unavailable" in bfv.shadow_lines(
        _frame(), float("nan"), mask=False)["caption"]


# -- masking and escaping --------------------------------------------------------

def test_mask_hides_every_money_digit():
    f = _frame(adjust=1500.0, captured_at=None,
               rows=[_row("A", 100, 85, 100.0)], unpriced=["ZZZ"])
    v = bfv.shadow_lines(f, 123456.0, mask=True)
    assert v["caption"] == ("🧪 Net capital check: "
                            "journal •••••• · Robinhood positions •••••• · gap •••••• "
                            "(Robinhood lower — new-position sizes are capped at "
                            "Robinhood's figure)")
    assert not re.search(r"\d", v["caption"]) and "$" not in v["caption"]
    for r in v["detail_rows"]:
        for key in ("Price", "Value of difference (journal − Robinhood)"):
            assert not re.search(r"\d", r[key]) and "$" not in r[key], r


def test_mask_on_same_and_higher():
    for adj in (0.0, -900.0):
        v = bfv.shadow_lines(_frame(adjust=adj, captured_at=None), 5000.0, mask=True)
        assert not re.search(r"\d", v["caption"]) and "$" not in v["caption"]


def test_caption_has_no_unescaped_dollar():
    for adj in (0.0, 1500.0, -2000.0):
        for mask in (False, True):
            v = bfv.shadow_lines(_frame(adjust=adj), 10000.0, mask=mask)
            assert not _unescaped_dollar(v["caption"]), v["caption"]


def test_detail_rows_are_plain_text_not_escaped():
    f = _frame(adjust=1500.0, rows=[_row("A", 100, 85)])
    r = bfv.shadow_lines(f, 10000.0, mask=False)["detail_rows"][0]
    assert "\\" not in r["Price"] and "\\" not in r["Value of difference (journal − Robinhood)"]


# -- detail rows -----------------------------------------------------------------

def test_detail_rows_content_and_sign():
    f = _frame(adjust=1000.0,
               rows=[_row("A", 100, 90, 100.0), _row("B", 50, 62.5, 80.0)])
    v = bfv.shadow_lines(f, 10000.0, mask=False)
    assert v["detail_title"] == "Where the two figures differ (2 names)"
    a, b = v["detail_rows"]
    assert a == {"Ticker": "A", "Journal shares": "100", "Robinhood shares": "90",
                 "Price": "$100.00",
                 "Value of difference (journal − Robinhood)": "+$1,000"}
    assert b["Ticker"] == "B" and b["Journal shares"] == "50"
    assert b["Robinhood shares"] == "62.5" and b["Price"] == "$80.00"
    assert b["Value of difference (journal − Robinhood)"] == "-$1,000"


def test_detail_title_singular():
    f = _frame(adjust=500.0, rows=[_row("A", 10, 5, 100.0)])
    assert bfv.shadow_lines(f, 10000.0, mask=False)["detail_title"] == \
        "Where the two figures differ (1 name)"


def test_unpriced_names_listed_and_counted():
    f = _frame(adjust=1000.0, rows=[_row("A", 100, 90)], unpriced=["BRK.B", "NEWCO"])
    v = bfv.shadow_lines(f, 10000.0, mask=False)
    assert v["detail_title"] == "Where the two figures differ (3 names)"
    assert [r["Ticker"] for r in v["detail_rows"]] == ["A", "BRK.B", "NEWCO"]
    for r in v["detail_rows"][1:]:
        assert r["Price"] == "no price"
        assert r["Value of difference (journal − Robinhood)"] == "no price — left out"


def test_unpriced_only_still_shows_detail_and_same_caption():
    v = bfv.shadow_lines(_frame(adjust=0.0, unpriced=["NEWCO"]), 10000.0, mask=False)
    assert " · same" in v["caption"]
    assert v["detail_title"] == "Where the two figures differ (1 name)"
    assert len(v["detail_rows"]) == 1


def test_no_user_facing_text_says_shadow():
    for f in (_frame(), _frame(adjust=500.0), _frame(adjust=-500.0),
              _frame(state=bf.STATE_UNPAIRED, adjust=None)):
        assert "shadow" not in bfv.shadow_lines(f, 1000.0, mask=False)["caption"].lower()
    assert "shadow" not in (bfv.drift_sizing_clause(_frame(adjust=500.0)) or "").lower()


def _excl(tk, js, bs):
    return {"ticker": tk, "journal_shares": js, "broker_shares": bs, "diff": js - bs}


def test_excluded_split_rows_listed_without_price_or_dollars():
    f = _frame(adjust=0.0, excluded=[_excl("NVDA", 10, 40)])
    v = bfv.shadow_lines(f, 10000.0, mask=False)
    assert v["detail_title"] == "Where the two figures differ (1 name)"
    assert v["detail_rows"] == [{
        "Ticker": "NVDA", "Journal shares": "10", "Robinhood shares": "40",
        "Price": "—",
        "Value of difference (journal − Robinhood)": "split since the sync — left out"}]
    assert " · same" in v["caption"]


def test_excluded_rows_count_with_priced_and_unpriced_and_survive_mask():
    f = _frame(adjust=1000.0, rows=[_row("A", 100, 90)], unpriced=["NEWCO"],
               excluded=[_excl("NVDA", 10, 40)])
    v = bfv.shadow_lines(f, 10000.0, mask=True)
    assert v["detail_title"] == "Where the two figures differ (3 names)"
    assert [r["Ticker"] for r in v["detail_rows"]] == ["A", "NEWCO", "NVDA"]
    assert v["detail_rows"][-1]["Price"] == "—"


def test_frame_without_excluded_key_still_renders():
    f = _frame(adjust=500.0, rows=[_row("A", 10, 5, 100.0)])
    f.pop("excluded", None)
    assert len(bfv.shadow_lines(f, 10000.0, mask=False)["detail_rows"]) == 1


def test_no_detail_when_no_differences():
    v = bfv.shadow_lines(_frame(), 10000.0, mask=False)
    assert v["detail_rows"] == [] and v["detail_title"] is None


def test_fractional_shares_trim_zeros():
    f = _frame(adjust=10.0, rows=[_row("A", 10.123456, 10.0, 80.0)])
    r = bfv.shadow_lines(f, 10000.0, mask=False)["detail_rows"][0]
    assert r["Journal shares"] == "10.123456" and r["Robinhood shares"] == "10"


# -- end to end with the real frame builder --------------------------------------

def test_with_real_broker_frame_unlogged_sell():
    snap = {"positions": {"A": 100, "B": 40}, "account_ids": ["x"],
            "all_accounts_ok": True, "captured_at": "2026-11-13T11:19:00-05:00",
            "kinds": {}}
    cash = {"cash_balance": -5000.0, "note": bf.BROKER_CASH_NOTE,
            "updated_at": "2026-11-13T16:20:00+00:00"}
    hold = pd.DataFrame([{"Ticker": "A", "Shares": 100}, {"Ticker": "B", "Shares": 50}])
    frame = bf.broker_frame(snap, cash, hold, PX, NOW,
                            pair_max_minutes=BROKER_FRAME_PAIR_MAX_MINUTES)
    v = bfv.shadow_lines(frame, 10000.0, mask=False, cash_note=cash["note"])
    assert ("Robinhood positions \\$9,000 · gap \\$1,000 (Robinhood lower — "
            "new-position sizes are capped at Robinhood's figure)") in v["caption"]
    assert v["detail_rows"][0]["Ticker"] == "B"
    assert v["detail_rows"][0]["Value of difference (journal − Robinhood)"] == "+$1,000"


# -- Home drift banner clause ------------------------------------------------------

_CLAUSE = "New-position sizes are capped at Robinhood's lower figure until this is fixed."


def test_drift_sizing_clause_boundary_is_the_same_below_dollar():
    just_under = bfv._SAME_BELOW_USD - 0.01
    assert bfv.drift_sizing_clause(_frame(adjust=just_under)) is None
    assert bfv.drift_sizing_clause(_frame(adjust=bfv._SAME_BELOW_USD)) == _CLAUSE
    assert bfv.drift_sizing_clause(_frame(adjust=1500.0)) == _CLAUSE


def test_drift_sizing_clause_silent_when_robinhood_higher_same_or_not_compared():
    assert bfv.drift_sizing_clause(_frame(adjust=0.0)) is None
    assert bfv.drift_sizing_clause(_frame(adjust=-2000.0)) is None
    for state in _REASONS:
        assert bfv.drift_sizing_clause(_frame(state=state, adjust=None)) is None
        assert bfv.drift_sizing_clause(_frame(state=state, adjust=5000.0)) is None


def test_drift_sizing_clause_odd_frames():
    for bad in (None, {}, "ok", {"state": "ok"}, _frame(adjust=float("nan")),
                _frame(adjust=None), _frame(adjust="abc")):
        assert bfv.drift_sizing_clause(bad) is None


def test_drift_sizing_clause_has_no_money_or_markup():
    c = bfv.drift_sizing_clause(_frame(adjust=1500.0))
    assert "$" not in c and not re.search(r"[0-9]", c) and "*" not in c and "<" not in c


# -- cash note (D4) ------------------------------------------------------------------

def test_hand_cash_note_constant_differs_from_the_sync_note():
    assert bfv.HAND_CASH_NOTE == "Entered by hand"
    assert bfv.HAND_CASH_NOTE != bf.BROKER_CASH_NOTE


def test_cash_note_default_blank_over_the_sync_note():
    assert bfv.cash_note_default(bf.BROKER_CASH_NOTE) == ""
    assert bfv.cash_note_default("  " + bf.BROKER_CASH_NOTE + " ") == ""
    assert bfv.cash_note_default("my own note") == "my own note"
    assert bfv.cash_note_default(bfv.HAND_CASH_NOTE) == bfv.HAND_CASH_NOTE
    assert bfv.cash_note_default(None) == ""
    assert bfv.cash_note_default("") == ""


def test_cash_note_to_save():
    assert bfv.cash_note_to_save(None) is None
    assert bfv.cash_note_to_save("") is None
    assert bfv.cash_note_to_save("   ") is None
    assert bfv.cash_note_to_save(bf.BROKER_CASH_NOTE) == bfv.HAND_CASH_NOTE
    assert bfv.cash_note_to_save("  " + bf.BROKER_CASH_NOTE + "  ") == bfv.HAND_CASH_NOTE
    assert bfv.cash_note_to_save("  typed in ") == "typed in"


def test_a_hand_saved_balance_can_never_pair_with_a_snapshot():
    # The saved note must never equal the sync's note (that is what pairs cash
    # with the positions snapshot), whatever the form pre-filled or the user typed.
    for typed in (bf.BROKER_CASH_NOTE, bfv.cash_note_default(bf.BROKER_CASH_NOTE),
                  "x", "", None):
        assert bfv.cash_note_to_save(typed) != bf.BROKER_CASH_NOTE
