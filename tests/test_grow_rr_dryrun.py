"""G-32 C1 dry-run script: the pure summarize() and its import hygiene."""
import importlib.util
import os
from pathlib import Path

import pytest

from stock_analyzer.constants import RR_ENTRY_MIN

pytestmark = pytest.mark.fast

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "grow_rr_dryrun.py"


def _load():
    spec = importlib.util.spec_from_file_location("grow_rr_dryrun_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(lane="pick", date="2026-10-20", cls="pass", rr=2.5, rr_old=None,
         base_source="analyst", cls_lane=None, skip=None):
    return {"lane": lane, "date": date, "cls": cls, "rr": rr, "rr_old": rr_old,
            "base_source": base_source, "cls_lane": cls_lane, "skip": skip}


def test_import_does_not_drop_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    _load()
    assert os.environ.get("ANTHROPIC_API_KEY") == "dummy"


def test_lane_split_and_class_counts():
    m = _load()
    rows = [
        _row("pick", cls="pass"), _row("pick", cls="below", rr=1.2),
        _row("add", cls="no_upside", rr=-0.5), _row("add", cls="unknown", rr=None),
        _row("overflow", cls="pass"),
    ]
    s = m.summarize(rows)["lanes"]
    assert (s["pick"]["shown"], s["pick"]["would_hold"]) == (2, 1)
    assert s["pick"]["by_class"] == {"pass": 1, "below": 1, "no_upside": 0, "unknown": 0}
    assert (s["add"]["shown"], s["add"]["would_hold"]) == (2, 2)
    assert s["add"]["by_class"]["no_upside"] == 1 and s["add"]["by_class"]["unknown"] == 1
    assert s["overflow"]["would_fill_slot"] == 1


def test_base_source_split():
    m = _load()
    rows = [_row(cls="below", rr=1.0, base_source="technical"),
            _row(cls="below", rr=1.0, base_source="technical"),
            _row(cls="pass", base_source="analyst"),
            _row(cls="unknown", rr=None, base_source=None)]
    p = m.summarize(rows)["lanes"]["pick"]
    assert p["all_by_base_source"] == {"technical": 2, "analyst": 1, "none": 1}
    assert p["held_by_base_source"] == {"technical": 2, "none": 1}


def test_near_miss_band_edges():
    m = _load()
    lo, hi = m._NEAR_MISS_FLOOR, RR_ENTRY_MIN
    rows = [_row(cls="below", rr=lo), _row(cls="below", rr=lo - 0.01),
            _row(cls="below", rr=1.999999),
            _row(cls="pass", rr=hi)]
    assert lo < hi
    assert m.summarize(rows)["lanes"]["pick"]["near_misses"] == 2


def test_rounding_disagreement_count():
    m = _load()
    rows = [_row(cls="below", rr=1.96, rr_old=2.0),   # display says 2.0, gate says no
            _row(cls="below", rr=1.5, rr_old=1.5),
            _row(cls="pass", rr=2.0, rr_old=2.0)]
    assert m.summarize(rows)["lanes"]["pick"]["rounding_disagree"] == 1


def test_basis_flips():
    m = _load()
    rows = [_row(cls="pass", cls_lane="below"), _row(cls="pass", cls_lane="pass"),
            _row(cls="below", rr=1.0, cls_lane=None)]
    p = m.summarize(rows)["lanes"]["pick"]
    assert (p["basis_flips"], p["basis_compared"]) == (1, 2)


def test_zero_pass_days_only_count_days_with_rows_in_lane():
    m = _load()
    rows = [
        _row("pick", date="d1", cls="below", rr=1.0), _row("pick", date="d1", cls="pass"),
        _row("pick", date="d2", cls="below", rr=1.0),
        _row("add", date="d2", cls="pass"),
        _row("add", date="d3", cls="unknown", rr=None),
    ]
    s = m.summarize(rows)["lanes"]
    assert (s["pick"]["days"], s["pick"]["zero_pass_days"]) == (2, 1)
    assert (s["add"]["days"], s["add"]["zero_pass_days"]) == (2, 1)


def test_skips_counted_not_measured():
    m = _load()
    out = m.summarize([_row(skip="no_price_at_surface"), _row(skip="short_history"),
                       _row(skip="short_history"), _row(cls="pass")])
    assert out["skipped"] == 3
    assert out["skips"] == {"no_price_at_surface": 1, "short_history": 2}
    assert out["lanes"]["pick"]["shown"] == 1


