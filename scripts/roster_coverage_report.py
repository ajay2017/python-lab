#!/usr/bin/env python3
"""Refresh aid for the three hand-maintained ticker rosters.

Turns the ad-hoc querying that the 2026-08-16 SECTOR_UNIVERSE refresh needed
(market caps, coverage gaps, liveness) into one repeatable command, so the next
refresh spends its time on the JUDGMENT and not on re-deriving the facts.

Rosters covered:
  scanner.SECTOR_UNIVERSE          the daily Grow Today scan net
  discovery_universe.DISCOVERY_UNIVERSE  the wider Movers net
  portfolio._SECTOR_CANDIDATES     Diversification Advisor ADD suggestions

REPORTING ONLY — reads nothing from the DB, writes nothing, and is imported by
nothing. It never decides which names belong; it lays out the evidence a human
uses to decide. Deliberately NOT wired into CI: "this roster could be better"
is a judgment call, not a pass/fail condition, and the two mechanical questions
that ARE pass/fail already have owners — staleness via reference_shelf.py's
🩺 System Trust check ⑤, and ticker rot via the weekly ticker_liveness sweep.

Usage:
  python scripts/roster_coverage_report.py              # all rosters
  python scripts/roster_coverage_report.py --caps       # + market caps (slow: one call per ticker)
  python scripts/roster_coverage_report.py --roster candidates

Needs network (yfinance). Not part of the test suite and not run by any hook.
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Highlight threshold for "worth a second look given what this roster is FOR".
# A REPORTING cue only — it filters nothing, gates nothing, and is deliberately
# NOT in constants.py, because it is not an investment threshold: no code reads
# it to make a decision. Set where it surfaces the small/mid-caps a human should
# eyeball, not where it draws a policy line.
_THIN_CAP_B = 60.0


def _rosters() -> dict[str, dict[str, list[str]]]:
    from stock_analyzer.scanner import SECTOR_UNIVERSE
    from stock_analyzer.discovery_universe import DISCOVERY_UNIVERSE
    from stock_analyzer.portfolio import _SECTOR_CANDIDATES
    return {
        "scan":       SECTOR_UNIVERSE,
        "discovery":  DISCOVERY_UNIVERSE,
        "candidates": _SECTOR_CANDIDATES,
    }


def _flat(roster: dict[str, list[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for names in roster.values():
        for t in names:
            tu = str(t).upper().strip()
            if tu and tu not in seen:
                seen.add(tu)
                out.append(tu)
    return sorted(out)


def _market_caps(tickers: list[str]) -> dict[str, float | None]:
    """Per-ticker market cap in $B. One call each — slow, hence opt-in."""
    import yfinance as yf
    caps: dict[str, float | None] = {}
    for t in tickers:
        try:
            raw = yf.Ticker(t).fast_info.get("marketCap")
            caps[t] = round(raw / 1e9, 1) if raw else None
        except Exception:
            caps[t] = None
    return caps


def _shelf() -> None:
    from stock_analyzer.reference_shelf import shelf_status
    print("\n=== Shelf status (same source as 🩺 System Trust check ⑤) ===")
    for row in shelf_status():
        print(f"  {row['severity'].upper():5}  {row['label']:38} {row['detail']}")


def report(which: str | None, with_caps: bool) -> None:
    rosters = _rosters()
    caps: dict[str, float | None] = {}
    if with_caps:
        # Scope the fetch to what will actually be printed. --roster candidates
        # is 56 calls; fetching all three rosters would be ~250 for no gain.
        # The scan report also needs the discovery names for its gap section.
        wanted = set(rosters) if not which else (
            {"scan", "discovery"} if which == "scan" else {which})
        every = sorted({t for k, r in rosters.items() if k in wanted
                        for t in _flat(r)})
        print(f"Fetching market caps for {len(every)} tickers (one call each)...")
        caps = _market_caps(every)

    for name, roster in rosters.items():
        if which and which != name:
            continue
        flat = _flat(roster)
        print(f"\n=== {name}: {len(roster)} buckets, {len(flat)} unique tickers ===")
        for bucket, names in roster.items():
            line = f"  {bucket:28} n={len(names):<3}"
            if with_caps:
                vals = [caps.get(t) for t in names]
                known = [v for v in vals if v is not None]
                # Median, not mean: one mega-cap would otherwise mask a bucket
                # of small names, which is the exact shape being looked for.
                if known:
                    srt = sorted(known)
                    mid = srt[len(srt) // 2] if len(srt) % 2 else (
                        (srt[len(srt) // 2 - 1] + srt[len(srt) // 2]) / 2)
                    thin = [f"{t} ${c}B" for t, c in
                            ((t, caps.get(t)) for t in names)
                            if c is not None and c < _THIN_CAP_B]
                    line += f" median=${mid:>7.1f}B"
                    if thin:
                        line += f"  under-$60B: {', '.join(thin)}"
            print(line)

    # Coverage gap: names the wider curated net carries that the daily scan
    # doesn't. This is the "what are we not looking at?" question — it does NOT
    # mean they should be added (2026-08-16: six large absent semiconductors
    # were left out ON PURPOSE, because adding them would have undone the tech
    # rebalance that refresh existed to achieve).
    if not which or which == "scan":
        scan = set(_flat(rosters["scan"]))
        disc = set(_flat(rosters["discovery"]))
        missing = sorted(disc - scan)
        print(f"\n=== In discovery but NOT in the daily scan ({len(missing)}) ===")
        if with_caps:
            ranked = sorted(missing, key=lambda t: -(caps.get(t) or 0))
            for t in ranked[:25]:
                print(f"  {t:6} ${caps.get(t) if caps.get(t) is not None else '?'}B")
            print(f"  ... {max(0, len(ranked) - 25)} more")
        else:
            print("  " + ", ".join(missing))
            print("  (re-run with --caps to rank these by size)")

    _shelf()
    print("\nLiveness is NOT checked here — the weekly ticker_liveness sweep "
          "owns that and emails on a finding. See CLAUDE.md.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roster", choices=["scan", "discovery", "candidates"],
                    help="limit to one roster")
    ap.add_argument("--caps", action="store_true",
                    help="fetch market caps (slow: one network call per ticker)")
    args = ap.parse_args()
    try:
        report(args.roster, args.caps)
    except Exception as exc:                      # reporting tool: never traceback at a human
        print(f"roster_coverage_report failed: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
