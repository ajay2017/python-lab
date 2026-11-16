"""Offline tests for the `--floor` mode of scripts/held_stop_dryrun.py (#26 C3c-2).

Synthetic frames only: no network, no DB, no app import. The mode is a read-only
replay of the EOD floor writer (`held_stop_floor`) over the long frame, then today on
the long frame vs the bundle-only fallback frame (spec:
docs/plans/held-stop-floor-c3c-spec.md section 10 item 2 and section 11).
"""
from __future__ import annotations

import csv
import importlib.util
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_analyzer import constants as C
from stock_analyzer import held_stop as hs
from stock_analyzer import held_stop_floor as hsf
from stock_analyzer import held_stop_wiring as hsw
from stock_analyzer.data import is_trading_day

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "held_stop_dryrun.py"


def _load():
    spec = importlib.util.spec_from_file_location("held_stop_dryrun_floor_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dr = _load()
T = "AAA"
POL = {**hsw._policy(), "trail_start": None}     # constants-driven, no go-live clip
AS_OF = date(2026, 10, 1)                         # a Thursday; the frames end 2026-09-30


# -- builders ------------------------------------------------------------------

def trading_days(end: date, n: int) -> list:
    out, d = [], end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def make_frame(days, closes, tz=None) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    df = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c}, index=idx)
    if tz == "ET":
        df.index = df.index.tz_localize("America/New_York")
    elif tz == "UTC":       # a bar stamped at 00:00 ET, expressed in UTC
        df.index = df.index.tz_localize("America/New_York").tz_convert("UTC")
    return df


def shaped_closes(n=140, e_idx=70, seed=3):
    """Flat ~100, entry at e_idx, a rise to ~108 over 22 sessions (under the first
    ratchet tier), then a slow decline: the trailing stop peaks early, so a shorter
    frame that seeds it later reads LOWER (the #26 defect C3c exists for)."""
    rng = np.random.default_rng(seed)
    c = np.empty(n)
    c[:e_idx] = 100 + rng.normal(0, 0.2, e_idx)
    c[e_idx] = 100.0
    for j in range(1, 23):
        c[e_idx + j] = 100 + 8.0 * j / 22 + rng.normal(0, 0.1)
    rest = n - (e_idx + 23)
    c[e_idx + 23:] = 108 - 9.0 * np.arange(1, rest + 1) / rest + rng.normal(0, 0.1, rest)
    return c


class Scn:
    def __init__(self, n=140, e_idx=70, seed=3, end=date(2026, 9, 30)):
        self.n, self.e_idx = n, e_idx
        self.days = trading_days(end, n)
        self.closes = shaped_closes(n, e_idx, seed)
        self.df = make_frame(self.days, self.closes)
        self.start = self.days[e_idx]
        self.avg = float(self.closes[e_idx])
        self.ep = {"status": "ok", "start": self.start, "first_buy_price": self.avg,
                   "split_ratio": 1.0, "reason": "", "last_buy": self.start}

    def row(self, bundle=None, *, sessions=20, bundle_start=None, live_price=None,
            long_df=None, as_of=AS_OF):
        bundle = self.df.iloc[-80:] if bundle is None else bundle
        return dr.floor_row(T, 10.0, self.avg, self.ep,
                            self.df if long_df is None else long_df, bundle, as_of, POL,
                            sessions=sessions, bundle_start=bundle_start,
                            live_price=live_price)


@pytest.fixture(scope="module")
def sc():
    return Scn()


def adjust(df, before: date, factor: float):
    """A dividend-style adjustment: every bar dated before `before` scaled by factor."""
    out = df.copy()
    m = out.index < pd.Timestamp(before)
    out.loc[m, ["Open", "High", "Low", "Close"]] = out.loc[m, ["Open", "High", "Low", "Close"]] * factor
    return out


# -- the replay loop -----------------------------------------------------------

