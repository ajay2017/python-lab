"""Writer-boundary sanitize (audit 2026-10-11 Low-D, fixed 2026-10-13).

Four writers passed caller values to `upsert` raw (save_recommendations'
composite/pillar scores and price_at_surface, save_score_history_batch,
save_account_daily_snapshot, save_portfolio_risk_snapshot), and four hand-
rolled `v != v` checks let ±inf through (`inf != inf` is False). One NaN/±inf
-- or a numpy integer, which JSON can't encode -- failed the WHOLE request,
losing every row in it. These tests capture exactly what each writer hands
to `upsert` and assert nothing non-finite or numpy-typed reaches it, while a
real value passes through unchanged.
"""
import json
import math

import warnings

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import db

pytestmark = pytest.mark.fast


class _Result:
    data = []


class _Builder:
    def __init__(self, sink):
        self._sink = sink

    def upsert(self, payload, **_kw):
        self._sink.append(payload)
        return self

    def execute(self):
        return _Result()


class _Client:
    def __init__(self):
        self.sent = []

    def table(self, _name):
        return _Builder(self.sent)


@pytest.fixture
def client(monkeypatch):
    c = _Client()
    monkeypatch.setattr(db, "_client", lambda: c)
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "is_readonly", lambda: False)
    return c


def _rows(payload):
    return payload if isinstance(payload, list) else [payload]


def _assert_json_clean(payload):
    """Encodes under strict JSON (no NaN/Infinity) -- the property the
    request needs -- and every float is finite."""
    json.dumps(payload, allow_nan=False)
    for row in _rows(payload):
        for k, v in row.items():
            assert not isinstance(v, (np.generic,)), f"{k} is still numpy: {type(v)}"
            if isinstance(v, float):
                assert math.isfinite(v), f"{k} = {v}"


BAD = [float("nan"), float("inf"), float("-inf"), np.float64("nan"), np.float64("inf")]


@pytest.mark.parametrize("bad", BAD)
def test_save_recommendations_sends_no_non_finite_value(client, bad):
    r = db.save_recommendations([{
        "ticker": "AAA", "rec_type": "buy_candidate", "rec_date": "2026-10-13",
        "price_at_surface": bad, "composite_score": bad, "t_score": bad,
        "bq_score": np.int64(70), "val_score": np.float64(61.5),
    }])
    assert r["saved"] == 1 and r["error"] is None
    sent = client.sent[-1]
    _assert_json_clean(sent)
    row = sent[0]
    assert row["composite_score"] is None and row["t_score"] is None
    assert row["price_at_surface"] is None
    assert row["bq_score"] == 70 and type(row["bq_score"]) is int   # numpy -> native
    assert row["val_score"] == 61.5 and type(row["val_score"]) is float  # finite numpy -> native


@pytest.mark.parametrize("bad", BAD)
def test_save_score_history_batch_sends_no_non_finite_value(client, bad):
    # Real column names (headless_alert_engine's builder). `composite` is NOT
    # NULL there, so a non-finite one still fails that row -- as it always did;
    # the builder's own _f already drops NaN, so this is defense in depth.
    # (t_score is a real nullable score_history column.)
    assert db.save_score_history_batch([
        {"ticker": "AAA", "score_date": "2026-10-13", "composite": 71.0, "t_score": bad},
        {"ticker": "BBB", "score_date": "2026-10-13", "composite": 72.0, "t_score": 55.0},
    ]) is True
    sent = client.sent[-1]
    _assert_json_clean(sent)
    assert sent[0]["t_score"] is None and sent[1]["t_score"] == 55.0


@pytest.mark.parametrize("bad", BAD)
def test_account_snapshot_sends_no_non_finite_value(client, bad):
    assert db.save_account_daily_snapshot({
        "snapshot_date": "2026-10-13", "gross_book": 25000.5, "cash_balance": bad, "leverage": bad,
    }) is True
    sent = client.sent[-1]
    _assert_json_clean(sent)
    assert sent["cash_balance"] is None and sent["leverage"] is None and sent["gross_book"] == 25000.5


