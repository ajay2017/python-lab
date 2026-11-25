"""Offline tests for the `--carry` mode of scripts/held_stop_dryrun.py (SlidingStop C2b).

Synthetic frames only: no network, no DB, no app import. The mode is a read-only replay
of the EOD carry writer / the carry reader (`held_stop_carry`) under a FORCED "the
journal stops dating this position from session k" scenario (spec:
docs/plans/sliding-stop.md section 7 item 4).
"""
from __future__ import annotations

import csv
import importlib.util
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import held_stop_carry as hsc
from stock_analyzer import held_stop_wiring as hsw
from stock_analyzer.data import is_trading_day

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "held_stop_dryrun.py"


def _load():
    spec = importlib.util.spec_from_file_location("held_stop_dryrun_carry_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dr = _load()
T = "AAA"
POL = {**hsw._policy(), "trail_start": None}     # constants-driven, no go-live clip
AS_OF = date(2026, 10, 1)                         # a Thursday; the frames end 2026-09-30
N, E_IDX = 140, 70
JSIG = (1, 1)


# -- builders ------------------------------------------------------------------

def trading_days(end: date, n: int) -> list:
    out, d = [], end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def make_frame(days, closes) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c}, index=idx)


class Scn:
    """kind: flat (a gentle rise), bleed (flat to a peak, then -0.6 a session), gap (flat,
    then one -15% day at index 130)."""

    def __init__(self, kind, n=N, e_idx=E_IDX, seed=1):
        rng = np.random.default_rng(seed)
        c = np.empty(n)
        if kind == "flat":
            c[:] = 100 + 0.05 * np.arange(n) + rng.normal(0, 0.1, n)
        elif kind == "bleed":
            c[:116] = 100 + rng.normal(0, 0.1, 116)
            c[116:] = 100 - 0.6 * np.arange(1, n - 116 + 1)
        elif kind == "gap":
            c[:130] = 100 + rng.normal(0, 0.1, 130)
            c[130:] = 85 + rng.normal(0, 0.1, n - 130)
        else:
            raise ValueError(kind)
        self.days = trading_days(date(2026, 9, 30), n)
        self.closes = c
        self.df = make_frame(self.days, c)
        self.start = self.days[e_idx]
        self.avg = float(c[e_idx])
        self.ep = {"status": "ok", "start": self.start, "first_buy_price": self.avg,
                   "split_ratio": 1.0, "reason": "", "last_buy": self.start}

    def holding(self, *, sessions=20, ks=None, force_lapse_from=None, journal_sig=JSIG,
                ep=None, long_df=None):
        return dr.carry_holding(T, 10.0, self.avg, self.ep if ep is None else ep,
                                self.df if long_df is None else long_df, AS_OF, POL,
                                journal_sig=journal_sig, sessions=sessions, ks=ks,
                                force_lapse_from=force_lapse_from)


def scenario(r, k, variant):
    return next(s for s in r["scenarios"] if s["k"] == k and s["variant"] == variant)


def all_sessions(r):
    return [e for s in r["scenarios"] for e in s["log"]]


@pytest.fixture(scope="module")
def flat():
    return Scn("flat")


@pytest.fixture(scope="module")
def bleed():
    return Scn("bleed")


@pytest.fixture(scope="module")
def gap():
    return Scn("gap")


# -- the flags -----------------------------------------------------------------

def test_carry_flags_false_breach_needs_a_full_true_basis():
    f = dr.carry_flags(100.0, 90.0, "full", 90.0, 100.0)       # equality counts as breached
    assert f["carried_breached"] and not f["truth_breached"]
    assert f["flags"] == ["FALSE_BREACH", "CARRY_ABOVE_FULL"]      # 100 is also above the full 90
    g = dr.carry_flags(100.0, 90.0, "partial", 90.0, 100.0)
    assert g["flags"] == ["BREACH_NO_FULL_TRUTH"]
    ok = dr.carry_flags(100.0, 100.0, "full", 90.0, 100.0)     # true breached too: not false
    assert ok["flags"] == ["SAVED_A_SLIDE"]