def test_replay_dates_are_the_last_n_sessions_after_start_and_last_buy_before_as_of(sc):
    ds = dr.replay_dates(sc.df, sc.ep, AS_OF, 5)
    assert ds == sc.days[-5:]
    assert dr.replay_dates(sc.df, sc.ep, AS_OF, 0) == []
    # never a session on or before the episode start, nor on / after as_of
    everything = dr.replay_dates(sc.df, sc.ep, AS_OF, 10_000)
    assert everything[0] == sc.days[sc.e_idx + 1] and everything[-1] == sc.days[-1]
    # a late add: only sessions after the last BUY would carry today's fingerprint
    late = {**sc.ep, "last_buy": sc.days[130]}
    assert dr.replay_dates(sc.df, late, AS_OF, 10_000) == sc.days[131:]
    # a bar dated as_of is today's partial bar and is never a replay session
    assert AS_OF not in dr.replay_dates(
        make_frame(sc.days + [AS_OF], list(sc.closes) + [99.0]), sc.ep, AS_OF, 10_000)
    assert dr.replay_dates(sc.df, {"status": "ok"}, AS_OF, 5) == []


def test_replay_writes_a_row_only_on_full_days_and_keeps_them_in_memory(sc):
    k = 135
    df = sc.df.drop(index=pd.Timestamp(sc.days[k]))      # D = days[k+1] then reads "history ends"
    dates = dr.replay_dates(df, sc.ep, AS_OF, 10)
    rows, log = dr.replay_writer(T, df, sc.ep, sc.avg, dates, POL)
    assert len(log) == 10
    partial = [e for e in log if e["basis"] != "full"]
    assert [e["as_of"] for e in partial] == [sc.days[k + 1]]
    assert all(e["wrote"] == (e["basis"] == "full") for e in log)     # the writer fails closed
    assert sum(e["wrote"] for e in log) == 9
    # one row per (ticker, episode), holding the LAST full day's as_of
    assert list(rows) == [sc.start]
    last_full = [e for e in log if e["wrote"]][-1]["as_of"]
    assert rows[sc.start]["as_of"] == last_full.isoformat()
    assert rows[sc.start]["source"] == hsf.SOURCE_EOD
    # replay never touches a database
    src = _PATH.read_text(encoding="utf-8")
    block = src[src.index("# -- floor mode"):src.index("def _parse_date")]
    for banned in ("db.save", "db.upsert", ".upsert(", ".insert(", "anthropic", "set_readonly(False"):
        assert banned not in block


def test_a_full_day_bind_is_logged_with_its_delta_in_cents(sc, monkeypatch):
    """Spec reviewer ask: log saved - computed when the floor binds on a FULL day."""
    real = hs.compute_held_stop
    target = sc.days[137]

    def lowered(df, ep, avg, as_of, **kw):
        res = real(df, ep, avg, as_of, **kw)
        if as_of == target:
            res = {**res, "stop": round(res["stop"] - 0.07, 2)}
        return res
    monkeypatch.setattr(hs, "compute_held_stop", lowered)
    dates = dr.replay_dates(sc.df, sc.ep, AS_OF, 6)
    _, log = dr.replay_writer(T, sc.df, sc.ep, sc.avg, dates, POL)
    bound = [e for e in log if e["delta"]]
    assert [e["as_of"] for e in bound] == [target]
    assert bound[0]["delta"] == pytest.approx(0.07) and bound[0]["floor_state"] == "applied"
    assert bound[0]["basis"] == "full"
    assert 0 < bound[0]["delta_pct"] < 0.1                      # cents on ~100
    row = sc.row(sessions=6)
    assert row["full_day_binds"] == 1
    assert row["full_day_max_delta"] == pytest.approx(0.07)
    assert str(target) in row["full_day_binds_detail"]
    assert "FLOOR_BOUND_ON_FULL_DAYS" in row["flags"]


def test_normal_full_days_never_bind(sc):
    r = sc.row()
    assert r["replay_sessions"] == 20 and r["replay_full_days"] == 20
    assert r["replay_rows_written"] == 20
    assert r["full_day_binds"] == 0 and r["full_day_max_delta"] == 0.0
    assert r["full_floor_state"] == "not_binding" and r["full_delta"] == 0.0
    assert r["full_basis"] == "full"


