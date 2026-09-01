"""Tests for stock_analyzer/ticker_liveness.py and its cron integration.

Contracts being locked:
  1. Batch-health boundary is INCLUSIVE at TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT:
     health_pct == threshold → conclusive; strictly below → inconclusive.
     The 2026-08-04 Critical was an off-by-one of this exact shape.
  2. Provider degradation (rate-limit) → inconclusive, never a dead verdict.
  3. Multi-source rescue: a ticker missing from the batch but returned by the
     live-price layer is NOT reported dead.
  3b. Batched escalation: suspects are escalated via ONE fetch_live(suspects)
      call (not N sequential single-ticker calls), bounded by
      _SWEEP_ESCALATION_CAP_SEC. `None` (offline/timeout) → no dead ticker
      for the whole batch; `{}` (genuinely all unconfirmed) → all dead. A
      malformed per-suspect payload doesn't abort the others.
  4. Confirmed dead path: email sent, `failures` empty, rc == 0.
  5. Ordering guarantee: sweep runs before DB sub-jobs, survives a DB early return.
  6. Sweep exception is isolated: lane failure without suppressing ① or ②.
  7. No email on a fully clean run.
  8. Shelf-status severity split: warn-only → no standalone email; down → email.
  9. sweep=None (batch raised) distinguishable from inconclusive, emails.
"""
import numpy as np
import pandas as pd
import pytest

import scripts.backfill_vol_predictions as bvp
from stock_analyzer import ticker_liveness as _tl
from stock_analyzer.constants import TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT
from stock_analyzer.ticker_liveness import sweep


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_df(alive: list[str], dead: list[str]) -> pd.DataFrame:
    """Build a yf.download-shaped DataFrame: MultiIndex(field, ticker) columns."""
    all_tickers = alive + dead
    dates = pd.date_range("2026-08-11", periods=5)
    tuples = [("Close", t) for t in all_tickers]
    mi = pd.MultiIndex.from_tuples(tuples)
    data: dict = {}
    for t in alive:
        data[("Close", t)] = [100.0, 101.0, 102.0, 103.0, 104.0]
    for t in dead:
        data[("Close", t)] = [np.nan, np.nan, np.nan, np.nan, np.nan]
    df = pd.DataFrame(data, index=dates)
    df.columns = mi
    return df


