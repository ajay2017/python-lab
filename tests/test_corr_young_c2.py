"""CorrYoung C2 (docs/plans/corr-young-holding.md section 7): the view decision and the
plain-text notes for a young holding left out of the correlation reading.

Covers corr_window.view_state / left_out_note / thin_note, risk_window.holdings_phrase,
util.correlation_unchecked_note / correlation_record_withheld_note /
diversification_label_text, pair_add_gate.unchecked_disclosure and
portfolio_health's diversification detail. The _OLD snapshot below was taken from the
functions BEFORE C2, so a legacy call (one or three arguments) is pinned byte for byte.
"""
import numpy as np
import pandas as pd
import pytest

from stock_analyzer import corr_window, pair_add_gate, portfolio, risk_window, util
from stock_analyzer.constants import CORR_MIN_OBS_TRUSTED
from stock_analyzer.portfolio_health import _diversification_score_sub, compute_health_score

FLOOR = CORR_MIN_OBS_TRUSTED

# Outputs of the pre-C2 functions (ascii-escaped; the emoji are 🔍 etc.).
_OLD = {
    'unchk_none': '\U0001f50d Whether every holding was included in this correlation reading is unknown this session \u2014 open \U0001f3e0 Home to check.',
    'unchk_empty': None,
    'unchk_one': "\U0001f50d AAA was left out of this reading \u2014 its price history couldn't be loaded this run, so no correlation involving it could be measured. The classification above describes your other holdings only; this is not a sign AAA is uncorrelated.",
    'unchk_two': "\U0001f50d AAA, BBB were left out of this reading \u2014 their price history couldn't be loaded this run, so no correlation involving them could be measured. The classification above describes your other holdings only; this is not a sign they are uncorrelated.",
    'unchk_eight': "\U0001f50d T00, T01, T02, T03, T04 +3 more were left out of this reading \u2014 their price history couldn't be loaded this run, so no correlation involving them could be measured. The classification above describes your other holdings only; this is not a sign they are uncorrelated.",
    'disc_none_pc': "\U0001f50d (couldn't confirm every holding was included in the correlation add-pause checks this run)",
    'disc_one_both': "\U0001f50d Correlated-pair and new-cluster add-pause checks didn't include AAA \u2014 its price history couldn't be loaded this run, so no correlated pairing involving it could be detected. Add suggestions were checked against your other holdings only; this is not a sign AAA is uncorrelated.",
    'disc_one_pair': "\U0001f50d Correlated-pair add-pause check didn't include AAA \u2014 its price history couldn't be loaded this run, so no correlated pairing involving it could be detected. Add suggestions were checked against your other holdings only; this is not a sign AAA is uncorrelated.",
    'disc_two_cl': "\U0001f50d New-cluster add-pause check didn't include AAA, BBB \u2014 their price history couldn't be loaded this run, so no correlated pairing involving them could be detected. Add suggestions were checked against your other holdings only; this is not a sign they are uncorrelated.",
    'disc_eight': "\U0001f50d Correlated-pair and new-cluster add-pause checks didn't include T00, T01, T02, T03, T04 +3 more \u2014 their price history couldn't be loaded this run, so no correlated pairing involving them could be detected. Add suggestions were checked against your other holdings only; this is not a sign they are uncorrelated.",
    'rec_unk': "S: couldn't confirm every holding was included and couldn't confirm the sample size. Open \U0001f3e0 Home and click \U0001f504 Refresh Signals to recompute it.",
    'rec_thin_nocap': "S: only 5 shared daily returns (needs 20), because the holdings' price histories only overlap on those days.",
    'rec_thin_mis': "S: only 5 shared daily returns (needs 20), because the holdings' price histories only overlap on those days.",
    'rec_both': "S: 2 holdings are missing from the correlation matrix (AAA, BBB) and only 5 shared daily returns (needs 20), because the holdings' price histories only overlap on those days.",
    'rec_miss1': 'S: 1 holding is missing from the correlation matrix (AAA).',
    'rec_miss8': 'S: 8 holdings are missing from the correlation matrix (A, B, C, D, E +3 more).',
    'rec_unkn_samp': "S: couldn't confirm the sample size. Open \U0001f3e0 Home and click \U0001f504 Refresh Signals to recompute it.",
    'lab_a': 'Well Diversified',
    'lab_b': 'Moderate \xb7 1 not checked',
    'lab_c': 'Moderate \xb7 2 not checked',
    'lab_e': 'Moderate',
    'lab_f': 'Moderate',
    'names_1': 'ANT (3.6% of the book: 4 trading days since its first close, 20 needed)',
    'names_2': 'ANT (3.6% of the book: 4 trading days since its first close, 20 needed) and X (size unknown: no price history)',
    'names_3': 'ANT (3.6% of the book: 4 trading days since its first close, 20 needed), X (size unknown: no price history) and Z (size unknown)',
    'names_0': '',
}