# -- a binding fallback --------------------------------------------------------

def test_rolled_bundle_loses_the_entry_anchor_and_the_floor_lifts_it(sc):
    # 80 bars: the entry sits 10 bars in, < 60 warm-up. Priced well above both stops.
    r = sc.row(bundle=sc.df.iloc[-80:], live_price=130.0)
    assert r["fb_basis"] == "partial" and r["fb_anchor_lost"] is True
    assert r["anchor_cover_start"] == sc.days[sc.e_idx - C.HELD_STOP_WARMUP_BARS]
    assert r["fallback_first_bar"] == sc.days[-80]
    assert r["fb_floor_state"] == "applied"
    assert r["fb_delta"] > 0.5
    assert r["fb_floored_stop"] == r["saved_row_stop"]
    assert r["fb_floored_stop"] == pytest.approx(r["full_stop"], abs=0.5)   # about the full figure
    assert r["fb_drop_vs_full"] > 0.5                          # the unfloored fallback fell
    assert r["fb_basis"] == "partial"                          # the floor never changes the basis
    assert "FLOOR_BINDS_FALLBACK" in r["flags"] and "ANCHOR_LOST_ON_FALLBACK" in r["flags"]
    assert "NEWLY_BREACHED_ON_FALLBACK" not in r["flags"]      # the price sits above both stops
    # a bundle that still reaches the ATR origin keeps the anchor and the floor has nothing to do
    whole = sc.row(bundle=sc.df)
    assert whole["fb_anchor_lost"] is False and whole["fb_delta"] == 0.0
    assert whole["fb_floor_state"] == "not_binding"


def test_bundle_start_truncates_the_fallback_frame_and_moves_the_anchor(sc):
    # truncate_frame: inclusive start, tz-aware frames compare on ET session dates
    cut = sc.days[75]
    for tz in (None, "ET", "UTC"):
        f = make_frame(sc.days, sc.closes, tz=tz)
        t = dr.truncate_frame(f, cut)
        assert hs._et_dates(t.index)[0].date() == cut
        assert len(t) == len(sc.days) - 75
    assert dr.truncate_frame(sc.df, None) is sc.df
    assert dr.truncate_frame(None, cut) is None
    # the whole frame keeps the anchor; --bundle-start past the origin bar loses it
    keep = sc.row(bundle=sc.df)
    drop = sc.row(bundle=sc.df, bundle_start=sc.days[11])     # origin bar is days[10]
    still = sc.row(bundle=sc.df, bundle_start=sc.days[10])    # start is inclusive: origin kept
    assert keep["fb_anchor_lost"] is False and still["fb_anchor_lost"] is False
    assert drop["fb_anchor_lost"] is True and drop["fallback_first_bar"] == sc.days[11]
    assert drop["bundle_start"] == sc.days[11]
    # a start after the last bar leaves no fallback frame
    none = sc.row(bundle=sc.df, bundle_start=AS_OF)
    assert none["fb_basis"] == "no_fallback_frame" and "fb_floor_state" not in none


# -- NEWLY_BREACHED_ON_FALLBACK -----------------------------------------------

def test_newly_breached_on_fallback_only_when_the_floor_alone_breaches(sc):
    base = sc.row()
    lo, hi = base["fb_stop"], base["fb_floored_stop"]
    assert hi - lo > 1.0
    mid = sc.row(live_price=round(hi - 0.01, 2))             # below the floored stop, above the raw
    assert mid["price_basis"] == "live"
    assert mid["fb_breached_floored"] is True and mid["fb_breached_unfloored"] is False
    assert "NEWLY_BREACHED_ON_FALLBACK" in mid["flags"]
    both = sc.row(live_price=round(lo - 1.0, 2))              # the unfloored stop breaches too
    assert both["fb_breached_unfloored"] is True and both["fb_breached_floored"] is True
    assert "NEWLY_BREACHED_ON_FALLBACK" not in both["flags"]
    above = sc.row(live_price=round(hi + 1.0, 2))
    assert above["fb_breached_floored"] is False and "NEWLY_BREACHED_ON_FALLBACK" not in above["flags"]
    # the app's own rule: a +0.04% gap rounds to <= 0 and counts as breached
    edge = round(hi * 1.0004, 2)
    assert dr.is_stop_breached(edge, hi) == sc.row(live_price=edge)["fb_breached_floored"]


