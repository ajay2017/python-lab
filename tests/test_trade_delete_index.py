"""DelIdx: the Trade History delete must map the ticked editor rows back to
trade ids by index LABEL, never by position.

The editor frame is `trades_df.copy()`, so its index labels are trades_df's.
After the delete path whose journal re-read failed, session `trades_df` was a
filtered frame with gaps in its index; `.iloc[labels]` then picked other trades
(or raised IndexError). app.py has no tests that import it, so the wiring is
pinned at the source; the pandas behaviour is shown directly.
"""
from pathlib import Path

import pandas as pd
import pytest

_APP = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


def _gappy_trades():
    full = pd.DataFrame({"id": [11, 12, 13, 14, 15],
                         "ticker": ["A", "B", "C", "D", "E"]})
    # What the unreadable-re-read path used to leave behind: ids 12 and 13 gone.
    return full[~full["id"].isin([12, 13])]          # index [0, 3, 4]


def test_iloc_on_labels_picks_the_wrong_trade():
    tdf = _gappy_trades()
    ticked = tdf.copy().loc[[3]]                       # the user ticks trade id 14
    with pytest.raises(IndexError):
        tdf.iloc[ticked.index]["id"].tolist()          # position 3 doesn't exist
    ticked0 = tdf.copy().loc[[0, 3]]
    with pytest.raises(IndexError):
        tdf.iloc[ticked0.index]["id"].tolist()


def test_iloc_on_labels_quietly_deletes_another_trade():
    # The dangerous case: no error, wrong id. Trade 11 gone -> index [1, 2, 3, 4];
    # the user ticks label 1 (id 12) and .iloc[[1]] returns id 13.
    full = pd.DataFrame({"id": [11, 12, 13, 14, 15]})
    tdf = full[full["id"] != 11]
    ticked = tdf.copy().loc[[1]]
    assert tdf.iloc[ticked.index]["id"].tolist() == [13]   # the old bug
    assert tdf.loc[ticked.index, "id"].tolist() == [12]    # the fix


def test_loc_on_labels_picks_the_ticked_trade():
    tdf = _gappy_trades()
    ticked = tdf.copy().loc[[3, 4]]
    assert tdf.loc[ticked.index, "id"].tolist() == [14, 15]


def test_app_delete_maps_selection_by_label():
    assert "trades_df.iloc[rows_to_delete.index]" not in _APP
    assert 'trades_df.loc[rows_to_delete.index, "id"]' in _APP
    assert "trades_df.loc[rows_to_delete.index].to_dict(\"records\")" in _APP


def test_unreadable_reread_path_resets_the_index():
    assert '_old_tdf[~_old_tdf["id"].isin(_ok_ids)].reset_index(drop=True)' in _APP
