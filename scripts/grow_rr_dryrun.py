#!/usr/bin/env python3
"""Dry-run: how many Grow Today picks / adds would a G-32 R:R >= RR_ENTRY_MIN gate hold?

G-32 chunk C1 (docs/plans/grow-today-rr-gate.md). Read-only diagnostic: nothing
is wired, nothing is enforced. Measures the unrounded entry R:R
(`stock_analyzer.entry_rr`) on the app's own recommendation rows and reports how
many would have been held, split by lane (pick / add / overflow), by R:R class
and by target `base_source`.

No user-data writes (`db.set_readonly(True)`); load_bundle may write through system
caches (fundamentals / sector cache) exactly as the app would. Makes no LLM calls
(ANTHROPIC_API_KEY is dropped before any app import, only when run as a script).
Requires Supabase credentials in the environment (SUPABASE_URL / SUPABASE_KEY),
the same ones Railway uses -- run from a normal shell. Fetches market data via
the app's own providers.

Modes:
  retro (default)  `recommendations` rows with rec_type new_pick / add_winner /
                   overflow_pick over the last N trading days. Per row: entry =
                   price_at_surface; ATR and technical levels from price history
                   strictly BEFORE rec_date (no look-ahead); stop = entry -
                   ATR_STOP_MULT x ATR (bundle basis; the secondary column uses
                   the row's rec_stop when present); target = compute_price_targets
                   on the truncated history with the chooser's analyst target.
  --today          near-exact: compute_morning_picks on the persisted scanner_cache,
                   then entry_rr.from_bundle on the reloaded bundle of each pick
                   (res["picks"]), add (res["grow"]["add_positions"]) and overflow
                   name (res["grow"]["overflow_picks"]). Near-exact, not exact,
                   because bundles are re-fetched now, not as of the scan.

Usage:
    python scripts/grow_rr_dryrun.py --days 30
    python scripts/grow_rr_dryrun.py --days 30 --csv rr_rows.csv
    python scripts/grow_rr_dryrun.py --tickers MU,OXY
    python scripts/grow_rr_dryrun.py --today

Known approximations (marked, not guessed):
  * Analyst target: that day's `analyst_target_snapshots` row when one exists
    (target_basis "snapshot", held tickers only), else TODAY's bundle
    financials analyst_target / num_analyst_opinions ("approx_today").
  * choose_pt_target is called with provider_source_key=None, as_of=rec_date,
    cached=False and saved_targets=[] (no saved-research fallback replayed).
  * Price history is loaded with period "1y" today and cut to bars strictly
    before rec_date. The 52-week high/low use that whole cut (up to 252 bars);
    the ATR and compute_price_targets (whose momentum candidate averages the
    WHOLE frame) get only bars in [rec_date - 6 calendar months, rec_date),
    mirroring the live period="6mo" bundle.
  * entry = price_at_surface (first-writer-wins in the rec rows); the retro stop
    is recomputed from history, not the bundle stop the app saw that day.
  * add_winner rows exist only on days the owner opened Home on a bull day.
  * Retro rows carry no mover flag (the recommendation rows do not expose it);
    --today records is_mover from the pick dict when present.
  * --today bundles are re-fetched now, so a price may differ from the scan.
"""
from __future__ import annotations

import os

if __name__ == "__main__":
    os.environ.pop("ANTHROPIC_API_KEY", None)   # no LLM spend, before any app import

import argparse  # noqa: E402
import csv  # noqa: E402
import sys  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import entry_rr  # noqa: E402
from stock_analyzer.constants import (  # noqa: E402
    ATR_STOP_MULT, RR_ENTRY_MIN, VALUATION_PT_MAX_TARGET_RATIO, VALUATION_PT_MIN_ANALYSTS,
)
from stock_analyzer.rr_readout import fmt_ratio_vs  # noqa: E402

# Readout band only, not a gate: "near misses" are measured R:R in [floor, RR_ENTRY_MIN).
_NEAR_MISS_FLOOR = 1.5