def _make_rosters(*, tickers: list[str]) -> dict:
    """Build fixture roster kwargs for `sweep()`'s three (now REQUIRED)
    roster params, distributing tickers across the three sources so
    membership tracking is exercised (each ticker appears in exactly one
    roster).

    App Settings (docs/plans/app-settings.md) Commit 3 deleted the
    module-level SECTOR_UNIVERSE/_SECTOR_CANDIDATES/DISCOVERY_UNIVERSE dicts
    this helper used to monkeypatch — `sweep()` now takes these as required
    arguments with no fallback, so this returns a dict meant to be splatted
    into a `sweep(**_make_rosters(...), ...)` call instead.
    """
    n = len(tickers)
    third = max(n // 3, 1)
    s = tickers[:third]
    p = tickers[third: 2 * third]
    d = tickers[2 * third:]

    return {
        "sector_universe":    {"A": s} if s else {},
        "sector_candidates":  {"B": p} if p else {},
        "discovery_universe": {"C": d} if d else {},
    }


def _clean_sweep():
    return {
        "status": "ok",
        "health_pct": 100.0,
        "dead": [],
        "suspects_n": 0,
        "roster_n": 230,
    }


def _clean_shelf():
    return []


def _mk_backfill_ok():
    return {
        "updated": 0, "skipped_count": 0, "pending": 0, "offline": False,
        "rows": 0, "tickers": 0, "skipped": [], "already_done": [],
    }


# `None` is a MEANINGFUL sweep value (the offline sentinel: the batch raised), so
# it cannot double as "argument not supplied" — doing so silently swapped in a
# clean sweep and made the sweep-returned-None test vacuously pass against the
# wrong scenario. Same collapse the offline-sentinel contract exists to prevent,
# reproduced in the harness. Distinct sentinel required.
_UNSET = object()


def _setup_maintenance_lane(monkeypatch, *,
                             sweep_result=_UNSET,
                             shelf_result=_UNSET,
                             analyst_offline=False,
                             analyst_raises=False,
                             vol_raises=False):
    """Drive cron_runner.main() in maintenance mode; return (rc, emails, notified)."""
    import cron_runner as cr
    import stock_analyzer.reference_shelf as _rs
    import stock_analyzer.notify as _notify

    if sweep_result is _UNSET:
        sweep_result = _clean_sweep()
    if shelf_result is _UNSET:
        shelf_result = _clean_shelf()

    emails: list[str] = []   # labels of emails sent via _send_email
    notified: list[str] = [] # details passed to _notify_failure

    monkeypatch.setenv("ALERT_RUN_MODE", "maintenance")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)

    # Sweep sub-job ⓪. Accepts **_kw (not a bare no-args lambda) because
    # App Settings Commit 2 has cron_runner call sweep() with
    # sector_universe=/discovery_universe=/sector_candidates= kwargs now —
    # these tests aren't about that threading (see the dedicated
    # test_maintenance_lane_* tests below for that), so the mock just
    # ignores whatever roster kwargs it's handed.
    monkeypatch.setattr(_tl, "sweep", lambda **_kw: sweep_result)
    monkeypatch.setattr(_rs, "shelf_status", lambda **_kw: shelf_result)
    # Patch the name BOUND IN cron_runner, not the one in notify: cron_runner
    # does `from stock_analyzer.notify import render_liveness_email` at module
    # import, so patching `_notify.render_liveness_email` is a no-op that reads
    # like it works — the real renderer would keep running.
    render_calls: list[dict] = []

    def _fake_render(**kw):
        render_calls.append(kw)
        return ("liveness subj", "<html>liveness</html>")

    monkeypatch.setattr(cr, "render_liveness_email", _fake_render)
    # Exposed for tests that need to assert on what the renderer was handed.
    _setup_maintenance_lane.last_render_calls = render_calls

    monkeypatch.setattr(cr, "_send_email",
                        lambda label, _s, _h: emails.append(label) or True)
    monkeypatch.setattr(cr, "_record_heartbeat", lambda *_a, **_kw: None)
    monkeypatch.setattr(cr, "_notify_failure",
                        lambda _mode, detail: notified.append(detail) or None)
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                        lambda: pd.DataFrame({"Ticker": ["AAPL"]}))

    # Sub-jobs ① and ②
    import scripts.backfill_analyst_prices as bap

    def _analyst(*_a, **_kw):
        if analyst_raises:
            raise RuntimeError("analyst boom")
        return {**_mk_backfill_ok(), "offline": analyst_offline,
                "pending": 1 if not analyst_offline else 0}

    def _vol(*_a, **_kw):
        if vol_raises:
            raise RuntimeError("vol boom")
        return {"rows": 0, "tickers": 0, "skipped": [], "already_done": []}

    monkeypatch.setattr(bap, "run_backfill", _analyst)
    monkeypatch.setattr(bvp, "run_backfill", _vol)

    rc = cr.main()
    return rc, emails, notified


# ── 1. Batch-health boundary — INCLUSIVE ──────────────────────────────────────

def test_health_at_threshold_is_conclusive(monkeypatch):
    """health_pct == TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT must be conclusive.

    Uses 10 tickers, 1 suspect → 90.0% == 90.0% threshold.
    The inclusivity is load-bearing: == threshold IS conclusive (not inconclusive).
    """
    tickers = [f"T{i}" for i in range(1, 11)]  # T1 … T10
    rosters = _make_rosters(tickers=tickers)

    suspect = tickers[-1]  # T10
    alive = [t for t in tickers if t != suspect]
    health = (len(alive) / len(tickers)) * 100.0
    assert health == TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT, (
        "pre-condition: 9/10 = 90.0 must equal the threshold")

    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=[suspect]),
        fetch_live=lambda ts: {},   # dead ticker confirmed by all providers
    )
    assert result is not None
    assert result["status"] != "inconclusive", (
        "health_pct == threshold must be conclusive (inclusive boundary)")
    assert result["status"] == "ok"
    assert abs(result["health_pct"] - 90.0) < 1e-9


def test_health_below_threshold_is_inconclusive(monkeypatch):
    """health_pct strictly below threshold → inconclusive, dead == [].

    Uses 10 tickers, 2 suspects → 80.0% < 90.0% threshold.
    One discrete step below the boundary is sufficient to assert < (not <=).
    """
    tickers = [f"T{i}" for i in range(1, 11)]
    rosters = _make_rosters(tickers=tickers)

    suspects = tickers[-2:]   # T9, T10
    alive = [t for t in tickers if t not in suspects]
    health = (len(alive) / len(tickers)) * 100.0
    assert health < TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT, (
        "pre-condition: 8/10 = 80.0 must be below the threshold")

    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=suspects),
        fetch_live=lambda ts: {},
    )
    assert result is not None
    assert result["status"] == "inconclusive"
    assert result["dead"] == []


