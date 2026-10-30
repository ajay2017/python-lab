"""Tests for stock_analyzer.revision_flag (finding #22, C1)."""
import re

import numpy as np

from stock_analyzer import revision_flag as rf
from stock_analyzer.constants import (
    EARNINGS_HOLD_OR_ADD_MIN_NET_REVISIONS as HOLD_MIN,
    EARNINGS_REVISION_WATCH_NET as WATCH,
)


def _b(net, ups=0, downs=0, **kw):
    return {"revisions": {"net": net, "upgrades_90d": ups, "downgrades_90d": downs, **kw}}


def test_boundary():
    assert rf.is_flagged(rf.read(_b(WATCH)))
    assert rf.is_flagged(rf.read(_b(WATCH - 1)))
    assert not rf.is_flagged(rf.read(_b(WATCH + 1)))


def test_unknown_inputs():
    bad = [
        None, {}, [], {"revisions": None}, {"revisions": {}}, {"revisions": []},
        {"revisions": {"upgrades_90d": 1}},
        _b(float("nan")), _b(float("inf")), _b(True), _b("x"),
        _b(WATCH, basis="consensus_snapshot"),
    ]
    for b in bad:
        r = rf.read(b)
        assert r["state"] == "unknown" and r["net"] is None
        assert not rf.is_flagged(r)
        assert rf.flag_line(r) is None


def test_ups_downs_none_when_bad():
    r = rf.read({"revisions": {"net": WATCH, "upgrades_90d": True, "downgrades_90d": "x"}})
    assert r["state"] == "ok" and r["ups"] is None and r["downs"] is None


def test_is_flagged_malformed():
    assert not rf.is_flagged(None)
    assert not rf.is_flagged({})
    assert not rf.is_flagged({"state": "ok"})
    assert not rf.is_flagged({"state": "ok", "net": "x"})


def test_plurals():
    n = WATCH  # -2: ups - downs must equal net for the counts sentence
    l = rf.flag_line({"state": "ok", "net": n, "ups": 0, "downs": 2})
    assert "2 downgrades vs 0 upgrades or initiations" in l
    l = rf.flag_line({"state": "ok", "net": n, "ups": 1, "downs": 1 - n})
    assert f"{1 - n} downgrades vs 1 upgrade or initiation " in l
    l = rf.flag_line({"state": "ok", "net": n, "ups": 3, "downs": 3 - n})
    assert "downgrades vs 3 upgrades or initiations" in l
    assert f"(net {n:+d})" in l


def test_inconsistent_counts_use_net_only():
    l = rf.flag_line({"state": "ok", "net": WATCH, "ups": 0, "downs": 0})
    assert l.startswith(f"Analyst ratings: net {WATCH:+d} in rating changes over")
    assert "downgrade" not in l.split(".")[0]


def test_numeric_types():
    assert rf.is_flagged(rf.read(_b(float(WATCH))))
    assert rf.is_flagged(rf.read(_b(np.int64(WATCH))))
    assert rf.is_flagged(rf.read(_b(np.float64(WATCH))))
    assert rf.read(_b(True))["state"] == "unknown"
    assert rf.read(_b(np.bool_(True)))["state"] == "unknown"


def test_window_days():
    # Missing key (cached bundle) -> 90d wording.
    r = rf.read(_b(WATCH, 0, -WATCH))
    assert r["window_days"] == 90
    assert "last 90 days" in rf.flag_line(r)
    assert "(90d)" in rf.checklist_line({"state": "ok", "net": WATCH + 1, "window_days": 90})[1]
    # Explicit 90.
    assert "last 90 days" in rf.flag_line(rf.read(_b(WATCH, 0, -WATCH, window_days=90)))
    # Undated fallback (None) -> no 90-day claim anywhere.
    r = rf.read(_b(WATCH, 0, -WATCH, window_days=None))
    assert r["window_days"] is None
    fl = rf.flag_line(r)
    assert "90" not in fl and "recent months" in fl
    cl = rf.checklist_line({"state": "ok", "net": WATCH + 1, "window_days": None})[1]
    assert "90" not in cl and "(recent)" in cl
    r2 = rf.read(_b(WATCH, window_days=None))
    r2["ups"] = None
    assert "90" not in rf.flag_line(r2)