_LANES = {"new_pick": "pick", "add_winner": "add", "overflow_pick": "overflow"}
_CLASSES = ("pass", "below", "no_upside", "unknown")


def _f(v):
    try:
        x = float(v)
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


# -- pure summary -------------------------------------------------------------

def summarize(rows: list[dict]) -> dict:
    """Aggregate per-row results. Rows with a truthy "skip" are counted as
    skips only. Row keys used: date, lane, cls, rr, rr_old, base_source,
    cls_lane (class on the lane's own stop basis, may be absent / None)."""
    skips: Counter = Counter()
    lanes: dict = {}
    for lane in ("pick", "add", "overflow"):
        lanes[lane] = {
            "shown": 0, "would_hold": 0,
            "by_class": {c: 0 for c in _CLASSES},
            "all_by_base_source": {}, "held_by_base_source": {},
            "near_misses": 0, "rounding_disagree": 0,
            "basis_flips": 0, "basis_compared": 0,
            "zero_pass_days": 0, "days": 0,
            "by_target_basis": {}, "by_asset_type": {},
            "movers": {"mover": {"shown": 0, "would_hold": 0},
                       "curated": {"shown": 0, "would_hold": 0}},
        }
    day_pass: dict = {}
    for r in rows:
        if r.get("skip"):
            skips[r["skip"]] += 1
            continue
        lane = r.get("lane")
        if lane not in lanes:
            skips["unknown_lane"] += 1
            continue
        L = lanes[lane]
        cls = r.get("cls") if r.get("cls") in _CLASSES else "unknown"
        src = r.get("base_source") or "none"
        L["shown"] += 1
        L["by_class"][cls] += 1
        L["all_by_base_source"][src] = L["all_by_base_source"].get(src, 0) + 1
        if cls != "pass":
            L["would_hold"] += 1
            L["held_by_base_source"][src] = L["held_by_base_source"].get(src, 0) + 1
        for key, split in (("target_basis", "by_target_basis"), ("asset_type", "by_asset_type")):
            v = r.get(key)
            if v:
                d = L[split].setdefault(v, {"shown": 0, "would_hold": 0})
                d["shown"] += 1
                d["would_hold"] += cls != "pass"
        if lane == "pick" and r.get("is_mover") is not None:
            d = L["movers"]["mover" if r["is_mover"] else "curated"]
            d["shown"] += 1
            d["would_hold"] += cls != "pass"
        rr = r.get("rr")
        if cls == "below" and rr is not None and _NEAR_MISS_FLOOR <= rr < RR_ENTRY_MIN:
            L["near_misses"] += 1
        rr_old = r.get("rr_old")
        if rr_old is not None and rr_old >= RR_ENTRY_MIN and cls != "pass":
            L["rounding_disagree"] += 1
        cl = r.get("cls_lane")
        if cl is not None:
            L["basis_compared"] += 1
            if cl != cls:
                L["basis_flips"] += 1
        key = (lane, r.get("date"))
        day_pass[key] = day_pass.get(key, False) or cls == "pass"
    for (lane, _d), any_pass in day_pass.items():
        lanes[lane]["days"] += 1
        if not any_pass:
            lanes[lane]["zero_pass_days"] += 1
    # days with >=1 pick row where neither a pick nor an overflow name passes
    pick_days = {d for (ln, d) in day_pass if ln == "pick"}
    no_pass_pick_overflow = sum(
        1 for d in pick_days
        if not day_pass[("pick", d)] and not day_pass.get(("overflow", d), False))
    lanes["overflow"]["would_fill_slot"] = lanes["overflow"]["by_class"]["pass"]
    return {"lanes": lanes, "skips": dict(skips), "skipped": sum(skips.values()),
            "zero_pass_pick_and_overflow_days": no_pass_pick_overflow,
            "pick_days": len(pick_days)}


# -- retro mode ---------------------------------------------------------------

