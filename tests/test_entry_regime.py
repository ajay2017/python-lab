"""Tests for stock_analyzer/entry_regime.py: the pre-registered entry regime rules
(MEASURE ONLY; docs/plans/entry-regime-check.md section 3).

All data is synthetic (no network) and dated on REAL NYSE sessions (built with
data.is_trading_day), because the module counts sessions by that calendar.
"""
import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import entry_regime as er
from stock_analyzer import market_trend as mt
from stock_analyzer.data import is_trading_day, previous_trading_day
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

ET = ZoneInfo("America/New_York")
AS_OF = date(2026, 11, 20)  # a Friday and a normal NYSE session
ROOT = Path(__file__).resolve().parent.parent


# ── builders ─────────────────────────────────────────────────────────────────

def _trend(as_of, label="weakening", ma200="above", ndx_label="steady",
           sector_label=None, spx_row=True):
    """A compute_trend-shaped result built with the real row keys."""
    def row(symbol, lab, side200):
        r = {k: None for k in mt._ROW_KEYS}
        r.update({"symbol": symbol, "name": symbol, "app_sectors": [],
                  "label": lab, "reason": "r", "confirm_k": 1,
                  "ma50_side": "below", "ma200_side": side200,
                  "from_high_pct": -7.8, "ma50_run_capped": False})
        return r
    indices = []
    if spx_row:
        indices.append(row("^GSPC", label, ma200))
    indices.append(row("^IXIC", ndx_label, "above"))
    sectors = [row("XLK", sector_label or "rising", None),
               row("XLE", sector_label or "falling", None)]
    return {"as_of": as_of, "indices": indices, "sectors": sectors, "not_checked": []}


def _rd(label="weakening", ma200="above", vix=20.0, as_of=AS_OF):
    return er.read(_trend(as_of, label=label, ma200=ma200), vix, as_of)


def _sessions_to(end, n):
    out, d = [end], end
    while len(out) < n:
        d -= timedelta(days=1)
        if is_trading_day(d):
            out.append(d)
    return list(reversed(out))


def _frame(spx_values, end=AS_OF):
    """Frame over trend_symbols(): the S&P 500 from spx_values, the rest flat."""
    n = len(spx_values)
    idx = pd.DatetimeIndex(_sessions_to(end, n))
    data = {}
    for sym in mt.trend_symbols():
        data[sym] = np.asarray(spx_values, dtype=float) if sym == "^GSPC" else np.full(n, 100.0)
    return pd.DataFrame(data, index=idx)


def _vix(values_by_date, tz=None):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in values_by_date])
    if tz:
        idx = idx.tz_localize(tz)
    return pd.Series(list(values_by_date.values()), index=idx, dtype=object)


# ── vix_close_asof ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("tz", [None, "America/New_York", "UTC"])
def test_vix_close_asof_reads_the_exact_date_tz_naive_or_aware(tz):
    s = _vix({date(2026, 11, 19): 19.0, date(2026, 11, 20): 21.5, date(2026, 11, 23): 40.0}, tz=tz)
    assert er.vix_close_asof(s, AS_OF) == 21.5


def test_vix_close_asof_missing_asof_never_uses_a_prior_close():
    s = _vix({date(2026, 11, 18): 19.0, date(2026, 11, 19): 22.0})
    assert er.vix_close_asof(s, AS_OF) is None


def test_vix_close_asof_ignores_a_bar_dated_today():
    # Mon 11-23 is "today" for a Friday as-of; its bar must not leak in
    only_today = _vix({date(2026, 11, 23): 55.0})
    assert er.vix_close_asof(only_today, AS_OF) is None
    both = _vix({date(2026, 11, 20): 18.0, date(2026, 11, 23): 55.0})
    assert er.vix_close_asof(both, AS_OF) == 18.0