def test_carry_above_full_tolerates_one_cent_and_reports_more():
    assert dr.carry_flags(150.0, 100.00, "full", 99.0, 100.01)["flags"] == []
    assert dr.carry_flags(150.0, 100.00, "full", 99.0, 100.02)["flags"] == ["CARRY_ABOVE_FULL"]
    assert dr.carry_flags(150.0, 100.00, "partial", 99.0, 120.0)["flags"] == []


def test_missed_and_missing_stops_never_crash():
    assert dr.carry_flags(100.0, 110.0, "full", 90.0, 95.0)["flags"] == ["MISSED"]
    f = dr.carry_flags(None, None, None, None, None)
    assert f["flags"] == [] and not f["carried_breached"]


def test_carry_ks_are_first_middle_and_five_before_the_end():
    ds = list(range(20))
    assert dr.carry_ks(ds) == [0, 10, 15]
    assert dr.carry_ks([7]) == [7]
    assert dr.carry_ks([]) == []
    assert dr.carry_ks(list(range(3))) == [0, 1]        # late clamps to the first, no repeats


# -- a flat / rising series ----------------------------------------------------

def test_flat_series_has_no_false_breach_and_carry_sits_between_uncarried_and_full(flat):
    r = flat.holding()
    assert not r["skipped"] and len(r["dates"]) == 20 and len(r["ks"]) == 3
    assert len(r["scenarios"]) == 3 * len(dr.CARRY_VARIANTS)
    sess = all_sessions(r)
    assert sess and not any("FALSE_BREACH" in e["flags"] for e in sess)
    assert not any("CARRY_ABOVE_FULL" in e["flags"] for e in sess)
    for e in sess:
        assert e["carried_stop"] >= e["uncarried_stop"]                       # never lowers
        assert e["carried_stop"] <= e["truth_stop"] + dr.CARRY_ABOVE_FULL_TOL  # not above full
        assert e["truth_basis"] == "full"
    s = dr.summarize_carry([r])
    assert s["false_breaches"] == 0 and dr.carry_gate_line(s) == "GATE: PASS (0 false breaches)"
    # before the mismatch the carry has nothing to do on a full basis
    pre = [e for e in sess if not e["mismatch"]]
    assert pre and all(e["carry_state"] == "n/a" for e in pre)


def test_the_mismatch_starts_at_k_and_rows_are_saved_in_memory(flat):
    r = flat.holding()
    k = r["ks"][1]
    log = scenario(r, k, "journal_readable")["log"]
    assert [e["mismatch"] for e in log] == [e["as_of"] >= k for e in log]
    assert all(e["wrote"] for e in log)                 # a full / carried row is saved nightly
    post = [e for e in log if e["mismatch"]]
    assert {e["carry_state"] for e in post} <= {"applied", "not_binding"}


# -- a slow bleed --------------------------------------------------------------

def test_slow_bleed_is_saved_by_the_carry_and_never_breaches_uncarried(bleed):
    k = bleed.days[120]
    r = bleed.holding(sessions=25, ks=[k])
    log = scenario(r, k, "journal_readable")["log"]
    assert not any(e["uncarried_breached"] for e in log)         # the bug the carry fixes
    first_true = next(e for e in log if e["truth_breached"])
    assert first_true["as_of"] >= k and first_true["mismatch"]
    assert "SAVED_A_SLIDE" in first_true["flags"]                # on the very session it breaches
    assert first_true["carried_breached"] and not first_true["uncarried_breached"]
    # the carry holds the full stop the real basis had reached before k
    assert first_true["carried_stop"] == pytest.approx(first_true["truth_stop"], abs=0.01)
    assert not any("FALSE_BREACH" in e["flags"] for e in all_sessions(r))
    # the same through a read-time outage when the EOD still reads the journal
    alt = scenario(r, k, "journal_down_at_read")["log"]
    assert [e["flags"] for e in alt] == [e["flags"] for e in log]