_EIGHT = [f"T{i:02d}" for i in range(8)]
_ITEMS = [
    {"ticker": "ANT", "reason": "short_history", "n_returns": 4, "weight_pct": 3.6},
    {"ticker": "X", "reason": "no_prices", "n_returns": 0, "weight_pct": None},
    {"ticker": "Z", "reason": "no_weight", "n_returns": 30, "weight_pct": None},
]
_COV_LO = {"n_obs": 129, "n_obs_max": 129, "min_returns": 20, "left_out": [
    {"ticker": "ANT", "reason": "short_history", "n_returns": 4, "weight_pct": 3.6}]}
_COV_OK = {"n_obs": 125, "n_obs_max": 125, "min_returns": 20, "left_out": []}


def _matrix():
    return pd.DataFrame([[1.0, 0.2], [0.2, 1.0]], index=["A", "B"], columns=["A", "B"])


# -- legacy outputs are byte-identical ---------------------------------------
def test_legacy_correlation_unchecked_note_is_byte_identical():
    for key, arg in {"none": None, "empty": [], "one": ["AAA"], "two": ["bbb", "AAA"],
                     "eight": _EIGHT}.items():
        assert util.correlation_unchecked_note(arg) == _OLD["unchk_" + key]
        # a coverage with nothing left out / no short names changes nothing either
        assert util.correlation_unchecked_note(arg, _COV_OK) == _OLD["unchk_" + key]
        assert util.correlation_unchecked_note(arg, None) == _OLD["unchk_" + key]


def test_legacy_unchecked_disclosure_is_byte_identical():
    cases = {"none_pc": (None, True, False), "one_both": (["AAA"], True, True),
             "one_pair": (["AAA"], True, False), "two_cl": (["AAA", "BBB"], False, True),
             "eight": (_EIGHT, True, True)}
    for key, (u, a, b) in cases.items():
        assert pair_add_gate.unchecked_disclosure(u, a, b) == _OLD["disc_" + key]
        assert pair_add_gate.unchecked_disclosure(u, a, b, _COV_OK) == _OLD["disc_" + key]


def test_legacy_record_withheld_note_is_byte_identical():
    rc = {"n_obs": 125}
    cases = {"unk": (None, None), "thin_nocap": ([], {"n_obs": 5}),
             "thin_mis": ([], {"n_obs": 5, "shortest_ticker": "AAA", "shortest_len": 125}),
             "both": (["AAA", "BBB"], {"n_obs": 5}), "miss1": (["AAA"], rc),
             "miss8": (list("ABCDEFGH"), rc), "unkn_samp": ([], {"n_obs": None})}
    for key, (u, c) in cases.items():
        assert util.correlation_record_withheld_note(u, c, subject="S") == _OLD["rec_" + key]


def test_legacy_diversification_label_text_is_byte_identical():
    cases = {"a": ("Well Diversified", [], None), "b": ("Moderate", ["A"], {"n_obs": 125}),
             "c": ("Moderate", ["A", "B"], {"n_obs": 125}), "e": ("Moderate", [], {"n_obs": 125}),
             "f": ("Moderate", None, None)}
    for key, (label, unchecked, cov) in cases.items():
        assert util.diversification_label_text(label, unchecked, cov) == _OLD["lab_" + key]