def test_vix_close_asof_accepts_a_plain_date_index():
    s = pd.Series([22.0], index=[AS_OF])
    assert er.vix_close_asof(s, AS_OF) == 22.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 0.0, -3.0, True, False, None, "abc"])
def test_vix_close_asof_rejects_unusable_values(bad):
    s = pd.Series([bad], index=pd.DatetimeIndex([pd.Timestamp(AS_OF)]), dtype=object)
    assert er.vix_close_asof(s, AS_OF) is None


def test_vix_close_asof_rejects_a_bool_dtype_series():
    s = pd.Series([True], index=pd.DatetimeIndex([pd.Timestamp(AS_OF)]))
    assert er.vix_close_asof(s, AS_OF) is None


def test_vix_close_asof_none_and_empty():
    assert er.vix_close_asof(None, AS_OF) is None
    assert er.vix_close_asof(pd.Series([], dtype=float), AS_OF) is None


def test_vix_close_asof_duplicate_date_keeps_the_last_bar():
    idx = pd.DatetimeIndex([pd.Timestamp(AS_OF), pd.Timestamp(AS_OF)])
    assert er.vix_close_asof(pd.Series([30.0, 26.0], index=idx), AS_OF) == 26.0


# ── read ─────────────────────────────────────────────────────────────────────

def test_read_ok_snapshot_carries_every_input():
    r = _rd(label="weakening", ma200="above", vix=22.5)
    assert r["read_state"] == "ok"
    assert r["as_of"] == AS_OF
    assert r["spx_label"] == "weakening"
    assert r["spx_reason"] == "r"
    assert r["spx_confirm_k"] == 1
    assert r["spx_ma50_side"] == "below"
    assert r["spx_ma200_side"] == "above"
    assert r["spx_from_high_pct"] == -7.8
    assert r["ndx_label"] == "steady"
    assert r["ndx_ma200_side"] == "above"
    assert r["vix_close"] == 22.5


def test_read_trend_none_is_unread_and_keeps_the_vix():
    r = er.read(None, 26.0, AS_OF)
    assert r["read_state"] == "trend_unread"
    assert r["spx_label"] is None and r["ndx_label"] is None
    assert r["vix_close"] == 26.0


def test_read_trend_for_another_session_is_unread():
    r = er.read(_trend(date(2026, 11, 19)), 20.0, AS_OF)
    assert r["read_state"] == "trend_unread"
    assert r["spx_label"] is None


def test_read_missing_sp500_row_is_unread():
    r = er.read(_trend(AS_OF, spx_row=False), 20.0, AS_OF)
    assert r["read_state"] == "trend_unread"
    assert r["spx_label"] is None and r["ndx_label"] is None


def test_read_cleans_a_bad_vix_to_none():
    assert _rd(vix=float("nan"))["vix_close"] is None
    assert _rd(vix=0.0)["vix_close"] is None
    assert _rd(vix=True)["vix_close"] is None


# ── three-valued logic: rules A and B ────────────────────────────────────────

@pytest.mark.parametrize("label,a,b", [
    ("falling", "hold", "hold"),
    ("weakening", "hold", "hold"),
    ("steady", "clear", "hold"),
    ("rising", "clear", "clear"),
    (None, "unknown", "unknown"),
])
def test_rules_a_and_b_for_every_label(label, a, b):
    r = {"read_state": "ok", "spx_label": label, "spx_ma200_side": None, "vix_close": None}
    assert er.decide(r, "A")["state"] == a
    assert er.decide(r, "B")["state"] == b


@pytest.mark.parametrize("rule", ["A", "B"])
def test_an_unread_trend_is_unknown_never_clear(rule):
    r = er.read(None, 20.0, AS_OF)
    assert er.decide(r, rule)["state"] == "unknown"
    # even a stale label on a read that says it is unread cannot clear
    stale = {"read_state": "trend_unread", "spx_label": "rising"}
    assert er.decide(stale, rule)["state"] == "unknown"