@pytest.mark.parametrize("bad", BAD)
def test_risk_snapshot_sends_no_non_finite_value(client, bad):
    assert db.save_portfolio_risk_snapshot({
        "snapshot_date": "2026-10-13", "portfolio_beta": bad, "top_sector": "Tech",
        "corr_coverage_n": np.int64(119), "avg_pairwise_corr": np.float64(0.12),
    }) is True
    sent = client.sent[-1]
    _assert_json_clean(sent)
    assert sent["portfolio_beta"] is None and sent["top_sector"] == "Tech"
    assert sent["corr_coverage_n"] == 119 and type(sent["corr_coverage_n"]) is int
    assert type(sent["avg_pairwise_corr"]) is float


@pytest.mark.parametrize("missing", [pd.NA, pd.NaT])
def test_pandas_missing_sentinels_become_null_not_a_string(client, missing):
    # str() would have stored the literal '<NA>' / 'NaT' in a text column.
    assert db.save_portfolio_risk_snapshot({"snapshot_date": "2026-10-13", "top_sector": missing}) is True
    assert client.sent[-1]["top_sector"] is None


def test_nulling_a_value_warns_naming_the_keys(client):
    with pytest.warns(UserWarning, match=r"save_account_daily_snapshot.*\['cash_balance', 'leverage'\]"):
        db.save_account_daily_snapshot({
            "snapshot_date": "2026-10-13", "gross_book": 1.0,
            "cash_balance": float("nan"), "leverage": float("inf"), "net_equity": None,
        })


def test_a_clean_payload_does_not_warn(client):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert db.save_portfolio_risk_snapshot({
            "snapshot_date": "2026-10-13", "portfolio_beta": 1.2, "top_sector": None,
        }) is True


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_gate_suppressions_rejects_inf_as_well_as_nan(client, bad):
    r = db.save_gate_suppressions([{
        "ticker": "AAA", "gate_id": "G-07", "rec_date": "2026-10-13", "source": "app",
        "price_at_suppress": bad, "composite_score": bad, "gate_value": bad,
    }])
    assert r["error"] is None
    sent = client.sent[-1]
    _assert_json_clean(sent)
    row = sent[0]
    assert row["price_at_suppress"] is None and row["composite_score"] is None
    assert row["gate_value"] is None


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_rec_events_rejects_inf_as_well_as_nan(client, bad):
    r = db.save_rec_events([{
        "rec_type": "beta_trim", "ticker": "AAA", "fired_date": "2026-10-13",
        "source": "eod", "metric_before": bad, "rec_dollars": 500.0,
    }])
    assert r["error"] is None
    sent = client.sent[-1]
    _assert_json_clean(sent)
    row = sent[0]
    assert row["metric_before"] is None and row["rec_dollars"] == 500.0


# ── the two remaining raw writers (2026-10-13 follow-up) ─────────────────────

def test_gate_suppressions_numpy_bool_counterfactual_no_longer_fails_the_batch(client):
    # lane/counterfactual/tone/sector passed through raw; a numpy bool_
    # (JSON can't encode it) failed the WHOLE ledger batch.
    r = db.save_gate_suppressions([{
        "ticker": "AAA", "gate_id": "G-20", "rec_date": "2026-10-13", "source": "app",
        "counterfactual": np.bool_(True), "composite_score": 70.0, "sector": "Tech",
    }])
    assert r["error"] is None
    sent = client.sent[-1]
    _assert_json_clean(sent)
    assert sent[0]["counterfactual"] is True and type(sent[0]["counterfactual"]) is bool
    assert sent[0]["sector"] == "Tech"


@pytest.mark.parametrize("bad", BAD)
def test_analyst_target_snapshots_send_no_non_finite_value(client, bad):
    assert db.save_analyst_target_snapshots_batch([
        {"ticker": "AAA", "snapshot_date": "2026-10-13", "target_mean": bad,
         "num_analysts": np.int64(12), "info_source": "yfinance"},
        {"ticker": "BBB", "snapshot_date": "2026-10-13", "target_mean": 210.5, "num_analysts": 9},
    ]) is True
    sent = client.sent[-1]
    _assert_json_clean(sent)
    assert sent[0]["target_mean"] is None and sent[0]["num_analysts"] == 12
    assert type(sent[0]["num_analysts"]) is int and sent[1]["target_mean"] == 210.5