# -- holdings_phrase ---------------------------------------------------------
def test_holdings_phrase_reproduces_the_old_names_text_byte_for_byte():
    assert risk_window.holdings_phrase(_ITEMS[:1], need=20) == _OLD["names_1"]
    assert risk_window.holdings_phrase(_ITEMS[:2], need=20) == _OLD["names_2"]
    assert risk_window.holdings_phrase(_ITEMS, need=20) == _OLD["names_3"]
    assert risk_window.holdings_phrase([], need=20) == _OLD["names_0"]


def test_holdings_phrase_without_size_omits_the_share():
    one = risk_window.holdings_phrase(_ITEMS[:1], need=20, with_size=False)
    assert one == "ANT (4 trading days since its first close, 20 needed)"
    two = risk_window.holdings_phrase(_ITEMS[:2], need=20, with_size=False)
    assert two == one + " and X (no price history)"
    assert "size unknown" not in risk_window.holdings_phrase(_ITEMS, need=20, with_size=False)
    one_day = risk_window.holdings_phrase(
        [{"ticker": "N", "reason": "short_history", "n_returns": 1}], need=20, with_size=False)
    assert one_day == "N (1 trading day since its first close, 20 needed)"


def test_weight_pct_is_public_and_the_private_alias_stays():
    assert risk_window.weight_pct("3.5") == 3.5
    assert risk_window.weight_pct(float("nan")) is None
    assert risk_window.weight_pct(None) is None
    assert risk_window._weight_pct is risk_window.weight_pct


# -- left_out_note / thin_note -----------------------------------------------
def test_left_out_note_sample_book_text():
    assert corr_window.left_out_note(_COV_LO) == (
        "Correlations measured without ANT (3.6% of the book: 4 trading days since "
        "its first close, 20 needed); its pairs are not shown and it is not part of the score.")


def test_left_out_note_plural_and_no_prices_wording():
    cov = {"n_obs": 125, "min_returns": 20, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4, "weight_pct": 3.6},
        {"ticker": "XYZ", "reason": "no_prices", "n_returns": 0, "weight_pct": 1.2}]}
    note = corr_window.left_out_note(cov)
    assert ("ANT (3.6% of the book: 4 trading days since its first close, 20 needed) and "
            "XYZ (1.2% of the book: no price history)") in note
    assert note.endswith("their pairs are not shown and they are not part of the score.")


def test_left_out_note_size_only_when_every_item_has_a_weight():
    cov = {"n_obs": 125, "min_returns": 20, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4, "weight_pct": 3.6},
        {"ticker": "XYZ", "reason": "no_prices", "n_returns": 0, "weight_pct": None}]}
    assert "of the book" not in corr_window.left_out_note(cov)
    # no weights passed at all (corr_coverage called without `weights`)
    bare = {"n_obs": 125, "min_returns": 20, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4}]}
    assert corr_window.left_out_note(bare) == (
        "Correlations measured without ANT (4 trading days since its first close, "
        "20 needed); its pairs are not shown and it is not part of the score.")


def test_left_out_note_none_cases_and_pre_c1_coverage():
    assert corr_window.left_out_note(None) is None
    assert corr_window.left_out_note("x") is None
    assert corr_window.left_out_note({"n_obs": 125}) is None            # pre-C1: no left_out
    assert corr_window.left_out_note({"n_obs": 125, "left_out": []}) is None
    assert corr_window.left_out_note({"n_obs": 125, "left_out": None}) is None


def test_left_out_note_uses_the_floor_when_min_returns_is_missing_or_bad():
    item = [{"ticker": "ANT", "reason": "short_history", "n_returns": 4}]
    for cov in ({"left_out": item}, {"left_out": item, "min_returns": None},
                {"left_out": item, "min_returns": "x"}):
        assert f"{FLOOR} needed" in corr_window.left_out_note(cov)