def test_decide_shape_and_version():
    d = er.decide(_rd(label="falling"), "A")
    assert d["rule"] == "A"
    assert d["rule_version"] == C.ENTRY_REGIME_RULE_VERSION
    assert isinstance(d["reason"], str) and d["reason"]
    assert isinstance(d["legs"], list) and d["legs"]


def test_decide_rejects_an_unknown_rule():
    with pytest.raises(ValueError):
        er.decide(_rd(), "D")


# ── three-valued logic: rule C ───────────────────────────────────────────────

@pytest.mark.parametrize("side,vix,expected", [
    ("below", 30.0, "hold"),
    ("below", 15.0, "hold"),
    ("below", None, "hold"),      # one measured leg trips: hold
    ("above", 30.0, "hold"),
    ("above", 15.0, "clear"),     # both measured, neither trips
    ("at", 15.0, "clear"),        # equal is not below (strict)
    ("above", None, "unknown"),   # clear leg + unmeasured leg is not clear
    (None, 30.0, "hold"),
    (None, 15.0, "unknown"),
    (None, None, "unknown"),
])
def test_rule_c_leg_combinations(side, vix, expected):
    r = {"read_state": "ok", "spx_label": "steady", "spx_ma200_side": side, "vix_close": vix}
    assert er.decide(r, "C")["state"] == expected


def test_rule_c_vix_boundary_is_greater_or_equal():
    base = {"read_state": "ok", "spx_ma200_side": "above"}
    assert C.RISK_OFF_VIX_LEVEL == 25.0
    assert er.decide({**base, "vix_close": 25.0}, "C")["state"] == "hold"
    assert er.decide({**base, "vix_close": 24.99}, "C")["state"] == "clear"
    assert er.decide({**base, "vix_close": 25.01}, "C")["state"] == "hold"


def test_rule_c_unread_trend_still_holds_on_a_tripped_vix_but_never_clears():
    assert er.decide(er.read(None, 30.0, AS_OF), "C")["state"] == "hold"
    assert er.decide(er.read(None, 15.0, AS_OF), "C")["state"] == "unknown"


def test_candidates_returns_a_b_c():
    c = er.candidates(_rd(label="steady", ma200="above", vix=30.0))
    assert set(c) == {"A", "B", "C"}
    assert (c["A"]["state"], c["B"]["state"], c["C"]["state"]) == ("clear", "hold", "hold")


# ── the as-of bar and the time-context matrix ────────────────────────────────

def test_a_bar_dated_after_the_asof_session_changes_no_verdict():
    base = 100 + np.arange(300) * 0.1
    plain = _frame(base, end=AS_OF)
    # add Monday 11-23 with a 30% crash, then ask for the Friday as-of
    later = pd.concat([plain, pd.DataFrame({c: [70.0] if c == "^GSPC" else [100.0]
                                            for c in plain.columns},
                                           index=pd.DatetimeIndex([pd.Timestamp(2026, 11, 23)]))])
    vix_plain = _vix({AS_OF: 18.0})
    vix_later = _vix({AS_OF: 18.0, date(2026, 11, 23): 60.0})
    r1 = er.read(mt.compute_trend(plain, as_of=AS_OF), er.vix_close_asof(vix_plain, AS_OF), AS_OF)
    r2 = er.read(mt.compute_trend(later, as_of=AS_OF), er.vix_close_asof(vix_later, AS_OF), AS_OF)
    assert r1 == r2
    assert er.candidates(r1) == er.candidates(r2)