# ── 2. Rate-limit / provider degradation ─────────────────────────────────────

def test_rate_limit_50pct_missing_is_inconclusive_not_dead(monkeypatch):
    """50% of roster tickers NaN in the batch → inconclusive, not a dead verdict.

    Validates that `_notify_failure` is not called and rc is unchanged from 0.
    """
    rc, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result={
            "status": "inconclusive",
            "health_pct": 50.0,
            "dead": [],
            "suspects_n": 115,
            "roster_n": 230,
        },
    )
    assert notified == [], "_notify_failure must NOT be called for an inconclusive sweep"
    # The inconclusive result IS an email trigger (not a lane failure)
    assert any("liveness" in e for e in emails), (
        "an inconclusive result must generate a liveness email")
    # rc is not raised by the sweep alone (backfills succeeded)
    assert rc == 0


# ── 3. Multi-source rescue ────────────────────────────────────────────────────

def test_multi_source_rescue_not_dead(monkeypatch):
    """A ticker absent from the batch but returned by fetch_live is NOT dead."""
    tickers = [f"T{i}" for i in range(1, 11)]
    rosters = _make_rosters(tickers=tickers)
    suspect = tickers[-1]
    alive = [t for t in tickers if t != suspect]

    def _live(ts):
        # Multi-source layer has a price for the suspect
        return {ts[0]: {"price": 42.0, "prev_close": None, "change_pct": None,
                        "fetched_at": "2026-08-16T10:00:00"}}

    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=[suspect]),
        fetch_live=_live,
    )
    assert result is not None
    assert result["status"] == "ok"
    dead_tickers = [d["ticker"] for d in result["dead"]]
    assert suspect not in dead_tickers, (
        f"{suspect} was rescued by fetch_live and must not be reported dead")


# ── 3b. Batched escalation — ONE fetch_live(suspects) call, not N ────────────
#
# All tests below use zero-padded ticker names (T01..T30) so lexical sort
# order matches numeric order — `roster = sorted(membership)` inside sweep()
# determines the exact suspects list/order handed to fetch_live, and these
# tests need to predict it exactly.

def _escalation_fixture(n_suspects: int = 3, n_total: int = 30):
    """Build a 30-ticker roster with the LAST `n_suspects` tickers dead in the
    batch download (30 total / 3 suspects = 90.0% health == threshold, the
    inclusive boundary — so the batch-health gate lets escalation run)."""
    tickers = [f"T{i:02d}" for i in range(1, n_total + 1)]
    rosters = _make_rosters(tickers=tickers)
    suspects = tickers[-n_suspects:]
    alive = [t for t in tickers if t not in suspects]
    return tickers, suspects, alive, rosters


def test_escalation_is_one_batched_call_not_n(monkeypatch):
    """3 suspects → fetch_live called exactly ONCE with the full length-3
    list, not 3 times with a length-1 list each."""
    _tickers, suspects, alive, rosters = _escalation_fixture()

    calls: list[list[str]] = []

    def _live(ts):
        calls.append(list(ts))
        return {t: {"price": 1.0} for t in ts}  # rescue all

    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=suspects),
        fetch_live=_live,
    )
    assert result is not None
    assert result["status"] == "ok"
    assert len(calls) == 1, (
        f"fetch_live must be called exactly once for the whole batch, "
        f"was called {len(calls)}x")
    assert calls[0] == suspects, "the single call must carry the FULL suspects list"
    assert result["dead"] == []


def test_escalation_empty_dict_means_all_dead(monkeypatch):
    """fetch_live returning `{}` means EVERY suspect was unconfirmed → all dead.

    This is the regression test for a truthiness-check bug: `{}` is falsy, so
    `if prices:` would wrongly skip the whole batch and produce dead=[]
    instead of the correct all-dead verdict. Only `is not None` correctly
    distinguishes the genuine all-dead result (`{}`) from the offline/timeout
    sentinel (`None`).
    """
    _tickers, suspects, alive, rosters = _escalation_fixture()

    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=suspects),
        fetch_live=lambda ts: {},
    )
    assert result is not None
    assert result["status"] == "ok"
    dead_tickers = {d["ticker"] for d in result["dead"]}
    assert dead_tickers == set(suspects), (
        "an empty-dict fetch_live result must mark EVERY suspect dead — "
        "a truthiness-check regression would instead leave dead == []")