def test_notes_are_plain_text_and_never_say_these_move_together():
    notes = [
        corr_window.left_out_note(_COV_LO),
        corr_window.thin_note({"n_obs": 4, "min_returns": 20}),
        corr_window.thin_note(None),
        corr_window.thin_note({"n_obs": None}),
        util.correlation_unchecked_note(["ANT"], _COV_LO),
        util.correlation_unchecked_note(None, _COV_LO),
        util.correlation_record_withheld_note(["ANT"], _COV_LO, subject="S"),
        pair_add_gate.unchecked_disclosure(["ANT"], True, True, _COV_LO),
    ]
    for n in notes:
        assert "**" not in n and "$" not in n
        assert "these move together" not in n and "they move together" not in n
        assert "toward zero" not in n


def test_thin_note_text_and_wording():
    note = corr_window.thin_note({"n_obs": 4, "min_returns": 20})
    assert note == (
        "⛔ Correlations can't be measured on this sample: only 4 shared daily returns "
        "(20 needed). A sample this small is unreliable in both directions: unrelated "
        "stocks can read as moving together and a real pair can read as independent, so "
        "no pairs, clusters or diversification score are shown. Adds on any pair that "
        "crossed the line stay paused until it can be measured.")
    assert "unreliable in both directions" in note
    assert "toward zero" not in note and "these move together" not in note
    # the floor is the coverage's own, else the constant
    assert f"({FLOOR} needed)" in corr_window.thin_note({"n_obs": 4})
    assert "too few shared daily returns" in corr_window.thin_note({"n_obs": None})


# -- view_state --------------------------------------------------------------
def test_view_state_thin_sample_is_withheld():
    out = corr_window.view_state(_matrix(), {"n_obs": 4, "min_returns": 20, "left_out": []}, [])
    assert out["state"] == "withheld"
    assert out["notes"] == [corr_window.thin_note({"n_obs": 4, "min_returns": 20})]
    assert out["left_out"] == []


def test_view_state_all_nan_zero_obs_is_withheld():
    nan = pd.DataFrame(float("nan"), index=["A", "B"], columns=["A", "B"])
    out = corr_window.view_state(nan, {"n_obs": 0, "min_returns": 20, "left_out": []}, [])
    assert out["state"] == "withheld" and "only 0 shared daily returns" in out["notes"][0]


def test_view_state_fewer_than_two_counted_is_withheld_and_names_the_holdings():
    cov = {"n_obs": 0, "n_obs_max": 0, "min_returns": 20, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4, "weight_pct": 3.6}]}
    out = corr_window.view_state(pd.DataFrame(), cov, ["ANT"])
    assert out["state"] == "withheld"
    assert out["notes"][0].startswith("⛔ Correlations can't be measured")
    assert out["notes"][1] == corr_window.left_out_note(cov)
    assert [x["ticker"] for x in out["left_out"]] == ["ANT"]
    # an empty matrix with left_out is withheld even when n_obs reads trusted
    odd = dict(cov, n_obs=125)
    assert corr_window.view_state(None, odd, ["ANT"])["state"] == "withheld"


def test_view_state_none_when_nothing_to_correlate_and_nothing_left_out():
    assert corr_window.view_state(pd.DataFrame(), None, None) == {
        "state": "none", "notes": [], "left_out": [], "unchecked_rest": None}
    assert corr_window.view_state(None, None, [])["state"] == "none"


def test_view_state_unknown_when_coverage_is_not_a_dict_or_unreadable():
    for cov in (None, "x", 5, {"n_obs": None}, {}, {"n_obs": True}, {"n_obs": 25.0}):
        out = corr_window.view_state(_matrix(), cov, [])
        assert out == {"state": "unknown", "notes": [], "left_out": [], "unchecked_rest": []}, cov


def test_view_state_trusted_with_ant_left_out_is_partial_with_the_note():
    out = corr_window.view_state(_matrix(), _COV_LO, ["ANT"])
    assert out["state"] == "partial"
    assert out["notes"] == [corr_window.left_out_note(_COV_LO)]
    assert [x["ticker"] for x in out["left_out"]] == ["ANT"]