@pytest.mark.parametrize("call,as_of", [
    (date(2026, 11, 23), date(2026, 11, 20)),   # Monday -> Friday
    (date(2026, 11, 27), date(2026, 11, 25)),   # day after Thanksgiving (half day) -> Wednesday
    (date(2026, 11, 2), date(2026, 10, 30)),    # Monday after the fall-back change
    (date(2027, 3, 15), date(2027, 3, 12)),     # Monday after the spring-forward change
    (date(2026, 11, 18), date(2026, 11, 17)),   # ordinary Wednesday
])
def test_shadow_row_matches_the_previous_session(call, as_of):
    assert is_trading_day(call)
    assert previous_trading_day(call) == as_of
    row = er.shadow_row(_rd(as_of=as_of, label="falling"), call, "scan")
    assert row is not None
    assert row["call_date"] == call
    assert row["as_of_session"] == as_of
    assert row["read_state"] == "ok"
    assert row["rule_a"] == "hold"


@pytest.mark.parametrize("call", [
    date(2026, 11, 21),   # Saturday
    date(2026, 11, 22),   # Sunday
    date(2026, 11, 26),   # Thanksgiving Thursday
    date(2026, 11, 1),    # fall-back Sunday
    date(2027, 3, 14),    # spring-forward Sunday
])
def test_shadow_row_is_none_on_a_non_trading_day(call):
    assert not is_trading_day(call)
    assert er.shadow_row(_rd(), call, "scan") is None


def test_shadow_row_asof_mismatch_is_all_unknown():
    # Monday 11-23 expects Friday 11-20; a read for Thursday 11-19 is a mismatch
    row = er.shadow_row(_rd(label="falling", ma200="below", vix=40.0, as_of=date(2026, 11, 19)),
                        date(2026, 11, 23), "scan")
    assert row["read_state"] == "asof_mismatch"
    assert (row["rule_a"], row["rule_b"], row["rule_c"]) == ("unknown", "unknown", "unknown")
    assert row["as_of_session"] == date(2026, 11, 19)  # what was actually read


def test_shadow_row_with_the_call_day_itself_as_asof_is_a_mismatch():
    # a read dated the call date (an intraday read) is never accepted
    row = er.shadow_row(_rd(label="falling", as_of=date(2026, 11, 23)), date(2026, 11, 23), "scan")
    assert row["read_state"] == "asof_mismatch"
    assert row["rule_a"] == "unknown"


def test_shadow_row_trend_unread_still_yields_a_row():
    row = er.shadow_row(er.read(None, 30.0, AS_OF), date(2026, 11, 23), "scan")
    assert row is not None
    assert row["read_state"] == "trend_unread"
    assert row["spx_label"] is None
    assert (row["rule_a"], row["rule_b"]) == ("unknown", "unknown")
    assert row["rule_c"] == "hold"  # the VIX leg alone still trips
    assert row["vix_close"] == 30.0


def test_shadow_row_records_the_rule_version_source_and_inputs():
    row = er.shadow_row(_rd(label="steady", ma200="above", vix=19.0), date(2026, 11, 23), "cron")
    assert row["rule_version"] == C.ENTRY_REGIME_RULE_VERSION
    assert row["source"] == "cron"
    assert row["spx_label"] == "steady" and row["ndx_label"] == "steady"
    assert row["spx_ma200_side"] == "above" and row["ndx_ma200_side"] == "above"
    assert row["vix_close"] == 19.0
    assert (row["rule_a"], row["rule_b"], row["rule_c"]) == ("clear", "hold", "clear")
    assert set(row) == {
        "call_date", "as_of_session", "source", "rule_version", "read_state",
        "spx_label", "spx_reason", "spx_confirm_k", "spx_ma50_side", "spx_ma200_side",
        "spx_from_high_pct", "ndx_label", "ndx_ma50_side", "ndx_ma200_side",
        "vix_close", "rule_a", "rule_b", "rule_c"}


@pytest.mark.parametrize("hour,minute", [(0, 5), (9, 45), (16, 1), (23, 55)])
def test_as_of_session_is_one_session_all_day(hour, minute):
    now = datetime(2026, 11, 18, hour, minute, tzinfo=ET)
    assert mt.as_of_session(now) == date(2026, 11, 17)


