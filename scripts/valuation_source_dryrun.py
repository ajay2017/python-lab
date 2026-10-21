#!/usr/bin/env python3
"""Dry-run: what does provider-first PT sourcing do to Valuation and the composite?

Item C, chunk C1 (read-only diagnostic; C2 later wires the chooser into
bundle_loader). For every held / watchlist / in-window-coverage ticker it loads
the bundle exactly the way the app does, then recomputes:

  OLD  the valuation + composite the bundle carries today (saved-research avg
       target first, provider `analyst_target` only as a fallback)
  NEW  valuation_score with avg_pt = the chooser's target (provider first, saved
       research only as fallback) and the financials `analyst_target` fallback
       suppressed (a copy with analyst_target=None), so a rejected provider
       target can never sneak back in through valuation_score's own fallback.
       The composite is recombined with scoring.combined_score (existing
       weights, nothing reimplemented).

Never writes to the DB (`db.set_readonly(True)`), makes no LLM calls
(ANTHROPIC_API_KEY is dropped before any import). Produces evidence only.

Requires Supabase credentials in the environment (SUPABASE_URL / SUPABASE_KEY),
the same ones Railway uses -- run from a normal shell. Fetches market data via
the app's own providers.

Usage:
    python scripts/valuation_source_dryrun.py
    python scripts/valuation_source_dryrun.py --min-analysts 3 --max-ratio 3.0
    python scripts/valuation_source_dryrun.py --tickers SPCX,MU

Known approximations (marked, not guessed):
  * provider `as_of`/`cached`: the bundle exposes `stale_as_of` (set when the
    whole bundle came from the last-known-good cache), `fund_source` ("live" or
    a cache tag) and `fetched_at`. cached = stale_as_of set OR fund_source !=
    "live"; as_of = stale_as_of or fetched_at. C2 may refine this.
  * saved-research firm key is read as `firm` on each analyst dict (the
    analyst_intel extraction prompt's field name); each record's date is its own
    `article_date` or else the coverage row's.
"""
from __future__ import annotations

import os

os.environ.pop("ANTHROPIC_API_KEY", None)   # no LLM spend, before any app import

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from collections import Counter  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import db  # noqa: E402
from stock_analyzer.bundle_loader import load_bundle  # noqa: E402
from stock_analyzer.constants import (  # noqa: E402
    COMPOSITE_BUY, COMPOSITE_HOLD, COMPOSITE_STRONG_BUY, VALUATION_COVERAGE_FRESH_DAYS,
)
from stock_analyzer.data import fetch_risk_free_rate, fetch_spy  # noqa: E402
from stock_analyzer.scoring import combined_score, recommendation  # noqa: E402
from stock_analyzer.valuation import valuation_score  # noqa: E402
from stock_analyzer.valuation_target import choose_pt_target, pt_short_tag  # noqa: E402

_THRESHOLDS = (("HOLD", COMPOSITE_HOLD), ("BUY", COMPOSITE_BUY), ("STRONG", COMPOSITE_STRONG_BUY))


