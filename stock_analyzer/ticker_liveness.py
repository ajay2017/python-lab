"""
Weekly ticker-liveness sweep for hand-maintained reference rosters.

AWARENESS / CHORE ONLY.  Never gates a recommendation, never suppresses a
pick, never changes a score.  Answers exactly one question: which tickers in
the curated rosters (scanner.SECTOR_UNIVERSE, discovery_universe.DISCOVERY_UNIVERSE,
portfolio._SECTOR_CANDIDATES) have stopped trading since the last human
curation pass?

Runs from the Saturday maintenance cron lane (cron_runner._run_maintenance).
Requires no database access — intentionally placed before the DB sub-jobs so
a Supabase outage cannot starve the roster-rot check.

Offline contract (enforced by check_antipatterns.py):
  None         — the batch download raised; sweep could not run at all.
  "inconclusive" result — batch ran but health below threshold; provider
                 degraded.  This is a RESULT, not an absence; callers must
                 branch on ``is None`` separately from
                 ``result.get("status") == "inconclusive"``.
  "ok" result  — batch healthy; suspects escalated through multi-source layer.

Never uses ``.get(...) or []`` / ``or {}`` patterns — the offline signal must
not be collapsed at any read site here.
"""
from __future__ import annotations

import pandas as pd

from stock_analyzer.constants import TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT

# Wall-clock cap (seconds) on the whole batch screen. OPERATIONAL knob, kept
# module-local rather than in constants.py on the same precedent as
# system_health.py's recency windows — it is not investment policy.
#
# Why it matters here specifically: this sweep is sub-job ⓪, so it runs BEFORE
# the analyst/vol backfills. yfinance exposes no request-level timeout, so a
# TCP-level hang would block until Railway's ~15-min job kill — which kills the
# PROCESS, meaning no heartbeat row and no _notify_failure email either. The
# lane would die silently and starve ① and ② with it. Bounding here is the fix;
# reordering is not, because ⓪ must precede ①'s DB-outage early return.
#
# 180s is deliberately generous against a ~230-ticker batched download (observed
# ~20s) — this is a hang guard, not a latency budget. Distinct from
# DATA_YF_REQUEST_TIMEOUT_SEC (20), which caps ONE bundle request; this caps a
# whole threaded batch.
_SWEEP_WALL_CLOCK_CAP_SEC = 180


