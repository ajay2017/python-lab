#!/usr/bin/env python3
"""Measure the REAL on/off "flicker" rate of WATCH/TRIM/EXIT deterioration
cards from live exit_signals history, so the 2026-06-28 hysteresis parking
decision (deferred until "someone eyeballs a flicker") can be revisited
against a MEASURED rate instead of an anecdote. This is Phase 0.5 of
docs/plans/exit-discipline.md's 2026-09-29 hysteresis re-analysis -- see that
doc + memory `project_exit_discipline` for the full design context and the
contingent Phase 1 mechanism spec this measurement would (or wouldn't) unlock.

WHAT THIS MEASURES. `exit_signals` (populated unconditionally by
cron_runner's premarket lane since 2026-07-21) only writes a row when a tier
actually FIRES -- there is no daily "still clear" row. So an absent
(ticker, date) row is ambiguous: genuinely clear that day, OR the day simply
wasn't covered (cron didn't run, or that ticker's own bundle was stale/
uncomputable that run). This script resolves the ambiguity using
`score_history` (written unconditionally, once per HELD ticker per trading
day that ticker's bundle was fresh enough to score, since roadmap B1) as the
"this ticker-day was actually covered" ground truth -- deliberately NOT
`cron_heartbeat`, which keeps only the single LATEST row per lane and cannot
answer "did premarket run on some past date." Only (ticker, date) pairs with
a `score_history` row are placed on that ticker's timeline; every other day
is simply absent from it (neither "active" nor "confirmed clear"), so a real
gap from a position being sold and later re-bought reads as one long
low-confidence span, never a manufactured flicker.

A "gap" = one CLEAR run of confirmed-covered days bounded on both sides by an
ACTIVE tier (WATCH/TRIM/EXIT) -- i.e. exactly the shape a clearing-hysteresis
buffer would suppress, if the gap is short. RISK_OFF signals are excluded --
a portfolio-wide regime overlay, not a per-ticker deterioration tier.

REDLINE. Read-only historical measurement, same posture as
`exit_ladder_replay.py` / `exit_early_cost_analysis.py`. Touches no gate, no
constant, no live recommendation. Produces evidence for the parked
hysteresis decision -- does NOT itself conclude "build it" or "don't"; any
resulting change to `exit_advisor.py`/`constants.py` is a separate `planner`
design pass plus the mandatory Opus `reviewer` (both are `_GATE_FILES`).

HONEST CAVEATS, printed on every run:
  - Coverage-gated: a ticker's timeline only spans dates with a
    `score_history` row -- that table is newer than `exit_signals`
    (roadmap B1 vs 2026-07-21), so the earliest usable window is bounded by
    whichever of the two started later, not by `exit_signals` alone.
  - A short gap is not automatically a defect: a single confirmed-clear day
    between two WATCH days could reflect genuine (if noisy) price action
    re-crossing a floor, not a design flaw -- this script COUNTS gaps, it
    does not judge them or recommend a buffer width.
  - Small-N: normal for tables only a few months old. A thin count is not
    evidence flicker doesn't exist, only that there isn't yet enough history
    to see it -- re-run periodically rather than trusting one pass.
  - Same-tier vs cross-tier gaps are reported separately: a WATCH-clear-WATCH
    round trip is the direct target of the contingent Phase 1 design (scoped
    to WATCH only); a WATCH-clear-TRIM or TRIM-clear-WATCH gap is broader
    tier instability the contingent design does not address.

Requires: Supabase credentials (SUPABASE_URL / SUPABASE_KEY) -- same
hosted-only DB constraint as its siblings (see CLAUDE.md); this project's DB
has no local dev loop. Cannot be run from this coding session -- the owner
runs it directly, or pastes the output back for interpretation. Uses direct
PostgREST reads via `requests`, not the `supabase` SDK, for the same
pyiceberg-wheel reason `exit_ladder_replay.py`/`exit_early_cost_analysis.py`
do.

Usage:
    python scripts/deterioration_flicker_scan.py               # every ticker
    python scripts/deterioration_flicker_scan.py --ticker MU   # one ticker
    python scripts/deterioration_flicker_scan.py --max-gap 5   # widen the "short gap" table (default 3)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_ACTIVE_TIERS = ("WATCH", "TRIM", "EXIT")
_TIER_SEVERITY = {"WATCH": 0, "TRIM": 1, "EXIT": 2}


def _to_date(v) -> date | None:
    if v is None:
        return None
    try:
        return v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _fmt(d: date | None) -> str:
    return d.strftime("%Y-%m-%d") if d else "--"


# ── Pure helpers (unit-testable with synthetic data -- no DB/network) ───────

def build_tier_timelines(
    signals_rows: list[dict], score_history_rows: list[dict],
) -> dict[str, list[tuple[date, str | None]]]:
    """{ticker: [(date, tier_or_None), ...]} sorted ascending, restricted to
    dates that have a `score_history` row for that ticker (the "confirmed
    covered" ground truth -- see module docstring). `tier_or_None` is the
    HIGHEST-severity active tier (WATCH/TRIM/EXIT) recorded for that ticker
    on that date, or `None` if confirmed-covered with no active tier that day.
    RISK_OFF rows are ignored -- not a per-ticker deterioration tier.
    """
    covered: dict[str, set] = {}
    for r in score_history_rows:
        t = str(r.get("ticker", "")).strip().upper()
        d = _to_date(r.get("score_date"))
        if not t or d is None:
            continue
        covered.setdefault(t, set()).add(d)

    tier_by_key: dict[tuple[str, date], str] = {}
    for r in signals_rows:
        sig_type = str(r.get("signal_type", "")).strip().upper()
        if sig_type not in _ACTIVE_TIERS:
            continue
        t = str(r.get("ticker", "")).strip().upper()
        d = _to_date(r.get("signal_date"))
        if not t or d is None:
            continue
        key = (t, d)
        existing = tier_by_key.get(key)
        if existing is None or _TIER_SEVERITY[sig_type] > _TIER_SEVERITY[existing]:
            tier_by_key[key] = sig_type

    timelines: dict[str, list[tuple[date, str | None]]] = {}
    for t, dates in covered.items():
        timeline = sorted((d, tier_by_key.get((t, d))) for d in dates)
        timelines[t] = timeline
    return timelines


def runs_from_timeline(timeline: list[tuple[date, str | None]]) -> list[dict]:
    """Collapse a per-day (date, tier) timeline into consecutive-same-state
    runs: [{"state": tier_or_None, "start": date, "end": date, "n_days": int}].
    `state=None` means confirmed-clear. Adjacent confirmed days only -- a
    timeline entry is only ever a day that WAS confirmed-covered, so a run's
    `n_days` counts confirmed days, not calendar days (a run can legitimately
    span a weekend/holiday gap between two confirmed trading days)."""
    runs: list[dict] = []
    for d, state in timeline:
        if runs and runs[-1]["state"] == state:
            runs[-1]["end"] = d
            runs[-1]["n_days"] += 1
        else:
            runs.append({"state": state, "start": d, "end": d, "n_days": 1})
    return runs


def gaps_from_runs(ticker: str, runs: list[dict]) -> list[dict]:
    """Every CLEAR run bounded on both sides by an ACTIVE run -- the shape a
    clearing-hysteresis buffer would suppress if short enough. A CLEAR run at
    either end of the timeline (nothing before/after it) is not a gap -- there
    is no "return to active" to measure."""
    gaps: list[dict] = []
    for i in range(1, len(runs) - 1):
        run = runs[i]
        if run["state"] is not None:
            continue
        before, after = runs[i - 1], runs[i + 1]
        if before["state"] is None or after["state"] is None:
            continue
        gaps.append({
            "ticker":          ticker,
            "before_tier":     before["state"],
            "after_tier":      after["state"],
            "same_tier":       before["state"] == after["state"],
            "gap_start":       run["start"],
            "gap_end":         run["end"],
            "gap_confirmed_days": run["n_days"],
        })
    return gaps


def summarize_gaps(gaps: list[dict], max_gap: int) -> dict:
    """Bucket counts for the "short gap" table -- 1..max_gap confirmed-days,
    plus a "> max_gap" overflow bucket. Split same-tier (the direct
    hysteresis target) from cross-tier (broader instability, out of the
    contingent design's WATCH-only scope)."""
    buckets = {n: {"same_tier": 0, "cross_tier": 0} for n in range(1, max_gap + 1)}
    overflow = {"same_tier": 0, "cross_tier": 0}
    for g in gaps:
        n = g["gap_confirmed_days"]
        kind = "same_tier" if g["same_tier"] else "cross_tier"
        if n <= max_gap:
            buckets[n][kind] += 1
        else:
            overflow[kind] += 1
    return {"buckets": buckets, "overflow": overflow}


# ── Supabase reads (direct PostgREST -- see module docstring's Requires) ────

def _postgrest_get(table: str, order: str) -> "pd.DataFrame | None":
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/v1/{table}",
            params={"select": "*", "order": order},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        print(f"Could not read the {table} table: {e}")
        return None
    return pd.DataFrame(rows)


def load_exit_signals() -> "pd.DataFrame | None":
    return _postgrest_get("exit_signals", "signal_date.asc")


def load_score_history() -> "pd.DataFrame | None":
    return _postgrest_get("score_history", "score_date.asc")


# ── Report ────────────────────────────────────────────────────────────────

def _print_report(timelines: dict[str, list[tuple[date, str | None]]], max_gap: int) -> None:
    all_covered_days = sum(len(tl) for tl in timelines.values())
    print(
        f"\n{'=' * 78}\n"
        f"HONEST CAVEAT: {len(timelines)} ticker(s), {all_covered_days} confirmed-covered "
        f"ticker-day(s) total\n(score_history coverage, not exit_signals alone -- see module "
        f"docstring). A thin count here is\nnormal for young tables, not evidence flicker "
        f"doesn't exist.\n{'=' * 78}\n"
    )
    if not timelines:
        print("No confirmed-covered ticker-days found. Nothing to measure yet.")
        return

    all_gaps: list[dict] = []
    for t, timeline in sorted(timelines.items()):
        runs = runs_from_timeline(timeline)
        all_gaps.extend(gaps_from_runs(t, runs))

    if not all_gaps:
        print("No ACTIVE-CLEAR-ACTIVE round trips found in the confirmed-covered "
              "history yet -- either no tier has ever cleared and returned, or "
              "there isn't enough history yet to see one.")
        return

    summary = summarize_gaps(all_gaps, max_gap)
    print(f"Gap-length distribution (confirmed-covered days the tier read CLEAR "
          f"before returning ACTIVE):\n")
    header = f"  {'GAP (confirmed days)':<24}{'SAME-TIER':<12}CROSS-TIER"
    print(header)
    for n in range(1, max_gap + 1):
        b = summary["buckets"][n]
        print(f"  {n:<24}{b['same_tier']:<12}{b['cross_tier']}")
    ov = summary["overflow"]
    print(f"  {'> ' + str(max_gap):<24}{ov['same_tier']:<12}{ov['cross_tier']}")

    print(f"\nAll {len(all_gaps)} gap event(s), shortest first "
          f"(shortest = most flicker-like):\n")
    header2 = (f"  {'TICKER':<8}{'BEFORE':<8}{'AFTER':<8}{'GAP START':<12}"
               f"{'GAP END':<12}{'CONFIRMED DAYS':<16}SAME TIER")
    print(header2)
    for g in sorted(all_gaps, key=lambda g: g["gap_confirmed_days"]):
        print(
            f"  {g['ticker']:<8}{g['before_tier']:<8}{g['after_tier']:<8}"
            f"{_fmt(g['gap_start']):<12}{_fmt(g['gap_end']):<12}"
            f"{g['gap_confirmed_days']:<16}{'yes' if g['same_tier'] else 'no'}"
        )

    watch_same_tier = [g for g in all_gaps if g["same_tier"] and g["before_tier"] == "WATCH"]
    print(
        f"\n{'=' * 78}\n"
        f"WATCH-specific same-tier gaps (the contingent Phase 1 design's scope): "
        f"{len(watch_same_tier)}\n"
        f"{'=' * 78}\n"
        "This is evidence for the parked hysteresis decision, not a conclusion -- "
        "see REDLINE\nin the module docstring. A short gap here doesn't by itself "
        "justify building the buffer;\na future planner/reviewer pass would still "
        "decide that, against this measured rate\ninstead of an anecdote.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--ticker", default=None, help="restrict to one ticker")
    parser.add_argument("--max-gap", type=int, default=3,
                         help="widest confirmed-day gap shown in the bucket table (default 3)")
    args = parser.parse_args()
    want = args.ticker.strip().upper() if args.ticker else None

    signals_df = load_exit_signals()
    if signals_df is None:
        print(
            "No exit_signals could be read. Either Supabase credentials "
            "(SUPABASE_URL / SUPABASE_KEY) are not set in this shell's "
            "environment, or the request itself failed -- see any error above."
        )
        return 1

    score_history_df = load_score_history()
    if score_history_df is None:
        print(
            "No score_history could be read. Either Supabase credentials are "
            "not set, or the request itself failed -- see any error above."
        )
        return 1

    signals_rows = signals_df.to_dict("records") if not signals_df.empty else []
    score_history_rows = score_history_df.to_dict("records") if not score_history_df.empty else []

    timelines = build_tier_timelines(signals_rows, score_history_rows)
    if want:
        timelines = {t: tl for t, tl in timelines.items() if t == want}
        if not timelines:
            print(f"No confirmed-covered score_history rows found for {want}.")
            return 0

    _print_report(timelines, args.max_gap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