# -- close-check drift, ref vs bind --------------------------------------------

def test_ref_and_bind_drift_are_reported_separately_with_the_bind_age(sc):
    base = sc.row()
    bind_s, ref_s = base["bind_session"], base["ref_session"]
    assert ref_s == sc.days[-2]       # the last replayed EOD row (D = days[-1]) is built from bars < D
    assert bind_s < ref_s and bind_s >= sc.days[-80]
    bind_idx = sc.days.index(bind_s)
    # dividend-style drift: everything up to and including the bind bar reads 3% lower
    adj = adjust(sc.df.iloc[-80:], bind_s + timedelta(days=1), 0.97)
    r = sc.row(bundle=adj)
    tol = C.HELD_STOP_FLOOR_CLOSE_TOL_PCT
    assert r["tol_pct"] == tol
    assert r["ref_status"] == "within" and r["ref_drift_pct"] == pytest.approx(0.0, abs=1e-9)
    assert r["bind_status"] == "over" and r["bind_drift_pct"] == pytest.approx(-3.0, abs=1e-6)
    assert abs(r["bind_drift_pct"]) > tol
    assert r["ref_age_sessions"] == 1
    assert r["bind_age_sessions"] == sc.n - 1 - bind_idx
    assert "DRIFT_OVER_TOL_BIND" in r["flags"] and "DRIFT_OVER_TOL_REF" not in r["flags"]
    assert r["fb_floor_state"] == "invalid" and r["fb_delta"] == 0.0   # the floor lapses on it
    assert "FLOOR_INVALID_ON_FALLBACK" in r["flags"]
    # the other way round: only the ref bar drifts
    odd = sc.df.iloc[-80:].copy()
    odd.loc[odd.index[-2], ["Open", "High", "Low", "Close"]] *= 1.03
    r2 = sc.row(bundle=odd)
    assert r2["ref_status"] == "over" and r2["ref_drift_pct"] == pytest.approx(3.0, abs=1e-6)
    assert r2["bind_status"] == "within"
    assert "DRIFT_OVER_TOL_REF" in r2["flags"] and "DRIFT_OVER_TOL_BIND" not in r2["flags"]
    # a bind bar older than the frame's first bar is skipped (outside_frame), ref still verifies
    out = sc.row(bundle=sc.df, bundle_start=bind_s + timedelta(days=1))
    assert out["bind_status"] == "outside_frame" and out["ref_status"] == "within"


def test_drift_is_judged_against_the_tolerance_read_at_call_time(sc, monkeypatch):
    bind_s = sc.row()["bind_session"]
    adj = adjust(sc.df.iloc[-80:], bind_s + timedelta(days=1), 0.992)       # a 0.8% dividend
    assert sc.row(bundle=adj)["bind_status"] == "within"
    monkeypatch.setattr(C, "HELD_STOP_FLOOR_CLOSE_TOL_PCT", 0.5)
    r = sc.row(bundle=adj)
    assert r["tol_pct"] == 0.5 and r["bind_status"] == "over"
    assert r["bind_drift_pct"] == pytest.approx(-0.8, abs=1e-6)