def test_both_u2_variants_run_and_a_down_journal_carries_one_session_only(bleed):
    k = bleed.days[120]
    r = bleed.holding(sessions=25, ks=[k])
    assert {s["variant"] for s in r["scenarios"]} == set(dr.CARRY_VARIANTS)
    down = scenario(r, k, "journal_down")["log"]
    post = [e for e in down if e["mismatch"]]
    # the first session reads the previous session's row; the writer fails closed after
    # that, so every later session lapses as unverified (U2) and the slide is MISSED
    assert post[0]["carry_state"] == "applied" and not post[0]["wrote"]
    assert all(e["carry_state"] == "lapsed" and e["carry_code"] == "unverified" for e in post[1:])
    assert not any(e["wrote"] for e in post)
    assert any("MISSED" in e["flags"] for e in post)
    assert not any("FALSE_BREACH" in e["flags"] for e in down)
    readable = scenario(r, k, "journal_readable")["log"]
    assert not any(e["carry_code"] for e in readable)


# -- a gap-down day ------------------------------------------------------------

def test_gap_down_after_k_breaches_carried_and_full_together(gap):
    k = gap.days[120]
    r = gap.holding(sessions=25, ks=[k])
    gap_day = gap.days[130]
    for variant in ("journal_readable", "journal_down_at_read"):
        log = scenario(r, k, variant)["log"]
        on = next(e for e in log if e["as_of"] == gap_day)
        assert on["truth_breached"] and on["carried_breached"]
        assert not any("FALSE_BREACH" in e["flags"] for e in log)
        for e in log:               # whenever the carried stop is breached, so is the true one
            if e["carried_breached"]:
                assert e["truth_breached"]
    assert dr.summarize_carry([r])["false_breaches"] == 0


# -- a lapse -------------------------------------------------------------------

def test_an_average_cost_change_lapses_the_carry_with_its_reason(bleed):
    k = bleed.days[120]
    lapse = bleed.days[130]
    r = bleed.holding(sessions=25, ks=[k], force_lapse_from=lapse)
    log = scenario(r, k, "journal_readable")["log"]
    before = next(e for e in log if e["as_of"] == bleed.days[129])
    on = next(e for e in log if e["as_of"] == lapse)
    assert before["carry_state"] == "applied" and not before["carry_code"]
    assert on["carry_state"] == "lapsed" and on["carry_code"] == "trades"
    assert "average cost" in on["carry_reason"]
    assert on["carried_stop"] == on["uncarried_stop"]            # the old stop is not used
    assert on["carried_stop"] < before["carried_stop"]
    # the lapse day's own stop re-seeds the row (the fingerprint is now the new one):
    # the old, higher row never comes back
    after = [e for e in log if e["as_of"] > lapse]
    assert after and all(e["carried_stop"] < before["carried_stop"] for e in after)
    s = dr.summarize_carry([r])
    assert s["lapses"][T]["trades"] >= 1


# -- the detector can fail the gate --------------------------------------------

def test_a_carried_stop_above_the_close_is_reported_as_a_false_breach(flat, monkeypatch):
    real = hsc.apply_carry
    target = flat.days[-3]

    def lifted(result, row, *, as_of, **kw):
        out = real(result, row, as_of=as_of, **kw)
        if as_of == target:
            out = {**out, "stop": 1000.0}
        return out
    monkeypatch.setattr(hsc, "apply_carry", lifted)
    r = flat.holding()
    s = dr.summarize_carry([r])
    assert s["false_breaches"] > 0
    # the lifted row is saved and carried forward, so later sessions breach too
    assert min(e["as_of"] for e in s["detail"]["FALSE_BREACH"]) == target
    assert s["flag_counts"]["CARRY_ABOVE_FULL"] > 0
    line = dr.carry_gate_line(s)
    assert line.startswith(f"GATE: FAIL ({s['false_breaches']} false breaches: ")
    assert T in line and str(target) in line


def _entry(**kw):
    base = {"ticker": T, "k": date(2026, 9, 1), "variant": "journal_readable",
            "as_of": date(2026, 9, 2), "mismatch": True, "close": 100.0, "truth_stop": 90.0,
            "truth_basis": "full", "uncarried_stop": 85.0, "uncarried_basis": "prior_session",
            "carried_stop": 101.0, "carried_minus_truth": 11.0, "carry_state": "applied",
            "carry_reason": "", "carry_code": "", "carry_binding": "trailing",
            "truth_breached": False, "uncarried_breached": False, "carried_breached": True,
            "wrote": True, "flags": ["FALSE_BREACH"]}
    base.update(kw)
    return base