def sweep(fetch_batch=None, fetch_live=None) -> dict | None:
    """Check all curated reference rosters for delisted / unknown tickers.

    Parameters
    ----------
    fetch_batch : callable | None
        DI seam for tests.  Signature: ``(tickers: list[str]) -> pd.DataFrame``.
        Default: ``yf.download(tickers, period="5d", auto_adjust=True,
        progress=False, threads=True)``.
    fetch_live : callable | None
        DI seam for tests.  Signature:
        ``(tickers: list[str]) -> dict[str, dict]``.
        Default: ``stock_analyzer.data.fetch_live_prices``.

    Returns
    -------
    dict | None
        ``None``
            The batch download raised — sweep could not run at all.
            Offline sentinel; callers branch on ``is None``.
        ``{"status": "inconclusive", "health_pct": float, "dead": [],
           "suspects_n": int, "roster_n": int}``
            Batch health STRICTLY below ``TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT``.
            The threshold itself is CONCLUSIVE (``health_pct == threshold`` →
            "ok"); only ``<`` is inconclusive.
            Provider likely rate-limited; every verdict would be noise.
        ``{"status": "ok", "health_pct": float, "dead": list,
           "suspects_n": int, "roster_n": int}``
            Batch healthy; suspects escalated through multi-source layer.
            ``dead`` is empty (all alive) or lists confirmed-dead tickers,
            each with the roster file(s) they appear in.
    """
    # ── Defaults ──────────────────────────────────────────────────────────────
    if fetch_batch is None:
        import yfinance as yf

        def fetch_batch(tickers: list[str]) -> pd.DataFrame:
            return yf.download(
                tickers,
                period="5d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )

    if fetch_live is None:
        from stock_analyzer.data import fetch_live_prices as _flp
        fetch_live = _flp

    # ── Build roster with per-ticker membership ───────────────────────────────
    from stock_analyzer import scanner, portfolio
    from stock_analyzer.discovery_universe import DISCOVERY_UNIVERSE

    membership: dict[str, set[str]] = {}
    for tk in (
        t.upper().strip()
        for bucket in scanner.SECTOR_UNIVERSE.values()
        for t in bucket
    ):
        membership.setdefault(tk, set()).add("scanner.py SECTOR_UNIVERSE")
    for tk in (
        t.upper().strip()
        for bucket in DISCOVERY_UNIVERSE.values()
        for t in bucket
    ):
        membership.setdefault(tk, set()).add("discovery_universe.py DISCOVERY_UNIVERSE")
    for tk in (
        t.upper().strip()
        for bucket in portfolio._SECTOR_CANDIDATES.values()
        for t in bucket
    ):
        membership.setdefault(tk, set()).add("portfolio.py _SECTOR_CANDIDATES")

    roster = sorted(membership)
    roster_n = len(roster)

    # ── Batch download ─────────────────────────────────────────────────────────
    # A raised exception here is the ONLY path that returns None (offline
    # sentinel).  "inconclusive" is a result, not an absence.
    # Bounded by a wall-clock cap so a TCP-level hang degrades to the offline
    # sentinel (a chore email) instead of killing the whole lane. Reuses the
    # provider layer's helper, which ABANDONS the worker on breach
    # (shutdown(wait=False)) — a plain `with ThreadPoolExecutor(...)` would
    # block on the hung thread at __exit__ and defeat the timeout entirely.
    try:
        from stock_analyzer.providers.yfinance_provider import _call_with_timeout
        df = _call_with_timeout(fetch_batch, (roster,), {},
                                _SWEEP_WALL_CLOCK_CAP_SEC)
    except Exception:
        return None

    # ── Extract Close column ──────────────────────────────────────────────────
    # yf.download(list_of_tickers, ...) → MultiIndex(field, ticker) columns.
    # df["Close"] yields a DataFrame with tickers as columns.
    # Defensive fallback for the single-ticker edge case (should never happen
    # with ~230 tickers, but keeps the function honest).
    try:
        if isinstance(df.columns, pd.MultiIndex):
            close_df = df["Close"]
        elif "Close" in df.columns:
            # Single-ticker fallback
            close_df = (
                df[["Close"]].rename(columns={"Close": roster[0]})
                if roster
                else pd.DataFrame()
            )
        else:
            close_df = pd.DataFrame()
    except (KeyError, TypeError):
        close_df = pd.DataFrame()

    suspects = [
        t
        for t in roster
        if t not in close_df.columns or close_df[t].dropna().shape[0] == 0
    ]
    suspects_n = len(suspects)
    health_pct = (
        (roster_n - suspects_n) / roster_n * 100.0 if roster_n else 0.0
    )

    # ── Batch-health gate ──────────────────────────────────────────────────────
    # health_pct == TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT is CONCLUSIVE; only
    # strictly below is inconclusive.  Asserted exactly in tests — the
    # 2026-08-04 Critical was an off-by-one of this exact shape, so state the
    # comparison rather than the ambiguous words "inclusive"/"exclusive".
    if health_pct < TICKER_LIVENESS_MIN_BATCH_HEALTH_PCT:
        return {
            "status":     "inconclusive",
            "health_pct": health_pct,
            "dead":       [],
            "suspects_n": suspects_n,
            "roster_n":   roster_n,
        }

    # ── Escalate suspects through multi-source provider layer ─────────────────
    # Expect 0-3 suspects in a healthy run.  A miss across all providers
    # (Finnhub → yfinance → FMP) IS the semantic "unknown symbol" — we do NOT
    # parse Yahoo's 404 (yfinance swallows it; hitting quoteSummary directly
    # depends on an unofficial crumb-gated endpoint, explicitly rejected).
    dead: list[dict] = []
    for t in suspects:
        # The dict access stays INSIDE the try: a provider returning a
        # non-dict payload would otherwise raise AttributeError out of sweep()
        # and be recorded as a lane failure, when the honest reading is
        # "couldn't confirm this one".
        try:
            prices = fetch_live([t])
            if prices is None:
                continue  # whole layer offline for this call → uncertain
            hit = prices.get(t)
            unconfirmed = hit is None or hit.get("price") is None
        except Exception:
            # Provider exception on escalation → uncertain, skip. Deliberately
            # fail-quiet: a false "dead" costs a live name deleted from the
            # roster; a missed dead costs one week.
            continue
        if unconfirmed:
            dead.append(
                {
                    "ticker":  t,
                    "rosters": sorted(membership.get(t, set())),
                }
            )

    return {
        "status":     "ok",
        "health_pct": health_pct,
        "dead":       dead,
        "suspects_n": suspects_n,
        "roster_n":   roster_n,
    }