def test_real_yfinance_producer_shape():
    rev = {
        "upgrades_90d": 1, "downgrades_90d": 3, "maintained_90d": 4,
        "net": -2, "window_days": 90,
        "latest": [{"firm": "X", "to_grade": "Sell", "from_grade": "Hold", "action": "down"}],
    }
    r = rf.read({"revisions": rev})
    assert r == {"state": "ok", "net": -2, "ups": 1, "downs": 3, "window_days": 90}
    assert rf.is_flagged(r) == (-2 <= WATCH)


def test_fmp_consensus_shape_is_unknown():
    from stock_analyzer.providers.fmp_provider import FMPProvider
    p = object.__new__(FMPProvider)
    p._get_json = lambda *a, **k: [{"strongBuy": 1, "buy": 2, "hold": 3, "sell": 5, "strongSell": 4}]
    rev = p._fetch_revisions("AAPL")
    assert rev.get("basis") == "consensus_snapshot" and rev["net"] == -6
    r = rf.read({"revisions": rev})
    assert r["state"] == "unknown" and not rf.is_flagged(r) and rf.flag_line(r) is None


def test_tail_wording():
    l = rf.flag_line({"state": "ok", "net": WATCH, "ups": 0, "downs": -WATCH})
    assert "won't size a trim from it unless that record supports it" in l
    assert "before it sizes" not in l


def test_flag_line_text_and_fallback():
    l = rf.flag_line(rf.read(_b(WATCH, 1, 3)))
    assert "**" not in l and "$" not in l
    assert "rating changes, not earnings-estimate cuts" in l
    l2 = rf.flag_line({"state": "ok", "net": WATCH, "ups": None, "downs": 2})
    assert l2.startswith(f"Analyst ratings: net {WATCH:+d} in rating changes over the last 90 days.")
    assert "rating changes, not earnings-estimate cuts" in l2


def test_revisions_phrase_branches():
    ok = lambda n: {"state": "ok", "net": n}
    assert rf.revisions_phrase({"state": "unknown", "net": None}) == "unavailable"
    assert rf.revisions_phrase(None) == "unavailable"
    assert rf.revisions_phrase(ok(WATCH)) == f"net {WATCH:+d} (flagged to watch)"
    assert rf.revisions_phrase(ok(WATCH + 1)) == "neutral"
    assert rf.revisions_phrase(ok(HOLD_MIN - 1)) == "neutral"
    assert rf.revisions_phrase(ok(HOLD_MIN)) == f"net {HOLD_MIN:+d}"


def test_checklist_line_branches():
    r = {"state": "ok", "net": WATCH, "ups": 0, "downs": 2}
    e, t, c = rf.checklist_line(r)
    assert (e, c) == ("⚑", "#f59e0b") and t == rf.flag_line(r)
    assert rf.checklist_line({"state": "ok", "net": WATCH + 1}) == (
        "•", f"Analyst rating changes (90d): net {WATCH + 1:+d}.", "#9ca3af")
    assert rf.checklist_line({"state": "unknown"}) == (
        "•", "Analyst rating changes unavailable.", "#9ca3af")


def test_no_overclaim_words():
    outs = []
    for n in (WATCH - 1, WATCH, WATCH + 1, HOLD_MIN, HOLD_MIN + 3):
        r = {"state": "ok", "net": n, "ups": 1, "downs": 2}
        outs += [rf.flag_line(r) or "", rf.revisions_phrase(r), rf.checklist_line(r)[1]]
    outs += [rf.revisions_phrase({}), rf.checklist_line({})[1]]
    for o in outs:
        low = o.lower()
        for w in ("urgency", "strongest", "reliable"):
            assert w not in low
        assert "estimate" not in re.sub("not earnings-estimate cuts", "", low)


def test_tail_is_true_at_any_distance_from_the_report():
    l = rf.flag_line({"state": "ok", "net": WATCH, "ups": 0, "downs": -WATCH})
    assert "the app is recording" not in l
    assert "before each report the app records whether this predicts a weak report" in l