def test_escalation_mixed_rescue(monkeypatch):
    """One batched response can rescue some suspects and confirm others dead
    in the SAME call — only the omitted/nulled ones land in `dead`."""
    _tickers, suspects, alive, rosters = _escalation_fixture()
    rescued, nulled, missing = suspects

    def _live(ts):
        return {
            rescued: {"price": 42.0},
            nulled: {"price": None},
            # `missing` intentionally absent from the payload entirely
        }

    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=suspects),
        fetch_live=_live,
    )
    assert result is not None
    dead_tickers = {d["ticker"] for d in result["dead"]}
    assert dead_tickers == {nulled, missing}
    assert rescued not in dead_tickers


def test_escalation_timeout_breach_is_bounded_and_fails_safe(monkeypatch):
    """A fetch_live that hangs past `_SWEEP_ESCALATION_CAP_SEC` must not hang
    sweep() itself — the wall-clock cap breaches, sweep() returns promptly,
    and the breach fails toward NO dead ticker (uncertain, not confirmed-dead).

    `_slow_live` returns `{}` (all-dead payload) AFTER sleeping, deliberately
    NOT a rescue-all payload — if the bound were silently broken (e.g. a
    future refactor drops `_call_with_timeout`), the call would eventually
    complete and `{}` would mark every suspect dead, so `dead == []` would
    FAIL. A rescue-all payload can't distinguish "bound fired" from "bound
    broken but got lucky" (both give dead == []) — this shape can."""
    import time

    monkeypatch.setattr(_tl, "_SWEEP_ESCALATION_CAP_SEC", 0.05)
    _tickers, suspects, alive, rosters = _escalation_fixture()

    def _slow_live(ts):
        time.sleep(0.5)  # well past the 0.05s cap
        return {}  # all-dead payload — see docstring above for why not rescue-all

    _t0 = time.monotonic()
    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=suspects),
        fetch_live=_slow_live,
    )
    _elapsed = time.monotonic() - _t0
    assert _elapsed < 0.4, (
        f"sweep() took {_elapsed:.2f}s — the escalation cap did not bound it")
    assert result is not None
    assert result["status"] == "ok"
    assert result["dead"] == [], (
        "a timeout breach must fail toward NO dead ticker, not a false dead "
        "(this would fail if the bound were silently broken, since _slow_live "
        "returns an all-dead {} payload once it actually completes)")


def test_escalation_layer_offline_returns_none_no_false_dead(monkeypatch):
    """fetch_live returning None (whole layer offline) → dead == [], and the
    top-level sweep() result stays the normal 'ok' dict — NOT the None
    offline sentinel, which is reserved for a BATCH-DOWNLOAD failure only."""
    _tickers, suspects, alive, rosters = _escalation_fixture()

    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=suspects),
        fetch_live=lambda ts: None,
    )
    assert result is not None, (
        "an escalation-phase None must not be promoted to the top-level "
        "offline sentinel — that is reserved for a batch-download failure")
    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert result["dead"] == []


def test_escalation_fetch_live_raises_is_contained(monkeypatch):
    """fetch_live raising an exception is contained — dead == [], and the
    overall sweep() result is still the normal 'ok' dict shape, not None."""
    _tickers, suspects, alive, rosters = _escalation_fixture()

    def _boom(ts):
        raise RuntimeError("provider exploded")

    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=suspects),
        fetch_live=_boom,
    )
    assert result is not None
    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert result["dead"] == []


def test_escalation_malformed_payload_does_not_abort_others(monkeypatch):
    """A malformed value for ONE suspect (non-dict) must not abort
    classification of the other suspects in the same batched response."""
    _tickers, suspects, alive, rosters = _escalation_fixture()
    t1, t2, t3 = suspects  # t1: malformed, t2: explicit-None price, t3: real price

    def _live(ts):
        return {t1: "not-a-dict", t2: {"price": None}, t3: {"price": 123.45}}

    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=alive, dead=suspects),
        fetch_live=_live,
    )
    assert result is not None
    dead_tickers = {d["ticker"] for d in result["dead"]}
    assert t1 not in dead_tickers, "malformed payload for t1 → uncertain, not dead"
    assert t2 in dead_tickers, "explicit None price → unconfirmed → dead"
    assert t3 not in dead_tickers, "real price → confirmed alive, not dead"