def test_view_state_unpriced_name_only_in_unchecked_is_partial_without_a_note():
    out = corr_window.view_state(_matrix(), _COV_OK, ["XYZ"])
    assert out == {"state": "partial", "notes": [], "left_out": [], "unchecked_rest": ["XYZ"]}


def test_view_state_trusted_full_and_pre_c1_coverage():
    assert corr_window.view_state(_matrix(), _COV_OK, [])["state"] == "full"
    pre = {"n_obs": 125, "n_tickers": 2, "shortest_ticker": "A", "shortest_len": 126,
           "longest_len": 126, "lengths": {"A": 126, "B": 126}}      # no left_out / n_obs_max
    assert corr_window.view_state(_matrix(), pre, []) == {
        "state": "full", "notes": [], "left_out": [], "unchecked_rest": []}
    assert corr_window.view_state(_matrix(), pre, None)["state"] == "full"


def test_view_state_exactly_at_the_floor_is_trusted():
    cov = {"n_obs": FLOOR, "min_returns": FLOOR, "left_out": []}
    assert corr_window.view_state(_matrix(), cov, [])["state"] == "full"
    cov = {"n_obs": FLOOR - 1, "min_returns": FLOOR, "left_out": []}
    assert corr_window.view_state(_matrix(), cov, [])["state"] == "withheld"


def test_view_state_never_raises_on_bad_input():
    class Boom:
        def get(self, *_a, **_k):
            raise RuntimeError("boom")
    for args in ((object(), object(), object()), (_matrix(), Boom(), []),
                 (_matrix(), {"n_obs": 125, "left_out": [None, 3, {"x": 1}]}, [])):
        out = corr_window.view_state(*args)
        assert out["state"] in {"unknown", "full", "partial", "withheld", "none"}
        assert isinstance(out["notes"], list)


def test_view_state_uses_the_one_sample_rule(monkeypatch):
    monkeypatch.setattr(portfolio, "CORR_MIN_OBS_TRUSTED", 200)
    assert corr_window.view_state(_matrix(), _COV_OK, [])["state"] == "withheld"


# -- util / pair_add_gate reasons from the coverage --------------------------
def test_unchecked_note_short_history_reads_its_own_reason():
    text = util.correlation_unchecked_note(["ANT"], _COV_LO)
    assert text == (
        "🔍 ANT was left out of this reading — it has only 4 daily returns of price "
        "history (20 needed), so no correlation involving it could be measured. The "
        "classification above describes your other holdings only; this is not a sign "
        "it is uncorrelated.")
    assert "couldn't be loaded" not in text
    # several young names keep their names (no pronoun)
    cov = {"n_obs": 125, "min_returns": 20, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4},
        {"ticker": "NEWB", "reason": "short_history", "n_returns": 7}]}
    two = util.correlation_unchecked_note(["ANT", "NEWB"], cov)
    assert "ANT and NEWB have only 4 and 7 daily returns of price history (20 needed)" in two
    assert "this is not a sign they are uncorrelated" in two


def test_unchecked_note_mixed_list_splits_the_reasons():
    cov = {"n_obs": 125, "min_returns": 20, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4},
        {"ticker": "NEWB", "reason": "short_history", "n_returns": 7},
        {"ticker": "XYZ", "reason": "no_prices", "n_returns": 0}]}
    text = util.correlation_unchecked_note(["XYZ", "ant", "NEWB"], cov)
    assert "ANT and NEWB have only 4 and 7 daily returns of price history (20 needed)" in text
    assert "price history couldn't be loaded this run for XYZ" in text
    assert text.startswith("🔍 ANT, NEWB, XYZ were left out of this reading")
    assert "so no correlation involving them could be measured" in text
    # a name that is only unpriced keeps the old wording exactly
    assert util.correlation_unchecked_note(["XYZ"], cov) == util.correlation_unchecked_note(["XYZ"])


# Fewer than 2 counted: no matrix, n_obs 0, and the producer published no unchecked list.
_COV_NONE_COUNTED = {"n_obs": 0, "n_obs_max": 0, "min_returns": 20, "left_out": [
    {"ticker": "ANT", "reason": "short_history", "n_returns": 4, "weight_pct": 3.6}]}