def _row(entries):
    return {"ticker": T, "shares": 10.0, "avg_cost": 100.0, "as_of": AS_OF,
            "episode_status": "ok", "episode_start": date(2026, 6, 1), "last_buy": None,
            "skipped": "", "dates": [e["as_of"] for e in entries], "ks": [entries[0]["k"]],
            "scenarios": [{"k": entries[0]["k"], "variant": "journal_readable", "log": entries,
                           "counts": {}, "states": {}, "lapses": {}}]}


def test_gate_line_text_for_zero_one_many_and_nothing_exercised():
    assert dr.carry_gate_line(dr.summarize_carry([_row([_entry(flags=[])])])) == \
        "GATE: PASS (0 false breaches)"
    one = dr.carry_gate_line(dr.summarize_carry([_row([_entry()])]))
    assert one.startswith("GATE: FAIL (1 false breaches: ") and one.endswith(")")
    assert "AAA k=2026-09-01 journal_readable 2026-09-02" in one
    many = [_entry(as_of=date(2026, 9, 2) + timedelta(days=i)) for i in range(12)]
    big = dr.carry_gate_line(dr.summarize_carry([_row(many)]))
    assert big.startswith("GATE: FAIL (12 false breaches: ") and "(+2 more)" in big
    nothing = dr.carry_gate_line(dr.summarize_carry(
        [{"ticker": "ANT", "skipped": "no usable episode", "scenarios": []}]))
    assert nothing.startswith("GATE: NOT EXERCISED")
    assert dr.carry_gate_line(dr.summarize_carry([])).startswith("GATE: NOT EXERCISED")


# -- holdings that cannot be replayed ------------------------------------------

def test_holdings_that_cannot_be_exercised_are_skipped_with_a_reason(flat):
    short = flat.df.iloc[-10:]                                   # < HELD_STOP_MIN_BARS bars
    young = {**flat.ep, "start": flat.days[-9], "last_buy": flat.days[-9]}
    r = flat.holding(ep=young, long_df=short)
    assert "HELD_STOP_MIN_BARS" in r["skipped"] and not r["scenarios"]
    assert "no usable episode" in flat.holding(ep={"status": "mismatch", "reason": "x"})["skipped"]
    assert "bought today" in flat.holding(ep={**flat.ep, "start": AS_OF})["skipped"]
    assert "fingerprint" in flat.holding(journal_sig=None)["skipped"]
    assert "no usable long price history" in flat.holding(long_df=pd.DataFrame())["skipped"]
    s = dr.summarize_carry([r, flat.holding()])
    assert s["exercised"] == [T] and len(s["skipped"]) == 1


# -- reporting -----------------------------------------------------------------

def test_summary_counts_and_the_printed_report(bleed, capsys):
    k = bleed.days[120]
    r = bleed.holding(sessions=25, ks=[k])
    s = dr.summarize_carry([r])
    assert s["flag_counts"]["SAVED_A_SLIDE"] > 0 and s["flag_counts"]["MISSED"] > 0
    assert s["false_breaches"] == 0 and s["lapses"][T]["unverified"] > 0
    assert s["scenario_sessions"] == 3 * 25
    dr.print_carry([r])
    dr.print_carry_summary(s)
    out = capsys.readouterr().out
    for want in ("journal_readable", "journal_down", "journal_down_at_read", "SAVED_A_SLIDE",
                 "MISSED", "lapses observed (reason codes) per holding", "flag counts:",
                 "holdings exercised", "GATE: PASS (0 false breaches)", "unverified"):
        assert want in out, want
    brief = out.count("\n    20")
    dr.print_carry([r], detail=True)
    assert capsys.readouterr().out.count("\n    20") > brief          # --detail lists every session


def test_header_explains_the_scenario_variants_and_flags(capsys):
    from types import SimpleNamespace
    args = SimpleNamespace(as_of=AS_OF, carry_sessions=20, trail_start_explicit=False)
    dr.print_carry_header(args, 3, hsw._policy())
    out = capsys.readouterr().out
    for want in ("read-only", "stops dating this position", "FALSE_BREACH", "CARRY_ABOVE_FULL",
                 "SAVED_A_SLIDE", "MISSED", "journal_down_at_read", "HELD_TRAIL_START",
                 "is_stop_breached"):
        assert want in out, want