def test_empty_rows():
    m = _load()
    out = m.summarize([])
    assert out["skipped"] == 0 and out["lanes"]["pick"]["shown"] == 0


def test_movers_split_counts_only_known_flags():
    m = _load()
    rows = [dict(_row(cls="pass"), is_mover=True), dict(_row(cls="below", rr=1.0), is_mover=True),
            dict(_row(cls="pass"), is_mover=False), _row(cls="pass"),   # flag unknown
            dict(_row("add", cls="pass"), is_mover=True)]               # non-pick lane ignored
    mv = m.summarize(rows)["lanes"]["pick"]["movers"]
    assert mv["mover"] == {"shown": 2, "would_hold": 1}
    assert mv["curated"] == {"shown": 1, "would_hold": 0}


def test_target_basis_and_asset_type_splits():
    m = _load()
    rows = [dict(_row(cls="pass"), target_basis="snapshot", asset_type="stock"),
            dict(_row(cls="below", rr=1.0), target_basis="approx_today", asset_type="etf"),
            _row(cls="pass")]   # neither field: not counted in the splits
    p = m.summarize(rows)["lanes"]["pick"]
    assert p["by_target_basis"] == {"snapshot": {"shown": 1, "would_hold": 0},
                                    "approx_today": {"shown": 1, "would_hold": 1}}
    assert p["by_asset_type"] == {"stock": {"shown": 1, "would_hold": 0},
                                  "etf": {"shown": 1, "would_hold": 1}}


def test_zero_pass_pick_and_overflow_days():
    m = _load()
    rows = [
        _row("pick", date="d1", cls="below", rr=1.0),                       # no overflow row: counts
        _row("pick", date="d2", cls="below", rr=1.0), _row("overflow", date="d2", cls="pass"),
        _row("pick", date="d3", cls="pass"),
        _row("overflow", date="d4", cls="below", rr=1.0),                   # no pick row: not a pick day
    ]
    out = m.summarize(rows)
    assert out["pick_days"] == 3
    assert out["zero_pass_pick_and_overflow_days"] == 1


# -- per-row reconstruction (_row_result) ------------------------------------

def _ohlc():
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range("2025-09-01", "2026-10-30")
    n = len(idx)
    close = 100 + np.arange(n) * 0.1
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                         "Close": close, "Volume": 1000}, index=idx)


def test_row_result_windows(monkeypatch):
    import pandas as pd
    from stock_analyzer import risk, targets
    m = _load()
    seen = {}
    real_atr = risk._atr_value

    def fake_targets(df, fin, price, chosen):
        seen["targets_df"], seen["fin"], seen["chosen"] = df, fin, chosen
        return {"base": price * 1.2, "base_source": "analyst"}

    def spy_atr(df, *a, **k):
        seen["atr_df"] = df
        return real_atr(df, *a, **k)

    monkeypatch.setattr(targets, "compute_price_targets", fake_targets)
    monkeypatch.setattr(risk, "_atr_value", spy_atr)
    df = _ohlc()
    rec = {"rec_type": "new_pick", "rec_date": "2026-10-15", "ticker": "AAA",
           "price_at_surface": 120.0, "rec_stop": 115.0}
    snap = {"target_mean": 150.0, "num_analysts": 10}
    r = m._row_result(rec, df, {}, snap)
    assert r["skip"] is None and r["target_basis"] == "snapshot"
    floor = pd.Timestamp("2026-10-15") - pd.DateOffset(months=6)
    for key in ("targets_df", "atr_df"):
        w = seen[key]
        assert w.index.max() < pd.Timestamp("2026-10-15")      # rec_date bar and later excluded
        assert w.index.min() >= floor                          # 6-month window
        assert w.index.min() < floor + pd.Timedelta(days=5)
    hi52 = seen["fin"]["52_week_high"]
    long_df = df[df.index < pd.Timestamp("2026-10-15")].tail(252)
    assert hi52 == float(long_df["High"].max())
    assert long_df.index.min() < floor                         # 52w frame is the longer one
    assert seen["fin"]["52_week_low"] == float(long_df["Low"].min())
    assert seen["fin"]["52_week_low"] < float(seen["targets_df"]["Low"].min())
    assert r["cls_lane"] in ("pass", "below", "no_upside", "unknown")


def test_row_result_missing_price_is_skip():
    m = _load()
    r = m._row_result({"rec_type": "add_winner", "rec_date": "2026-10-15", "ticker": "X",
                       "price_at_surface": None}, _ohlc(), {}, None)
    assert r["skip"] == "no_price_at_surface" and r["lane"] == "add"