def test_escalation_skipped_when_no_suspects(monkeypatch):
    """0 suspects (all names have price history) → fetch_live is never called
    at all, and dead == []."""
    tickers = [f"T{i:02d}" for i in range(1, 31)]
    rosters = _make_rosters(tickers=tickers)

    called: list[list[str]] = []

    def _live(ts):
        called.append(list(ts))
        return {}

    result = sweep(
        **rosters,
        fetch_batch=lambda _ts: _make_df(alive=tickers, dead=[]),
        fetch_live=_live,
    )
    assert result is not None
    assert result["status"] == "ok"
    assert result["dead"] == []
    assert called == [], "fetch_live must not be called when there are 0 suspects"


# ── 3c. sweep() roster params — App Settings ─────────────────────────────
# `sector_universe`/`discovery_universe`/`sector_candidates` are REQUIRED
# (Commit 3 removed the module-level fallback default entirely) — this pins
# that the passed dicts are what actually gets swept.

def test_sweep_uses_explicit_roster_params(monkeypatch):
    fake_su = {"FakeSU": ["ZZZ1"]}
    fake_du = {"FakeDU": ["ZZZ2"]}
    fake_sc = {"FakeSC": ["ZZZ3"]}

    result = sweep(
        fetch_batch=lambda _ts: _make_df(alive=["ZZZ1", "ZZZ2", "ZZZ3"], dead=[]),
        fetch_live=lambda ts: {},
        sector_universe=fake_su,
        discovery_universe=fake_du,
        sector_candidates=fake_sc,
    )
    assert result is not None
    assert result["roster_n"] == 3, "must sweep ONLY the passed fake rosters, not the real ~230-ticker set"


def test_sweep_empty_roster_params_sweep_nothing_no_fallback(monkeypatch):
    """Explicit {} on all three roster params (the real caller's contract on
    a resolve_universe failure) must sweep literally nothing — there is no
    module-level dict left to fall back to."""
    result = sweep(
        fetch_batch=lambda _ts: pd.DataFrame(),
        fetch_live=lambda ts: {},
        sector_universe={}, discovery_universe={}, sector_candidates={},
    )
    assert result is not None
    assert result["roster_n"] == 0


# ── 3d. cron_runner._run_maintenance threads resolved rosters into sweep() ──
# (App Settings Commit 2) — the liveness sub-job now resolves all three
# rosters via reference_data.resolve_universe_or_none rather than letting
# sweep() read the hardcoded dicts directly.

def _maintenance_env(monkeypatch, cr):
    monkeypatch.setenv("ALERT_RUN_MODE", "maintenance")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)
    monkeypatch.setattr(cr, "render_liveness_email", lambda **_kw: ("subj", "<html/>"))
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_kw: False)
    monkeypatch.setattr(cr, "_record_heartbeat", lambda *_a, **_kw: None)
    monkeypatch.setattr(cr, "_notify_failure", lambda *_a, **_kw: None)
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                        lambda: pd.DataFrame({"Ticker": ["AAPL"]}))
    import scripts.backfill_analyst_prices as bap
    monkeypatch.setattr(bap, "run_backfill",
                        lambda **_kw: {**_mk_backfill_ok(), "pending": 1})
    monkeypatch.setattr(bvp, "run_backfill",
                        lambda **_kw: {"rows": 0, "tickers": 0, "skipped": [], "already_done": []})


def test_maintenance_lane_threads_resolved_rosters_into_sweep(monkeypatch):
    """Fake, distinct rosters (not the real hardcoded dicts) prove the
    maintenance lane's liveness sub-job reads through
    reference_data.resolve_universe_or_none end-to-end."""
    import cron_runner as cr
    import stock_analyzer.reference_shelf as _rs
    from stock_analyzer import reference_data as rd

    fake_su = {"FakeSU": ["ZZZ1"]}
    fake_du = {"FakeDU": ["ZZZ2"]}
    fake_sc = {"FakeSC": ["ZZZ3"]}
    _fakes = {
        "sector_universe": (fake_su, None, None),
        "discovery_universe": (fake_du, None, None),
        "sector_candidates": (fake_sc, None, None),
    }
    monkeypatch.setattr(rd, "resolve_universe_or_none", lambda name: _fakes[name])

    captured: dict = {}

    def _spy_sweep(fetch_batch=None, fetch_live=None, sector_universe=None,
                   discovery_universe=None, sector_candidates=None):
        captured["sector_universe"] = sector_universe
        captured["discovery_universe"] = discovery_universe
        captured["sector_candidates"] = sector_candidates
        return _clean_sweep()

    monkeypatch.setattr(_tl, "sweep", _spy_sweep)
    monkeypatch.setattr(_rs, "shelf_status", lambda **_kw: [])
    _maintenance_env(monkeypatch, cr)

    cr.main()

    assert captured["sector_universe"] == fake_su
    assert captured["discovery_universe"] == fake_du
    assert captured["sector_candidates"] == fake_sc