def test_close_drift_statuses_in_isolation(sc):
    row = {"ref_session": sc.days[-1].isoformat(), "ref_close": float(sc.closes[-1]),
           "bind_session": sc.days[100].isoformat(), "bind_close": float(sc.closes[100])}
    ok = dr.close_drift(row, sc.df, AS_OF, tol_pct=2.0, long_frame=sc.df)
    assert ok["ref_status"] == ok["bind_status"] == "within"
    assert ok["bind_age_sessions"] == sc.n - 1 - 100
    # a deleted bar INSIDE the span is "missing" (verify_closes fails on it)
    gap = sc.df.drop(index=pd.Timestamp(sc.days[100]))
    assert dr.close_drift(row, gap, AS_OF, tol_pct=2.0)["bind_status"] == "missing"
    assert dr.close_drift(None, sc.df, AS_OF, tol_pct=2.0)["ref_status"] == "no_row"
    assert dr.close_drift(row, None, AS_OF, tol_pct=2.0)["ref_status"] == "no_frame"
    assert dr.close_drift({**row, "ref_close": None}, sc.df, AS_OF, tol_pct=2.0)["ref_status"] == "no_data"
    # at the tolerance: within; a hair over: over (the verify_closes arithmetic)
    exact = {**row, "ref_close": float(sc.closes[-1]) / 1.02}
    assert dr.close_drift(exact, sc.df, AS_OF, tol_pct=2.0 + 1e-9)["ref_status"] == "within"
    assert dr.close_drift(exact, sc.df, AS_OF, tol_pct=1.99)["ref_status"] == "over"
    # the dry run and the production check agree on the same row and frame
    saved = {**row, "stop": 90.0, "as_of": sc.days[-1].isoformat(), "binding": "entry"}
    ok_prod, _ = hsf.verify_closes(saved, sc.df, AS_OF, tol_pct=2.0)
    assert ok_prod is (ok["ref_status"] == ok["bind_status"] == "within")


# -- an unrecorded split ---------------------------------------------------------

def test_unrecorded_two_for_one_split_reads_invalid_and_never_a_breach(sc):
    """The bundle is split-adjusted but the saved closes are not (no SPLIT row in the
    journal): the close check fails and the floor lapses. Applied by mistake it would
    read 'sell all' on a stock that merely split."""
    halved = sc.df.iloc[-80:].copy()
    halved[["Open", "High", "Low", "Close"]] = halved[["Open", "High", "Low", "Close"]] * 0.5
    base = sc.row()
    px = round(base["saved_row_stop"] - 0.5, 2)               # under the SAVED stop, over the halved one
    r = sc.row(bundle=halved, live_price=px)
    assert r["fb_floor_state"] == "invalid" and r["fb_floor_reason"].startswith("history")
    assert r["fb_delta"] == 0.0 and r["fb_floored_stop"] == r["fb_stop"]
    assert "NEWLY_BREACHED_ON_FALLBACK" not in r["flags"]
    assert r["fb_breached_floored"] is False
    assert "FLOOR_INVALID_ON_FALLBACK" in r["flags"]
    assert r["ref_status"] == "over" and r["ref_drift_pct"] == pytest.approx(-50.0, abs=1e-6)
    assert dr.summarize_floor([r])["newly_breached"] == []


# -- U8 -------------------------------------------------------------------------

def test_a_permanently_partial_name_is_listed_under_u8():
    short = Scn(e_idx=40)                      # 40 bars before the entry < the 60-bar warm-up
    r = short.row(bundle=short.df.iloc[-120:])
    assert r["replay_sessions"] > 0 and r["replay_full_days"] == 0
    assert r["replay_rows_written"] == 0 and r["full_basis"] == "partial"
    assert r["u8_permanently_partial"] is True and "U8_PERMANENTLY_PARTIAL" in r["flags"]
    assert r["anchor_cover_start"] is None
    assert r["fb_floor_state"] == "none" and r["fb_delta"] == 0.0     # no row, so no floor
    full = Scn().row()
    assert full["u8_permanently_partial"] is False
    s = dr.summarize_floor([r, full])
    assert s["u8"] == ["AAA"] and s["holdings"] == 2