def _trading_days_back(n: int, end: date) -> date:
    from stock_analyzer.data import is_trading_day
    d, seen = end, 0
    while seen < n:
        d -= timedelta(days=1)
        if is_trading_day(d):
            seen += 1
    return d


def _truncate_before(df, rec_date: date):
    import pandas as pd
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    return df[idx.normalize() < pd.Timestamp(rec_date)]


def _windows(df_full, rec_date: date):
    """(df_long, df_6m): bars strictly before rec_date, and the subset within 6
    calendar months of it (what a live period="6mo" bundle would hold)."""
    import pandas as pd
    df_long = _truncate_before(df_full, rec_date)
    idx = pd.to_datetime(df_long.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    floor = pd.Timestamp(rec_date) - pd.DateOffset(months=6)
    return df_long, df_long[idx.normalize() >= floor]


def _row_result(rec: dict, df_full, bundle_today: dict, snap: dict | None) -> dict:
    """Measure one recommendation row; never raises (a bad row becomes a skip)."""
    from stock_analyzer.risk import _atr_value
    from stock_analyzer.targets import compute_price_targets, risk_reward
    from stock_analyzer.valuation_target import choose_pt_target

    lane = _LANES[rec["rec_type"]]
    out = {"date": str(rec.get("rec_date"))[:10], "lane": lane,
           "ticker": rec.get("ticker"), "skip": None,
           "asset_type": rec.get("asset_type")}
    entry = _f(rec.get("price_at_surface"))
    if entry is None or entry <= 0:
        out["skip"] = "no_price_at_surface"
        return out
    out["entry"] = entry
    try:
        rec_date = date.fromisoformat(out["date"])
        df_long, df = _windows(df_full, rec_date)
        if len(df) < 20:
            out["skip"] = "short_history"
            return out
        atr = float(_atr_value(df))
        out["atr_pct"] = atr / entry * 100
        stop = entry - ATR_STOP_MULT * atr
        out["stop"] = stop
        if snap is not None and _f(snap.get("target_mean")) is not None:
            p_target, p_n, basis = snap.get("target_mean"), snap.get("num_analysts"), "snapshot"
        else:
            fin = (bundle_today or {}).get("financials") or {}
            p_target, p_n, basis = fin.get("analyst_target"), fin.get("num_analyst_opinions"), "approx_today"
        out["target_basis"] = basis
        chosen = choose_pt_target(
            p_target, p_n, None, rec_date, False, [], entry,
            min_analysts=VALUATION_PT_MIN_ANALYSTS, max_ratio=VALUATION_PT_MAX_TARGET_RATIO)
        w52 = df_long.tail(252)
        tg = compute_price_targets(
            df, {"52_week_high": float(w52["High"].max()), "52_week_low": float(w52["Low"].min())},
            entry, chosen["target"])
        base = tg.get("base")
        out["base"] = base
        out["base_source"] = tg.get("base_source")
        m = entry_rr.measure(entry, stop, base)
        out.update(rr=m["rr"], state=m["state"], cls=entry_rr.classify(m))
        out["rr_old"] = risk_reward(entry, stop, base) if m["state"] != "unknown" else None
        rs = _f(rec.get("rec_stop"))
        if rs is not None and rs > 0:
            out["stop_lane"] = rs
            out["cls_lane"] = entry_rr.classify(entry_rr.measure(entry, rs, base))
    except Exception as e:  # per-row isolation
        out["skip"] = f"error:{type(e).__name__}"
    return out


def run_retro(args) -> list[dict]:
    from stock_analyzer import db
    from stock_analyzer.bundle_loader import load_bundle
    from stock_analyzer.data import fetch_risk_free_rate, fetch_spy
    from stock_analyzer.market_time import today_et

    end = today_et()
    start = _trading_days_back(args.days, end)
    recs = db.load_recommendations_or_none(start, end)
    if recs is None:
        raise SystemExit("Could not read recommendations from Supabase. Nothing was computed.")
    recs = recs[recs["rec_type"].isin(list(_LANES))]
    if args.tickers:
        recs = recs[recs["ticker"].str.upper().isin(args.tickers)]
    print(f"{len(recs)} rows, rec_date after {start} ({args.days} trading days back) "
          f"through {end} (today, inclusive)")
    snaps = db.load_analyst_target_snapshots(days_back=(end - start).days + 7)
    snap_map = {}
    if snaps is not None and not snaps.empty:
        for _, s in snaps.iterrows():
            snap_map[(str(s["ticker"]).upper(), str(s["snapshot_date"])[:10])] = s.to_dict()
    try:
        rfr = fetch_risk_free_rate()
    except Exception:
        rfr = 0.045
    try:
        spy = fetch_spy("1y")
    except Exception:
        spy = None
    bundles: dict = {}
    out = []
    for rec in recs.to_dict("records"):
        t = str(rec.get("ticker") or "").upper()
        if t not in bundles:
            try:
                bundles[t] = load_bundle(t, "1y", spy_df=spy, rfr=rfr)
            except Exception:
                bundles[t] = None
        b = bundles[t]
        rd = str(rec.get("rec_date"))[:10]
        if not b or b.get("df") is None:
            out.append({"date": rd, "lane": _LANES[rec["rec_type"]], "ticker": t,
                        "skip": "no_bundle"})
            continue
        out.append(_row_result(rec, b["df"], b, snap_map.get((t, rd))))
    return out


# -- --today mode -------------------------------------------------------------

def run_today(args) -> list[dict]:
    from stock_analyzer import db
    from stock_analyzer.bundle_loader import load_bundle
    from stock_analyzer.data import fetch_risk_free_rate, fetch_spy
    from stock_analyzer.headless_alert_engine import compute_morning_picks
    from stock_analyzer.market_time import today_et
    from stock_analyzer.targets import risk_reward

    cache = db.load_scanner_cache()
    if not cache or cache.get("df") is None:
        raise SystemExit("No persisted scanner_cache -- cannot run --today.")
    print(f"scanner_cache: scan_date={cache.get('scan_date')} scanned_at={cache.get('scanned_at')} "
          f"source={cache.get('source')} (near-exact: bundles are re-fetched now)")
    res = compute_morning_picks(scanner_results=cache["df"])
    for e in res.get("errors") or []:
        print(f"  note: {e}")
    grow = res.get("grow") or {}
    items = ([("pick", p) for p in res.get("picks") or []]
             + [("add", p) for p in grow.get("add_positions") or []]
             + [("overflow", p) for p in grow.get("overflow_picks") or []])
    try:
        rfr = fetch_risk_free_rate()
    except Exception:
        rfr = 0.045
    try:
        spy = fetch_spy("6mo")
    except Exception:
        spy = None
    today = today_et().isoformat()
    out = []
    for lane, p in items:
        t = str(p.get("ticker") or "").upper()
        if args.tickers and t not in args.tickers:
            continue
        row = {"date": today, "lane": lane, "ticker": t, "skip": None, "target_basis": "live",
               "is_mover": p.get("is_mover")}
        try:
            b = load_bundle(t, "6mo", spy_df=spy, rfr=rfr)
            m = entry_rr.from_bundle(b)
            atr = _f(b.get("atr"))
            entry = _f(b.get("current_price"))
            row.update(entry=entry, stop=_f(b.get("stop")),
                       base=_f((b.get("targets") or {}).get("base")),
                       base_source=m.get("base_source"), rr=m["rr"], state=m["state"],
                       cls=entry_rr.classify(m), asset_type=b.get("asset_type"),
                       atr_pct=(atr / entry * 100) if atr is not None and entry else None)
            row["rr_old"] = (risk_reward(entry, row["stop"], row["base"])
                             if m["state"] != "unknown" else None)
        except Exception as e:
            row["skip"] = f"error:{type(e).__name__}"
        out.append(row)
    return out


# -- output -------------------------------------------------------------------

_COLS = ("date", "lane", "ticker", "entry", "stop", "base", "base_source", "target_basis",
         "rr", "cls", "atr_pct")
_HDR = ("date", "lane", "ticker", "entry", "stop", "base", "base_source", "target_basis",
        "R:R", "class", "ATR%")


def _cell(r: dict, c: str) -> str:
    v = r.get(c)
    if c == "rr":
        return fmt_ratio_vs(v, RR_ENTRY_MIN) + ":1" if v is not None else "-"
    if c in ("entry", "stop", "base", "atr_pct"):
        return f"{v:.2f}" if isinstance(v, (int, float)) else "-"
    return str(v) if v not in (None, "") else "-"


def print_rows(rows: list[dict]) -> None:
    ok = [r for r in rows if not r.get("skip")]
    table = [_HDR] + [tuple(_cell(r, c) for c in _COLS) for r in ok]
    widths = [max(len(str(t[i])) for t in table) for i in range(len(_HDR))]
    for t in table:
        print("  ".join(str(c).ljust(w) for c, w in zip(t, widths)))


def print_summary(s: dict) -> None:
    print(f"\n=== SUMMARY (RR_ENTRY_MIN={RR_ENTRY_MIN}; near-miss band "
          f"[{_NEAR_MISS_FLOOR}, {RR_ENTRY_MIN}) is a readout only, not a gate) ===")
    for lane, L in s["lanes"].items():
        if not L["shown"]:
            print(f"\n[{lane}] no rows")
            continue
        print(f"\n[{lane}] shown {L['shown']}, would be held {L['would_hold']}")
        print(f"  by class: {L['by_class']}")
        print(f"  all by base_source: {L['all_by_base_source']}")
        print(f"  held by base_source: {L['held_by_base_source']}")
        print(f"  near misses [{_NEAR_MISS_FLOOR}, {RR_ENTRY_MIN}): {L['near_misses']}")
        print(f"  1-dp risk_reward >= {RR_ENTRY_MIN} but unrounded does not clear: "
              f"{L['rounding_disagree']}")
        print(f"  bundle-vs-lane stop basis flips: {L['basis_flips']} of "
              f"{L['basis_compared']} compared")
        print(f"  days with no passing {lane} row: {L['zero_pass_days']} of "
              f"{L['days']} days with >=1 {lane} row")
        if L["by_target_basis"]:
            print(f"  by target_basis (shown / held): {L['by_target_basis']}")
        if L["by_asset_type"]:
            print(f"  by asset_type (shown / held): {L['by_asset_type']}")
        if lane == "pick" and (L["movers"]["mover"]["shown"] or L["movers"]["curated"]["shown"]):
            print(f"  movers vs curated (rows with the flag known): {L['movers']}")
        if lane == "overflow":
            print(f"  overflow names that pass (could fill a freed slot): {L['would_fill_slot']}")
    print(f"\ndays with zero passing picks AND zero passing overflow names: "
          f"{s['zero_pass_pick_and_overflow_days']} of {s['pick_days']} days with >=1 pick row")
    print(f"\nskipped rows: {s['skipped']} {s['skips'] or ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30, help="trading days back (retro mode)")
    ap.add_argument("--today", action="store_true", help="near-exact mode on the live scanner_cache")
    ap.add_argument("--csv", default="", help="write the per-row results to this path")
    ap.add_argument("--tickers", default="", help="comma-separated ticker filter")
    args = ap.parse_args()
    args.tickers = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}

    from stock_analyzer import db
    db.set_readonly(True)
    if not db.has_db():
        print("No Supabase credentials (SUPABASE_URL/SUPABASE_KEY) -- cannot run.")
        return 1

    rows = run_today(args) if args.today else run_retro(args)
    print_rows(rows)
    print_summary(summarize(rows))
    if args.csv:
        keys = list(_COLS) + ["state", "rr_old", "stop_lane", "cls_lane", "is_mover", "asset_type", "skip"]
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} rows to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