def test_as_of_session_utc_clock_maps_to_the_prior_et_date():
    # 2026-11-19 03:30 UTC is 2026-11-18 22:30 ET, so today is 11-18 and the
    # last completed session is 11-17 (the naive UTC date would give 11-18)
    utc = datetime(2026, 11, 19, 3, 30, tzinfo=timezone.utc)
    assert mt.as_of_session(utc) == date(2026, 11, 17)


# ── subsumption and the no-sector rule ───────────────────────────────────────

def test_rule_c_trend_leg_trips_only_when_rule_a_holds():
    """market_trend: a close below the 200-day average is a weakness leg, so the
    S&P 500 reads Weakening or Falling and rule A holds whenever C's trend leg trips."""
    below_seen = other_seen = 0
    for seed, late_drift in [(1, -0.004), (2, -0.003), (3, -0.006), (4, 0.0), (5, 0.002)]:
        rng = np.random.default_rng(seed)
        mu = np.where(np.arange(400) < 250, 0.0008, late_drift)
        closes = 100.0 * np.exp(np.cumsum(rng.normal(mu, 0.01)))
        frame = _frame(closes)
        for as_of in _sessions_to(AS_OF, 400)[-150::5]:
            res = mt.compute_trend(frame, as_of=as_of)
            if res is None:
                continue
            rd = er.read(res, 15.0, as_of)
            if rd["read_state"] != "ok":
                continue
            if rd["spx_ma200_side"] == "below":
                below_seen += 1
                assert rd["spx_label"] in ("weakening", "falling")
                assert er.decide(rd, "A")["state"] == "hold"
                assert er.decide(rd, "C")["state"] == "hold"
            else:
                other_seen += 1
    assert below_seen > 0 and other_seen > 0  # the loop actually exercised both sides


def test_no_sector_input_changes_a_verdict():
    labels = ("falling", "weakening", "steady", "rising", None)
    verdicts = []
    for lab in labels:
        t = _trend(AS_OF, label="steady", ma200="above", sector_label=lab)
        if lab is None:
            for s in t["sectors"]:
                s["label"] = None
        r = er.read(t, 20.0, AS_OF)
        verdicts.append((r, er.candidates(r)))
    assert all(v == verdicts[0] for v in verdicts)


# ── pins ─────────────────────────────────────────────────────────────────────

def test_the_label_constants_are_valid_market_trend_labels():
    valid = {k for k in mt._LABEL_TEXT if k is not None}
    assert set(C.ENTRY_REGIME_HOLD_SPX_LABELS) <= valid
    assert set(C.ENTRY_REGIME_ALT_HOLD_SPX_LABELS) <= valid
    assert set(C.ENTRY_REGIME_HOLD_SPX_LABELS) <= set(C.ENTRY_REGIME_ALT_HOLD_SPX_LABELS)
    # Rule B is "label != Rising" (plan §3): a label market_trend adds later must
    # land in B's hold set, never read "clear" under B by omission (C1 review).
    assert set(C.ENTRY_REGIME_ALT_HOLD_SPX_LABELS) == valid - {"rising"}


# {rule version: fingerprint}. EDITOR: a changed value needs a NEW version key,
# so changing a value without bumping ENTRY_REGIME_RULE_VERSION fails here (C1
# review). Also bump for a change to a rule in entry_regime.py or to
# market_trend's label logic, which no fingerprint can see.
_RULE_FINGERPRINTS = {
    1: (
        ("weakening", "falling"),
        ("weakening", "falling", "steady"),
        25.0,
        200,
        50,
        5,
        3,
    ),
}