def test_maintenance_lane_unavailable_roster_degrades_to_empty_dict_not_fallback(monkeypatch):
    """An unavailable roster resolution must pass {} into sweep(), never a
    bare None — sweep()'s roster params are required with no fallback at
    all, so passing None through would raise inside sweep() rather than
    honestly sweeping zero names for that roster. Also must NOT abort the
    maintenance lane — this is a chore/awareness check, not a decision
    path."""
    import cron_runner as cr
    import stock_analyzer.reference_shelf as _rs
    from stock_analyzer import reference_data as rd

    monkeypatch.setattr(rd, "resolve_universe_or_none", lambda name: (None, None, "boom"))

    captured: dict = {}

    def _spy_sweep(fetch_batch=None, fetch_live=None, sector_universe=None,
                   discovery_universe=None, sector_candidates=None):
        captured["sector_universe"] = sector_universe
        captured["discovery_universe"] = discovery_universe
        captured["sector_candidates"] = sector_candidates
        return _clean_sweep()

    monkeypatch.setattr(_tl, "sweep", _spy_sweep)
    monkeypatch.setattr(_rs, "shelf_status", lambda **_kw: [])
    _maintenance_env(monkeypatch, cr)

    cr.main()

    assert captured["sector_universe"] == {}
    assert captured["discovery_universe"] == {}
    assert captured["sector_candidates"] == {}
    assert cr._LAST_LANE_FAILURE_DETAIL is None, (
        "a roster-resolution failure for this chore must not be reported as "
        "a maintenance lane failure"
    )


def test_run_maintenance_source_no_longer_reads_rosters_directly():
    """Literal import-isolation check, complementing the behavioral proofs
    above: asserts against the real source text of the real function that
    the liveness sub-job no longer reads SECTOR_UNIVERSE/DISCOVERY_UNIVERSE/
    _SECTOR_CANDIDATES directly — this is the exact class of mistake a prior
    review caught elsewhere in this project (an import-isolation test missed
    the real file/function)."""
    import inspect
    import cron_runner as cr

    src = inspect.getsource(cr._run_maintenance)
    for _name in ("SECTOR_UNIVERSE", "DISCOVERY_UNIVERSE", "_SECTOR_CANDIDATES"):
        assert _name not in src, (
            f"_run_maintenance must resolve rosters via "
            f"reference_data.resolve_universe_or_none, never read {_name} directly"
        )
    assert "resolve_universe_or_none" in src


# ── 4. Confirmed-dead path ────────────────────────────────────────────────────

def test_confirmed_dead_emails_not_lane_failure(monkeypatch):
    """One confirmed-dead ticker → liveness email sent; failures empty; rc == 0;
    _LAST_LANE_FAILURE_DETAIL remains None (not a maintenance lane failure)."""
    import cron_runner as cr

    rc, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result={
            "status": "ok",
            "health_pct": 99.0,
            "dead": [{"ticker": "CFLT",
                      "rosters": ["scanner.py SECTOR_UNIVERSE"]}],
            "suspects_n": 1,
            "roster_n": 231,
        },
    )
    assert any("liveness" in e for e in emails), "email must be sent for a dead ticker"
    assert notified == [], "a dead ticker is a chore, not a lane failure"
    assert rc == 0, "dead ticker must not set rc=1"
    # _LAST_LANE_FAILURE_DETAIL drives the 🩺 System Trust heartbeat — must stay None
    assert cr._LAST_LANE_FAILURE_DETAIL is None


# ── 5. Sweep runs before DB sub-jobs ─────────────────────────────────────────