def test_unchecked_note_none_with_left_out_names_them_and_keeps_the_unconfirmed_fact():
    text = util.correlation_unchecked_note(None, _COV_NONE_COUNTED)
    assert text == (
        "🔍 ANT was left out of this reading (it has only 4 daily returns of price "
        "history, 20 needed); whether every other holding was included couldn't be "
        "confirmed this session.")
    assert "couldn't be confirmed" in text
    assert "other holdings only" not in text and "classification" not in text
    assert "Open 🏠 Home" not in text and "unknown this session" not in text
    # plural, mixed reasons
    cov = {"n_obs": 0, "min_returns": 20, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4},
        {"ticker": "XYZ", "reason": "no_prices", "n_returns": 0}]}
    two = util.correlation_unchecked_note(None, cov)
    assert two == (
        "🔍 ANT, XYZ were left out of this reading (ANT has only 4 daily returns of "
        "price history and XYZ has no price history, 20 needed); whether every other "
        "holding was included couldn't be confirmed this session.")
    # only an unpriced holding: no floor quoted
    only = util.correlation_unchecked_note(
        None, {"n_obs": 0, "left_out": [{"ticker": "XYZ", "reason": "no_prices", "n_returns": 0}]})
    assert "(it has no price history)" in only and "needed" not in only
    # no left_out in the coverage keeps the old unknown line
    assert util.correlation_unchecked_note(None, _COV_OK) == _OLD["unchk_none"]
    # a published empty list still says nothing
    assert util.correlation_unchecked_note([], _COV_LO) is None


def test_record_withheld_note_splits_young_names_from_missing_ones():
    note = util.correlation_record_withheld_note(["ANT"], dict(_COV_LO, n_obs=3), subject="S")
    assert note == ("S: ANT has only 4 daily returns (20 needed) and only 3 shared daily "
                    "returns (needs 20), because the holdings' price histories only overlap "
                    "on those days.")
    cov = {"n_obs": 125, "min_returns": 20, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4},
        {"ticker": "NEWB", "reason": "short_history", "n_returns": 7}]}
    two = util.correlation_record_withheld_note(["ANT", "NEWB", "XYZ"], cov, subject="S")
    assert two == ("S: ANT and NEWB have only 4 and 7 daily returns (20 needed) and "
                   "1 holding is missing from the correlation matrix (XYZ).")
    one_return = {"n_obs": 125, "left_out": [
        {"ticker": "N", "reason": "short_history", "n_returns": 1}]}
    assert "N has only 1 daily return (20 needed)" in util.correlation_record_withheld_note(
        ["N"], one_return, subject="S")


def test_record_withheld_note_never_says_capped_and_stays_none_iff_verified():
    covs = [None, {"n_obs": 5, "shortest_ticker": "NEWCO", "shortest_len": 6}, _COV_LO, _COV_OK,
            {"n_obs": 3, "left_out": _COV_LO["left_out"]}]
    for u in (None, [], ["ANT"], ["XYZ"], ["ANT", "XYZ"]):
        for c in covs:
            note = util.correlation_record_withheld_note(u, c, subject="S")
            assert (note is None) == portfolio.correlation_claim_verified(u, c), (u, c)
            if note is not None:
                assert "capped by" not in note and "$" not in note and "**" not in note