def test_names_without_an_episode_or_bought_today_never_get_a_floor(sc):
    r = dr.floor_row("BBB", 5.0, 10.0, {"status": "no_history", "reason": "none"}, None, None,
                     AS_OF, POL)
    assert r["no_floor"].startswith("no usable episode") and "NO_FLOOR_NO_EPISODE" in r["flags"]
    today = dr.floor_row("CCC", 5.0, 10.0, {**sc.ep, "start": AS_OF}, sc.df, sc.df, AS_OF, POL)
    assert today["no_floor"].startswith("bought today")
    s = dr.summarize_floor([r, today, sc.row()])
    assert s["floor_possible"] == 1 and [t for t, _ in s["no_floor"]] == ["BBB", "CCC"]


# -- time context ---------------------------------------------------------------

@pytest.mark.parametrize("tz", ["ET", "UTC"])
def test_tz_aware_frames_give_the_naive_result(sc, tz):
    a = sc.row()
    b = sc.row(bundle=make_frame(sc.days, sc.closes, tz=tz).iloc[-80:],
               long_df=make_frame(sc.days, sc.closes, tz=tz))
    for k in ("full_stop", "fb_stop", "fb_floored_stop", "fb_delta", "saved_row_stop",
              "ref_status", "bind_status", "bind_age_sessions", "replay_rows_written"):
        assert a[k] == b[k], k


def test_weekend_visit_replays_through_friday_and_floors_on_the_fallback(sc):
    sat = date(2026, 10, 3)
    assert not is_trading_day(sat) and is_trading_day(date(2026, 10, 2))
    days = trading_days(date(2026, 10, 2), sc.n)
    scn = Scn(end=date(2026, 10, 2))
    assert scn.days == days
    r = scn.row(as_of=sat)
    # Friday's EOD row was built from sessions before Friday: its ref bar is Thursday
    assert r["saved_row_as_of"] == date(2026, 10, 2) and r["ref_session"] == date(2026, 10, 1)
    assert r["full_basis"] == "full" and r["fb_floor_state"] == "applied"
    assert r["ref_age_sessions"] == 1 and r["ref_status"] == "within"


# -- summary, printing, CSV -----------------------------------------------------

def test_summary_counts_binds_states_drift_and_ages(sc):
    bind_s = sc.row()["bind_session"]
    clean = sc.row(live_price=130.0)
    over = sc.row(bundle=adjust(sc.df.iloc[-80:], bind_s + timedelta(days=1), 0.97),
                  live_price=130.0)
    over2 = {**over, "ticker": "BBB"}
    s = dr.summarize_floor([{**clean, "ticker": "AAA"}, over2])
    assert s["fb_binds"] == ["AAA"] and s["fb_states"] == {"applied": 1, "invalid": 1}
    assert s["full_states"] == {"not_binding": 2}
    assert s["bind"]["over"] == 1 and s["bind"]["within"] == 1
    assert s["ref"]["over"] == 0 and s["ref"]["within"] == 2
    assert s["bind"]["max_abs_drift_pct"] == pytest.approx(3.0, abs=1e-6)
    assert len(s["bind"]["ages"]) == 2 and s["anchor_lost"] == ["AAA", "BBB"]
    assert s["full_day_binds_total"] == 0 and s["newly_breached"] == []
    assert dr.summarize_floor([])["holdings"] == 0


def test_print_shows_the_over_under_tolerance_counts_ref_and_bind_apart(sc, capsys):
    r = sc.row()
    dr.print_floor([r])
    dr.print_floor_summary(dr.summarize_floor([r]), 2.0)
    out = capsys.readouterr().out
    for want in ("FALLBACK", "close-check: ref ", "bind ", "OVER tolerance", "REF session",
                 "BIND session", "NEWLY_BREACHED_ON_FALLBACK", "full-day binds (replay)",
                 "cents only", "U8, permanently partial", "HELD_STOP_FLOOR_CLOSE_TOL_PCT",
                 "bar ages"):
        assert want in out, want