def _saved_by_ticker(cov_df) -> dict:
    """{ticker: [ {firm, price_target, article_date}, ... ]} from coverage rows."""
    out: dict = {}
    if cov_df is None or cov_df.empty:
        return out
    for _, row in cov_df.iterrows():
        t = str(row.get("ticker") or "").strip().upper()
        if not t:
            continue
        raw = row.get("analysts") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = []
        if not isinstance(raw, list):
            continue
        for a in raw:
            if not isinstance(a, dict):
                continue
            out.setdefault(t, []).append({
                "firm": a.get("firm"),
                "price_target": a.get("price_target"),
                "article_date": a.get("article_date") or row.get("article_date"),
            })
    return out


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _cross(old, new) -> list[str]:
    return [name for name, th in _THRESHOLDS if (old >= th) != (new >= th)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--min-analysts", type=int, default=3)
    ap.add_argument("--max-ratio", type=float, default=3.0)
    ap.add_argument("--tickers", default="", help="comma-separated override of the ticker set")
    args = ap.parse_args()

    db.set_readonly(True)
    if not db.has_db():
        print("No Supabase credentials (SUPABASE_URL/SUPABASE_KEY) -- cannot run.")
        return 1

    cov_df = db.load_analyst_coverage(days=VALUATION_COVERAGE_FRESH_DAYS, limit=None)
    saved_map = _saved_by_ticker(cov_df)

    held: set[str] = set()
    hdf = db.load_holdings_or_none()
    if hdf is not None and not hdf.empty:
        held = {str(t).strip().upper() for t in hdf["Ticker"].tolist() if str(t).strip()}
    wl = db.load_watchlist_or_none() or []
    if args.tickers.strip():
        tickers = sorted({t.strip().upper() for t in args.tickers.split(",") if t.strip()})
    else:
        tickers = sorted(held | {str(t).strip().upper() for t in wl if str(t).strip()}
                         | set(saved_map))
    print(f"{len(tickers)} tickers ({len(held)} held); min_analysts={args.min_analysts} "
          f"max_ratio={args.max_ratio}\n")

    try:
        rfr = fetch_risk_free_rate()
    except Exception:
        rfr = 0.045
    try:
        spy = fetch_spy("6mo")
    except Exception:
        spy = None

    hdr = ("ticker", "held", "provider tgt/N", "old saved avg", "new source",
           "val old>new", "comp old>new", "verdict old>new", "crosses", "reject")
    rows = []
    changed = 0
    crossings: Counter = Counter()
    rejects: Counter = Counter()
    for t in tickers:
        try:
            b = load_bundle(t, "6mo", spy_df=spy, rfr=rfr)
            fin = dict(b.get("financials") or {})
            price = b.get("current_price")
            ad = dict(b.get("val_analyst_data") or {})
            cached = b.get("stale_as_of") is not None or b.get("fund_source") not in (None, "live")
            as_of = b.get("stale_as_of") or b.get("fetched_at")
            ch = choose_pt_target(
                fin.get("analyst_target"), fin.get("num_analyst_opinions"), b.get("info_source"),
                as_of, cached, saved_map.get(t, []), price,
                min_analysts=args.min_analysts, max_ratio=args.max_ratio)
            new_ad = dict(ad, avg_pt=ch["target"])
            fin_new = dict(fin, analyst_target=None)   # suppress valuation_score's own fallback
            old_val, old_tot = b["val_score"], b["total"]
            new_val, _sig, _avail = valuation_score(fin_new, new_ad, price, b.get("sector") or "")
            new_tot = combined_score(b["t_score"], b["bq_score"], new_val, b["s_score"])
            old_v = recommendation(old_tot)["label"]
            new_v = recommendation(new_tot)["label"]
            cr = _cross(old_tot, new_tot)
            if abs(new_tot - old_tot) >= 0.05:
                changed += 1
            for c in cr:
                crossings[c] += 1
            rej = [r for r in (ch["provider_reject"], ch["saved_reject"]) if r]
            for r in rej:
                rejects[r] += 1
            ptn = (f"{_f(fin.get('analyst_target')):.2f}/{fin.get('num_analyst_opinions')}"
                   if _f(fin.get("analyst_target")) is not None else "-/-")
            old_avg = f"{_f(ad.get('avg_pt')):.2f}" if _f(ad.get("avg_pt")) is not None else "-"
            rows.append((t, "Y" if t in held else "", ptn, old_avg,
                         f"{ch['source'] or '-'} ({pt_short_tag(ch)})",
                         f"{old_val:.1f}>{new_val:.1f}", f"{old_tot:.1f}>{new_tot:.1f}",
                         f"{old_v}>{new_v}", ",".join(cr) or "-", ",".join(rej) or "-"))
        except Exception as e:   # per-ticker isolation: print and continue
            rows.append((t, "Y" if t in held else "", "ERROR", "", "", "", "", "", "",
                         f"{type(e).__name__}: {e}"[:60]))

    widths = [max(len(str(r[i])) for r in rows + [hdr]) for i in range(len(hdr))]
    for r in [hdr] + rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)))
    print(f"\nnames with a changed composite: {changed} of {len(rows)}")
    print("threshold crossings:", dict(crossings) or "none",
          f"(HOLD={COMPOSITE_HOLD}, BUY={COMPOSITE_BUY}, STRONG={COMPOSITE_STRONG_BUY})")
    print("reject reasons:", dict(rejects) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