def test_unchecked_disclosure_short_history_and_mixed():
    text = pair_add_gate.unchecked_disclosure(["ANT"], True, True, _COV_LO)
    assert text == (
        "🔍 Correlated-pair and new-cluster add-pause checks didn't include ANT — it has "
        "only 4 daily returns of price history (20 needed), so its pairs couldn't be "
        "checked. Add suggestions were checked against your other holdings only; this is "
        "not a sign it is uncorrelated.")
    assert "couldn't be loaded" not in text
    cov = {"n_obs": 125, "min_returns": 20, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4},
        {"ticker": "NEWB", "reason": "short_history", "n_returns": 7}]}
    two = pair_add_gate.unchecked_disclosure(["ANT", "NEWB"], True, False, cov)
    assert ("ANT and NEWB have only 4 and 7 daily returns of price history (20 needed), "
            "so their pairs couldn't be checked") in two
    mixed = pair_add_gate.unchecked_disclosure(["ANT", "XYZ"], False, True, cov)
    assert ("ANT has only 4 daily returns of price history (20 needed) and price history "
            "couldn't be loaded this run for XYZ, so no correlated pairing involving them "
            "could be detected") in mixed
    # a name that is only unpriced keeps the old sentence exactly
    assert pair_add_gate.unchecked_disclosure(["XYZ"], True, True, cov) == \
        pair_add_gate.unchecked_disclosure(["XYZ"], True, True)


def test_unchecked_disclosure_none_with_left_out_names_them_and_keeps_the_unconfirmed_fact():
    text = pair_add_gate.unchecked_disclosure(None, True, False, _COV_NONE_COUNTED)
    assert text == (
        "🔍 The correlation add-pause checks didn't include ANT (it has only 4 daily "
        "returns of price history, 20 needed); couldn't confirm every other holding was "
        "included this run.")
    assert "couldn't confirm" in text and "other holdings only" not in text
    cov = {"n_obs": 0, "min_returns": 20, "left_out": [
        {"ticker": "ANT", "reason": "short_history", "n_returns": 4},
        {"ticker": "XYZ", "reason": "no_prices", "n_returns": 0}]}
    assert pair_add_gate.unchecked_disclosure(None, False, True, cov) == (
        "🔍 The correlation add-pause checks didn't include ANT, XYZ (ANT has only 4 daily "
        "returns of price history and XYZ has no price history, 20 needed); couldn't "
        "confirm every other holding was included this run.")
    assert pair_add_gate.unchecked_disclosure(None, True, False, _COV_OK) == _OLD["disc_none_pc"]
    # neither gate checked still says nothing; a clean [] still says nothing
    assert pair_add_gate.unchecked_disclosure(None, False, False, _COV_NONE_COUNTED) is None
    assert pair_add_gate.unchecked_disclosure([], True, True, _COV_LO) is None


# -- diversification label ---------------------------------------------------
def test_diversification_label_thin_reads_unavailable_regardless():
    for label in ("Moderate", "Well Diversified", "Unavailable", None):
        assert util.diversification_label_text(label, ["ANT"], {"n_obs": 4}) == \
            "Unavailable · sample too thin"
    # a trusted sample with a young name left out keeps its label and the count
    assert util.diversification_label_text("Moderate", ["ANT"], _COV_LO) == "Moderate · 1 not checked"


# -- portfolio_health --------------------------------------------------------
def test_health_sub_score_carries_the_left_out_note_when_trusted():
    sub = _diversification_score_sub(50.0, 0.4, _COV_LO, ["ANT"])
    assert sub["score"] == 60.0
    assert sub["detail"]["left_out_note"] == corr_window.left_out_note(_COV_LO)


def test_health_sub_score_has_no_note_key_when_nothing_was_left_out():
    sub = _diversification_score_sub(50.0, 0.4, _COV_OK, [])
    assert "left_out_note" not in sub["detail"]
    pre = _diversification_score_sub(50.0, 0.4, {"n_obs": 125}, [])
    assert "left_out_note" not in pre["detail"] and pre["score"] == 60.0


def test_health_sub_score_withheld_paths_unchanged():
    thin = _diversification_score_sub(50.0, 0.4, dict(_COV_LO, n_obs=3), ["ANT"])
    assert thin["score"] is None and "left_out_note" not in thin["detail"]
    assert "too thin" in thin["detail"]["withheld_reason"]
    unk = _diversification_score_sub(50.0, 0.4, None, [])
    assert unk["score"] is None and "left_out_note" not in unk["detail"]