def test_rule_definitions_are_pinned_to_the_rule_version():
    fingerprint = (
        C.ENTRY_REGIME_HOLD_SPX_LABELS,
        C.ENTRY_REGIME_ALT_HOLD_SPX_LABELS,
        C.RISK_OFF_VIX_LEVEL,
        C.RISK_OFF_TREND_MA,
        C.TREND_MA_SESSIONS,
        C.TREND_MA_SLOPE_SESSIONS,
        C.TREND_CONFIRM_SESSIONS,
    )
    assert C.ENTRY_REGIME_RULE_VERSION in _RULE_FINGERPRINTS, "add this version's fingerprint"
    assert _RULE_FINGERPRINTS[C.ENTRY_REGIME_RULE_VERSION] == fingerprint
    # A new version must actually change something.
    fps = list(_RULE_FINGERPRINTS.values())
    assert len(fps) == len(set(fps))


def test_entry_regime_module_is_pure():
    tree = ast.parse((ROOT / "stock_analyzer" / "entry_regime.py").read_text(encoding="utf-8"))
    banned = ("streamlit", "stock_analyzer.db", "stock_analyzer.exit_advisor", "supabase")
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imported.append(mod)
            imported += [f"{mod}.{a.name}" for a in node.names]
    for name in imported:
        assert not any(name == b or name.startswith(b + ".") for b in banned), name
    assert not any(n.endswith(".db") or n == "db" for n in imported)
    assert not any(n.endswith("exit_advisor") for n in imported)


# Every module allowed to import entry_regime while the rule is measure-only. A
# new importer must be added here deliberately (C1 review): the readout (C2),
# then C3's shadow capture helper; a decision module only at go-live.
_ENTRY_REGIME_IMPORT_ALLOWLIST = {
    "stock_analyzer/entry_regime.py",
    "stock_analyzer/enter_now_readout.py",   # C2: measure-only readout
}


def _imports_entry_regime(tree) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any("entry_regime" in a.name for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if "entry_regime" in (node.module or "") or any(
                    "entry_regime" in a.name for a in node.names):
                return True
    return False


def test_no_decision_module_imports_entry_regime():
    """Shadow invariant: only the allowlisted measure-only modules may import the
    rules. Walks every stock_analyzer module plus app.py and cron_runner.py."""
    paths = sorted((ROOT / "stock_analyzer").rglob("*.py")) + [ROOT / "app.py", ROOT / "cron_runner.py"]
    offenders = []
    for f in paths:
        rel = f.relative_to(ROOT).as_posix()
        if rel in _ENTRY_REGIME_IMPORT_ALLOWLIST or "__pycache__" in rel:
            continue
        if _imports_entry_regime(parse_file_cached(f)):
            offenders.append(rel)
    assert not offenders, f"entry_regime imported outside the allowlist: {offenders}"


def test_as_date_converts_a_tz_aware_evening_to_its_et_date():
    """21:00 ET on Mon 11-23 is 02:00 UTC on 11-24: the ET date wins (C1 review;
    the TJUtc / C3b class). shadow_row must not record 11-24 or an as-of mismatch."""
    from datetime import timezone
    import pandas as pd
    utc_evening = datetime(2026, 11, 24, 2, 0, tzinfo=timezone.utc)
    assert er._as_date(utc_evening) == date(2026, 11, 23)
    assert er._as_date(pd.Timestamp(utc_evening)) == date(2026, 11, 23)
    assert er._as_date(datetime(2026, 11, 23, 21, 0)) == date(2026, 11, 23)   # naive keeps its date
    assert er._as_date("2026-11-23") is None
    assert er._as_date(pd.NaT) is None
    row = er.shadow_row(er.read(None, None, date(2026, 11, 20)), utc_evening, "cron")
    assert row is not None and row["call_date"] == date(2026, 11, 23)
    assert row["read_state"] != "asof_mismatch"


def test_vix_close_asof_rejects_a_dataframe_loudly():
    import pandas as pd
    df = pd.DataFrame({"Close": [20.0]}, index=pd.to_datetime(["2026-11-20"]))
    with pytest.raises(TypeError):
        er.vix_close_asof(df, date(2026, 11, 20))
