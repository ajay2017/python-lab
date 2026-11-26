"""CorrYoung C4 review round: corr_window.thin_note names "fewer than 2 holdings
can be paired" instead of "only 0 shared daily returns" when nothing could pair."""
from __future__ import annotations

import pandas as pd

from stock_analyzer import corr_window as cw


def _cov(n_obs, n_tickers, left_out):
    return {"n_tickers": n_tickers, "n_obs": n_obs, "n_obs_max": n_obs,
            "min_returns": 20, "left_out": left_out}


_ANT = {"ticker": "ANT", "reason": "short_history", "n_returns": 4}
_XYZ = {"ticker": "XYZ", "reason": "short_history", "n_returns": 6}


def test_fewer_than_two_counted_reads_as_nothing_to_pair():
    # 2 holdings with prices, both young: 0 counted
    note = cw.thin_note(_cov(0, 2, [_ANT, _XYZ]))
    assert "fewer than 2 holdings have enough price history to pair" in note
    assert "only 0" not in note and "move together" not in note
    assert "no pairs, clusters or diversification score are shown" in note
    # 3 priced, 2 young: 1 counted
    assert "fewer than 2 holdings" in cw.thin_note(_cov(0, 3, [_ANT, _XYZ]))


def test_two_counted_with_no_shared_days_keeps_the_overlap_wording():
    note = cw.thin_note(_cov(0, 4, [_ANT, _XYZ]))   # 2 counted
    assert "only 0 shared daily returns (20 needed)" in note
    assert "fewer than 2 holdings" not in note


def test_nothing_left_out_or_unreadable_count_keeps_the_old_wording():
    assert "only 0 shared daily returns" in cw.thin_note(_cov(0, 2, []))
    bad = _cov(0, None, [_ANT])
    assert "only 0 shared daily returns" in cw.thin_note(bad)
    assert "fewer than 2 holdings" not in cw.thin_note(_cov(4, 2, [_ANT, _XYZ]))
    assert "too few shared daily returns" in cw.thin_note({"n_obs": "x"})
    assert "too few shared daily returns" in cw.thin_note(None)


def test_view_state_withheld_note_uses_it_for_an_empty_matrix():
    v = cw.view_state(pd.DataFrame(), _cov(0, 2, [_ANT, _XYZ]), [])
    assert v["state"] == "withheld"
    assert v["notes"][0].startswith("⛔ Correlations can't be measured: fewer than 2 holdings")