def test_sweep_runs_before_db_early_return(monkeypatch):
    """With has_db() False the analyst backfill early-returns, but the sweep
    must already have run before that early return fires."""
    import cron_runner as cr
    import stock_analyzer.reference_shelf as _rs
    import stock_analyzer.notify as _notify

    called: list[str] = []

    def _sweep_spy(**_kw):
        called.append("sweep")
        return _clean_sweep()

    monkeypatch.setenv("ALERT_RUN_MODE", "maintenance")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)

    monkeypatch.setattr(_tl, "sweep", _sweep_spy)
    monkeypatch.setattr(_rs, "shelf_status", lambda **_kw: [])
    # Patch the name bound in cron_runner, not notify — see the note in
    # _setup_maintenance_lane. Inert here (this sweep is clean, so nothing is
    # rendered), but patching `_notify` would be a silent no-op and a landmine
    # for the next edit.
    monkeypatch.setattr(cr, "render_liveness_email",
                        lambda **_kw: ("subj", "<html/>"))

    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_kw: False)
    monkeypatch.setattr(cr, "_record_heartbeat", lambda *_a, **_kw: None)
    monkeypatch.setattr(cr, "_notify_failure", lambda *_a, **_kw: None)

    # DB is unavailable — analyst backfill triggers the early DB-return path
    monkeypatch.setattr(cr, "_handle_db_unavailable", lambda *_a, **_kw: 1)

    import scripts.backfill_analyst_prices as bap
    monkeypatch.setattr(
        bap, "run_backfill",
        lambda **_kw: {**_mk_backfill_ok(), "offline": True},
    )

    cr.main()

    assert "sweep" in called, (
        "sweep (sub-job ⓪) must execute before the DB early-return in sub-job ①")


# ── 6. Sweep exception is isolated ───────────────────────────────────────────

def test_sweep_exception_is_contained(monkeypatch):
    """An exception inside the sweep lands in `failures` (lane failure) and
    the analyst/vol backfills still run afterward."""
    import stock_analyzer.reference_shelf as _rs

    ran: list[str] = []

    def _boom(**_kw):
        raise RuntimeError("simulated sweep failure")

    monkeypatch.setattr(_tl, "sweep", _boom)
    monkeypatch.setattr(_rs, "shelf_status", lambda **_kw: [])

    import scripts.backfill_analyst_prices as bap

    def _analyst(*_a, **_kw):
        ran.append("analyst")
        return {**_mk_backfill_ok(), "pending": 1}

    def _vol(*_a, **_kw):
        ran.append("vol")
        return {"rows": 0, "tickers": 0, "skipped": [], "already_done": []}

    import cron_runner as cr
    monkeypatch.setenv("ALERT_RUN_MODE", "maintenance")
    monkeypatch.setenv("ALERT_FORCE", "1")
    monkeypatch.delenv("ALERT_TEST_EMAIL", raising=False)
    monkeypatch.setattr(cr, "_send_email", lambda *_a, **_kw: False)
    monkeypatch.setattr(cr, "_record_heartbeat", lambda *_a, **_kw: None)
    notified: list[str] = []
    monkeypatch.setattr(cr, "_notify_failure",
                        lambda _m, detail: notified.append(detail))
    monkeypatch.setattr(cr.db, "has_db", lambda: True)
    monkeypatch.setattr(cr.db, "load_holdings_or_none",
                        lambda: pd.DataFrame({"Ticker": ["AAPL"]}))
    monkeypatch.setattr(bap, "run_backfill", _analyst)
    monkeypatch.setattr(bvp, "run_backfill", _vol)

    rc = cr.main()

    assert rc == 1, "a sweep exception must mark the lane as failed"
    assert any("liveness" in d for d in notified), (
        "_notify_failure must be called with the sweep exception detail")
    assert "analyst" in ran, "analyst backfill must still run after the sweep exception"
    assert "vol" in ran, "vol backfill must still run after the sweep exception"


# ── 7. No email on a clean run ────────────────────────────────────────────────

def test_no_email_on_clean_run(monkeypatch):
    """All tickers alive, no shelf issues → no liveness email sent."""
    rc, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result=_clean_sweep(),
        shelf_result=[
            {"key": "sector_universe", "severity": "ok",
             "label": "Grow Today scan universe", "location": "scanner.py",
             "detail": "last refreshed ...", "consequence": ""},
        ],
    )
    liveness_emails = [e for e in emails if "liveness" in e]
    assert liveness_emails == [], (
        "no liveness email should be sent when the sweep is clean and no shelf is down")


# ── 8. Shelf-status severity split ───────────────────────────────────────────

def test_shelf_warn_only_no_standalone_email(monkeypatch):
    """warn-only shelf row does NOT trigger a standalone email when sweep is clean."""
    _, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result=_clean_sweep(),
        shelf_result=[
            {"key": "sector_universe", "severity": "warn",
             "label": "Grow Today scan universe", "location": "scanner.py",
             "detail": "95 days old (refresh every 90d)", "consequence": ""},
        ],
    )
    liveness_emails = [e for e in emails if "liveness" in e]
    assert liveness_emails == [], (
        "a warn-only shelf row must not trigger a standalone liveness email")