def test_csv_has_one_row_per_scenario_session_and_a_skipped_row(bleed, tmp_path):
    k = bleed.days[120]
    r = bleed.holding(sessions=25, ks=[k])
    skipped = {"ticker": "ANT", "skipped": "no stop could be built"}
    flat_rows = dr.flatten_carry([r, skipped])
    assert len([x for x in flat_rows if x["record"] == "session"]) == 3 * 25
    assert [x for x in flat_rows if x["record"] == "skipped"] == [
        {"record": "skipped", "ticker": "ANT", "reason": "no stop could be built"}]
    path = tmp_path / "carry.csv"
    dr._write_csv(str(path), flat_rows)
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        got = list(rd)
        header = rd.fieldnames
    for col in ("ticker", "k", "variant", "as_of", "close", "truth_stop", "truth_basis",
                "uncarried_stop", "carried_stop", "carried_minus_truth", "carry_state",
                "carry_code", "truth_breached", "uncarried_breached", "carried_breached",
                "flags", "record", "reason"):
        assert col in header, col
    assert any("SAVED_A_SLIDE" in x["flags"] for x in got if x["record"] == "session")


# -- the script's guarantees ---------------------------------------------------

def test_carry_block_never_touches_a_database_or_an_llm():
    src = _PATH.read_text(encoding="utf-8")
    block = src[src.index("# -- carry mode"):src.index("def _parse_date")]
    for banned in ("db.save", "db.upsert", ".upsert(", ".insert(", "anthropic",
                   "set_readonly(False", "load_manual_stops", "fetch_ticker_bundle",
                   "fetch_live_prices"):
        assert banned not in block, banned


def test_docstring_lists_carry_and_carry_sessions():
    doc = dr.__doc__
    assert "--carry" in doc and "--carry-sessions" in doc
    assert "--carry-sessions 20" in doc                      # the usage line
    assert "GATE: PASS (0 false breaches)" in doc and "journal_down_at_read" in doc


# -- argument parsing ----------------------------------------------------------

def test_carry_arguments_parse_and_carry_mode_is_read_only_and_manual_free(monkeypatch, tmp_path):
    from stock_analyzer import db
    seen = {}
    monkeypatch.setattr(db, "set_readonly", lambda flag: seen.setdefault("ro", flag))
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "load_trades_or_none", lambda: pd.DataFrame(
        columns=["id", "ticker", "action", "shares", "price", "traded_at"]))
    monkeypatch.setattr(db, "load_holdings_or_none", lambda: pd.DataFrame(
        columns=["Ticker", "Shares", "Avg Cost ($)"]))

    def boom():
        raise AssertionError("carry mode must not load manual stops")
    monkeypatch.setattr(db, "load_manual_stops_or_none", boom)

    def fake_run(args, holdings, trades):
        seen["args"] = args
        return [], []
    monkeypatch.setattr(dr, "run_carry", fake_run)
    out = tmp_path / "c.csv"
    rc = dr.main(["--carry", "--carry-sessions", "7", "--tickers", "aapl,abbv",
                  "--csv", str(out)])
    a = seen["args"]
    assert rc == 0 and seen["ro"] is True
    assert a.carry is True and a.carry_sessions == 7 and a.tickers == {"AAPL", "ABBV"}
    assert out.exists()
    seen.clear()
    assert dr.main(["--carry"]) == 0
    assert seen["args"].carry_sessions == dr.CARRY_SESSIONS_DEFAULT
    assert seen["args"].trail_start_explicit is False


@pytest.mark.parametrize("argv,msg", [
    (["--carry", "--today"], "own mode"),
    (["--carry", "--replay"], "own mode"),
    (["--carry", "--floor"], "own mode"),
    (["--carry", "--carry-sessions", "0"], "at least 1"),
    (["--carry", "--as-of", "2020-01-02"], "refused"),
])
def test_carry_argument_refusals(argv, msg, capsys):
    assert dr.main(argv) == 2
    assert msg in capsys.readouterr().out


def test_carry_flag_is_off_by_default_so_existing_modes_are_unchanged(capsys):
    assert dr.main(["--as-of", "2020-01-02"]) == 2          # today mode still refuses a past as-of
    assert "today mode" in capsys.readouterr().out