def test_header_names_production_policy_and_the_tolerance(capsys):
    from types import SimpleNamespace
    args = SimpleNamespace(as_of=AS_OF, floor_sessions=20, bundle_start=date(2026, 6, 24),
                           trail_start_explicit=False, live=False)
    dr.print_floor_header(args, 4, hsw._policy())
    out = capsys.readouterr().out
    assert "bundle-start=2026-06-24" in out and "HELD_TRAIL_START" in out
    assert f"{float(C.HELD_STOP_FLOOR_CLOSE_TOL_PCT)}%" in out and "read-only" in out


def test_csv_carries_every_column_plus_one_replay_row_per_session(sc, tmp_path):
    r = sc.row(sessions=7)
    flat = dr.flatten_floor([r])
    today = [x for x in flat if x["record"] == "today"]
    replay = [x for x in flat if x["record"] == "replay"]
    assert len(today) == 1 and len(replay) == 7
    assert set(today[0]) == {"record"} | {k for k in r if k != "replay_log"}
    assert today[0]["flags"] == "|".join(r["flags"])
    path = tmp_path / "floor.csv"
    dr._write_csv(str(path), flat)
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        got = list(rd)
        header = rd.fieldnames
    for col in ("fb_floor_state", "fb_delta", "ref_drift_pct", "bind_drift_pct",
                "bind_age_sessions", "ref_age_sessions", "full_day_binds", "full_delta",
                "u8_permanently_partial", "fb_anchor_lost", "anchor_cover_start",
                "fallback_first_bar", "flags", "record", "floor_state", "delta", "wrote"):
        assert col in header, col
    assert len(got) == 8 and got[0]["record"] == "today" and got[1]["record"] == "replay"


# -- argument parsing ------------------------------------------------------------

def test_floor_arguments_parse_and_floor_mode_is_read_only_and_manual_free(monkeypatch, tmp_path):
    from stock_analyzer import db
    seen = {}
    monkeypatch.setattr(db, "set_readonly", lambda flag: seen.setdefault("ro", flag))
    monkeypatch.setattr(db, "has_db", lambda: True)
    monkeypatch.setattr(db, "load_trades_or_none", lambda: pd.DataFrame(
        columns=["id", "ticker", "action", "shares", "price", "traded_at"]))
    monkeypatch.setattr(db, "load_holdings_or_none", lambda: pd.DataFrame(
        columns=["Ticker", "Shares", "Avg Cost ($)"]))

    def boom():
        raise AssertionError("floor mode must not load manual stops")
    monkeypatch.setattr(db, "load_manual_stops_or_none", boom)

    def fake_run(args, holdings, trades):
        seen["args"] = args
        return [], []
    monkeypatch.setattr(dr, "run_floor", fake_run)
    out = tmp_path / "f.csv"
    rc = dr.main(["--floor", "--floor-sessions", "7", "--bundle-start", "2026-06-24",
                  "--tickers", "aapl,abbv", "--csv", str(out)])
    a = seen["args"]
    assert rc == 0 and seen["ro"] is True
    assert a.floor is True and a.floor_sessions == 7
    assert a.bundle_start == date(2026, 6, 24) and a.tickers == {"AAPL", "ABBV"}
    assert out.exists()
    seen.clear()
    assert dr.main(["--floor"]) == 0
    assert seen["args"].floor_sessions == dr.FLOOR_SESSIONS_DEFAULT
    assert seen["args"].bundle_start is None and seen["args"].trail_start_explicit is False


@pytest.mark.parametrize("argv,msg", [
    (["--floor", "--today"], "own mode"),
    (["--floor", "--replay"], "own mode"),
    (["--floor", "--floor-sessions", "0"], "at least 1"),
    (["--floor", "--bundle-start", "2999-01-01"], "must be before"),
    (["--floor", "--as-of", "2020-01-02"], "refused"),
])
def test_floor_argument_refusals(argv, msg, capsys):
    assert dr.main(argv) == 2
    assert msg in capsys.readouterr().out


def test_floor_flag_is_off_by_default_so_existing_modes_are_unchanged(capsys):
    assert dr.main(["--as-of", "2020-01-02"]) == 2          # today mode still refuses a past as-of
    assert "today mode" in capsys.readouterr().out