def test_shelf_down_triggers_email(monkeypatch):
    """A shelf row with severity == 'down' triggers a liveness email on its own
    even when the sweep is completely clean."""
    _, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result=_clean_sweep(),
        shelf_result=[
            {"key": "macro_event_calendar", "severity": "down",
             "label": "Macro event calendar backbone", "location": "macro_calendar.py",
             "detail": "EXPIRED 2026-07-01 — 46d ago; extend it now",
             "consequence": "macro Act-Today items stop firing"},
        ],
    )
    liveness_emails = [e for e in emails if "liveness" in e]
    assert liveness_emails, (
        "a shelf row with severity == 'down' must trigger a liveness email")
    assert notified == [], "an expired shelf table is a chore, not a lane failure"


# ── 9. sweep=None (batch raised) ─────────────────────────────────────────────

def test_sweep_none_emails_and_is_distinguishable_from_inconclusive(monkeypatch):
    """sweep=None (batch exception) emails and is distinguishable from inconclusive.

    The email content must explicitly state there is no verdict and why —
    'silence is not health' (reference_shelf.py docstring principle).
    """
    _, emails, notified = _setup_maintenance_lane(
        monkeypatch,
        sweep_result=None,   # batch raised → sweep returns None
    )
    # Must send an email for a None sweep
    liveness_emails = [e for e in emails if "liveness" in e]
    assert liveness_emails, (
        "sweep=None (batch raised) must trigger a liveness email")
    assert notified == [], "sweep=None is not a lane failure"

    # The renderer must receive the None sentinel ITSELF, not a coerced empty
    # dict — that distinction is what lets the email say "no verdict this week"
    # instead of falsely reporting a clean sweep.
    received = _setup_maintenance_lane.last_render_calls
    assert received, "render_liveness_email must be called"
    assert received[0]["sweep"] is None, (
        f"renderer got {received[0]['sweep']!r}, not the None offline sentinel")
    assert received[-1]["sweep"] is None, (
        "render_liveness_email must receive sweep=None, not the inconclusive dict")


# ── render_liveness_email unit tests ─────────────────────────────────────────

def test_render_liveness_email_none_sweep():
    """render_liveness_email with sweep=None includes 'no verdict' headline."""
    from stock_analyzer.notify import render_liveness_email
    subj, html = render_liveness_email(
        sweep=None, shelf_down=[], shelf_warn=[], built_at="2026-08-16T08:00:00"
    )
    assert "no verdict" in subj.lower() or "failed" in subj.lower()
    assert "could not run" in html or "no verdict" in html.lower()


def test_render_liveness_email_inconclusive():
    """render_liveness_email with inconclusive sweep includes 'inconclusive'."""
    from stock_analyzer.notify import render_liveness_email
    subj, html = render_liveness_email(
        sweep={
            "status": "inconclusive",
            "health_pct": 50.0,
            "dead": [],
            "suspects_n": 115,
            "roster_n": 230,
        },
        shelf_down=[], shelf_warn=[], built_at="2026-08-16T08:00:00",
    )
    assert "inconclusive" in subj.lower()
    assert "inconclusive" in html.lower()


def test_render_liveness_email_dark_on_light():
    """The email must be dark-on-light (no near-white text on a dark background)
    because email clients strip <body> styling — verified live on 2026-08-16."""
    from stock_analyzer.notify import render_liveness_email
    _, html = render_liveness_email(
        sweep={
            "status": "ok",
            "health_pct": 99.0,
            "dead": [{"ticker": "DEAD", "rosters": ["scanner.py SECTOR_UNIVERSE"]}],
            "suspects_n": 1,
            "roster_n": 230,
        },
        shelf_down=[], shelf_warn=[], built_at="2026-08-16T08:00:00",
    )
    # Near-white text colours that are invisible on a white background
    for invisible in ("#f9fafb", "#e5e7eb", "#a8a29e", "#d6d3d1"):
        assert invisible not in html, (
            f"{invisible} is unreadable on a white background")
    # Dark page background must NOT be on <body> (clients strip it)
    assert "background:#0c0a09" not in html


def test_render_liveness_email_dead_ticker_escaping():
    """Ticker names in dead list are HTML-escaped (no XSS via crafted names)."""
    from stock_analyzer.notify import render_liveness_email
    _, html = render_liveness_email(
        sweep={
            "status": "ok",
            "health_pct": 99.0,
            "dead": [{"ticker": "<script>alert(1)</script>",
                      "rosters": ["scanner.py SECTOR_UNIVERSE"]}],
            "suspects_n": 1,
            "roster_n": 230,
        },
        shelf_down=[], shelf_warn=[], built_at="2026-08-16T08:00:00",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