def test_compute_health_score_passes_the_note_through():
    port = pd.DataFrame({"Ticker": ["A", "B"], "Weight (%)": [50.0, 50.0], "Sector": ["T", "H"]})
    out = compute_health_score(port, 50.0, 0.4, None, None, None, _COV_LO, ["ANT"])
    assert out["sub_scores"]["diversification"]["detail"]["left_out_note"].startswith(
        "Correlations measured without ANT")


# -- end to end on a real frame: the sample book of the spec -----------------
def _sample_book():
    idx = pd.bdate_range(end="2026-11-20", periods=130)
    rng = np.random.default_rng(1)
    hd = {f"L{i}": {"df": pd.DataFrame(
        {"Close": 100 * np.cumprod(1 + rng.normal(0, 0.01, 130))}, index=idx)} for i in range(10)}
    hd["ANT"] = {"df": pd.DataFrame({"Close": [10, 10.2, 10.1, 10.4, 10.3]}, index=idx[-5:])}
    return hd


def test_sample_book_end_to_end():
    hd = _sample_book()
    weights = {"ANT": 3.6, **{f"L{i}": 9.64 for i in range(10)}}
    cov = corr_window.corr_coverage(hd, weights=weights)
    df = corr_window.corr_matrix(hd)
    assert cov["n_obs"] == 129 and "ANT" not in df.columns
    view = corr_window.view_state(df, cov, ["ANT"])
    assert view["state"] == "partial"
    assert view["notes"] == [
        "Correlations measured without ANT (3.6% of the book: 4 trading days since its "
        "first close, 20 needed); its pairs are not shown and it is not part of the score."]
    assert "it has only 4 daily returns of price history (20 needed)" in \
        util.correlation_unchecked_note(["ANT"], cov)
    # the same book with only ANT priced beside one long name: fewer than 2 counted
    two = {"L0": hd["L0"], "ANT": hd["ANT"]}
    cov2 = corr_window.corr_coverage(two, weights={"ANT": 3.6, "L0": 96.4})
    assert cov2["n_obs"] == 0 and cov2["left_out"][0]["ticker"] == "ANT"
    v2 = corr_window.view_state(corr_window.corr_matrix(two), cov2, ["ANT"])
    assert v2["state"] == "withheld" and len(v2["notes"]) == 2


# -- unchecked_rest ----------------------------------------------------------
def test_view_state_unchecked_rest_excludes_left_out_names():
    out = corr_window.view_state(_matrix(), _COV_LO, ["ANT", "xyz", "XYZ", "NEWB", None, " "])
    assert out["unchecked_rest"] == ["XYZ", "NEWB"]
    only_left_out = corr_window.view_state(_matrix(), _COV_LO, ["ANT"])
    assert only_left_out["unchecked_rest"] == []
    assert only_left_out["state"] == "partial"


def test_view_state_unchecked_rest_is_none_when_unchecked_is_none():
    assert corr_window.view_state(_matrix(), _COV_LO, None)["unchecked_rest"] is None
    assert corr_window.view_state(_matrix(), _COV_OK, None) == {
        "state": "full", "notes": [], "left_out": [], "unchecked_rest": None}
    assert corr_window.view_state(object(), object(), object())["unchecked_rest"] is None


def test_view_state_unchecked_rest_present_in_every_state():
    thin = corr_window.view_state(_matrix(), {"n_obs": 4, "left_out": []}, ["XYZ"])
    assert thin["state"] == "withheld" and thin["unchecked_rest"] == ["XYZ"]
    withheld = corr_window.view_state(pd.DataFrame(), _COV_NONE_COUNTED, ["ANT", "XYZ"])
    assert withheld["state"] == "withheld" and withheld["unchecked_rest"] == ["XYZ"]
    assert corr_window.view_state(_matrix(), None, [])["unchecked_rest"] == []


def test_unconfirmed_reasons_wording():
    one = [{"ticker": "ANT", "reason": "short_history", "n_returns": 1}]
    assert corr_window.unconfirmed_reasons(one, need=20) == \
        "it has only 1 daily return of price history, 20 needed"
    assert corr_window.unconfirmed_reasons(
        [{"ticker": "XYZ", "reason": "no_prices", "n_returns": 0}], need=20) == \
        "it has no price history"
