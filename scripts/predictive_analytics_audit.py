#!/usr/bin/env python3
"""Predictive Analytics audit -- old page population vs the corrected one.

Read-only sibling of `scripts/offense_attribution.py`. The 📊 Predictive
Analytics page used to band ONE ROW PER DAILY SURFACING, across every
rec_type (incl. the awareness-only `buy_candidate`), every composite-weights
regime, and ETFs -- and called a score band "consistently positive" on hit
rate alone, even with a negative mean alpha. The 2026-09-30 fix (owner
decisions D1-D6) grades one row per ticker, gated BUY calls only
(`ACTIONABLE_REC_TYPES`), current weights only, stocks only, and requires a
band to beat SPY both on average AND more often than not.

This script prints both populations side by side so the size of the change
is visible, using the SAME functions the page calls
(`match_recs_to_trades` -> `compute_outcomes` -> `prepare_population` ->
the page's own lens functions), so its numbers cannot drift from the page.

  [A] current page  -- all rows, all types/versions/asset types (raw)
  [B] new scope raw -- version/ETF/scope-filtered rows, NOT collapsed
  [C] new scope     -- [B] collapsed to one representative per ticker

REDLINE. Read-only. Touches no gate, no constant, no DB row. Evidence only.

Requires SUPABASE_URL / SUPABASE_KEY (service-role) in the environment, and
network access for SPY history, one batch live-price call and (unless
--skip-forward) one historical-close lookup per divergent new_pick episode
for the true Day+20 forward alpha.

Usage:
    python scripts/predictive_analytics_audit.py
    python scripts/predictive_analytics_audit.py --skip-forward
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer.constants import (  # noqa: E402
    COMPOSITE_WEIGHTS_VERSION,
    ENTRY_TIMING_DEDUP_WINDOW_DAYS,
    ENTRY_TIMING_DIVERGENCE_ALIGNED_MAX,
    ENTRY_TIMING_DIVERGENCE_DIVERGING_MAX,
    PREDICTIVE_MIN_BAND_N,
    REC_SCORE_MIN_DAYS,
)
from stock_analyzer import predictive_analytics as pa  # noqa: E402
from stock_analyzer.recommendations_history import (  # noqa: E402
    collapse_recs_by_ticker,
    compute_outcomes,
    match_recs_to_trades,
)

PAGE_SIZE = 1000
DAY20 = 20


class PaginationError(RuntimeError):
    """Fetched row count disagrees with the server's exact count."""


# ── Paginated read (pure assembly, testable with fake pages) ────────────────

def fetch_all_pages(
    fetch_page: Callable[[int, int], "tuple[list[dict], int | None]"],
    page_size: int = PAGE_SIZE,
) -> list[dict]:
    """Assemble every row from a paged source.

    `fetch_page(start, end)` returns `(rows, exact_count)` for the inclusive
    range [start, end]; `exact_count` is the server's total (PostgREST
    `Prefer: count=exact`, parsed from `Content-Range`), or None if the
    server didn't send one. Stops at the first short/empty page or once the
    exact count is reached. Raises PaginationError when the assembled length
    differs from the exact count, or when no exact count was ever returned
    -- a silently truncated read is exactly the defect class this audit is
    looking for, so it must never be tolerated here.
    """
    rows: list[dict] = []
    exact: int | None = None
    start = 0
    while True:
        page, count = fetch_page(start, start + page_size - 1)
        if count is not None:
            exact = count
        rows.extend(page)
        if len(page) < page_size:
            break
        if exact is not None and len(rows) >= exact:
            break
        start += page_size
    if exact is None:
        raise PaginationError("server returned no exact count; refusing to trust the read")
    if len(rows) != exact:
        raise PaginationError(f"fetched {len(rows)} rows but the server reports {exact}")
    return rows


def _parse_content_range(v: "str | None") -> "int | None":
    # "0-999/1234" or "*/0"
    if not v or "/" not in v:
        return None
    tail = v.rsplit("/", 1)[1].strip()
    return int(tail) if tail.isdigit() else None


def _rest_page_fn(table: str) -> Callable[[int, int], "tuple[list[dict], int | None]"]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "")

    def _fetch(start: int, end: int):
        resp = requests.get(
            f"{url}/rest/v1/{table}",
            params={"select": "*", "order": "id.asc"},
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Range-Unit": "items",
                "Range": f"{start}-{end}",
                "Prefer": "count=exact",
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json(), _parse_content_range(resp.headers.get("Content-Range"))

    return _fetch


def load_table(table: str) -> "pd.DataFrame | None":
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_KEY"):
        print("SUPABASE_URL / SUPABASE_KEY not set.")
        return None
    try:
        rows = fetch_all_pages(_rest_page_fn(table))
    except PaginationError as e:
        print(f"ABORT -- {table}: {e}")
        return None
    except Exception as e:
        print(f"Could not read the {table} table: {e}")
        return None
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── Live data (same construction as offense_attribution.py) ─────────────────

def build_spy_close_by_date(period: str = "1y") -> dict:
    from stock_analyzer import data as _data
    out: dict = {}
    try:
        hist = _data.fetch_spy(period)
        if hist is not None and not hist.empty and "Close" in hist.columns:
            for idx, row in hist.iterrows():
                d = idx.date() if hasattr(idx, "date") else None
                try:
                    c = float(row["Close"])
                except (TypeError, ValueError):
                    c = None
                if d is not None and c and c > 0:
                    out[d] = c
    except Exception as e:
        print(f"Could not fetch SPY history: {e}")
    return out


def fetch_current_prices(tickers: list[str]) -> dict:
    from stock_analyzer import data as _data
    if not tickers:
        return {}
    try:
        px = _data.fetch_live_prices(tickers)
        return {t: float(d.get("price", 0)) for t, d in (px or {}).items()
                if d and d.get("price")}
    except Exception as e:
        print(f"Could not fetch current prices: {e}")
        return {}


# ── Small print helpers ─────────────────────────────────────────────────────

def _graded(r: dict) -> bool:
    return not r.get("outcome_maturing") and r.get("alpha_pct") is not None


def _mean(vals) -> "float | None":
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _fmt(v, spec="+.2f") -> str:
    return "—" if v is None else format(v, spec)


def _hdr(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def _old_threshold(bands: list[dict], min_n: int) -> "int | None":
    """The PRE-2026-09-30 criterion (hit rate >= 0.5 only), reproduced here
    so the audit can show what the old page would have said."""
    eligible = [b for b in sorted(bands, key=lambda x: x["band_floor"])
                if b["n"] >= min_n and b["p_positive_alpha"] is not None]
    for i, b in enumerate(eligible):
        if all(x["p_positive_alpha"] >= 0.5 for x in eligible[i:]):
            return b["band_floor"]
    return None


def _band_floor_of(r: dict, size: int = 5) -> "int | None":
    try:
        return int((float(r["composite_score"]) // size) * size)
    except (TypeError, ValueError, KeyError):
        return None


# ── Sections ────────────────────────────────────────────────────────────────

def s0_integrity(recs_df, trades_df, enriched) -> None:
    _hdr("§0 LOAD INTEGRITY")
    print(f"recommendations rows: {len(recs_df)}  (paginated; matched the server's exact count)")
    print(f"trades rows:          {len(trades_df)}")
    dates = [r["rec_date"] for r in enriched if r.get("rec_date")]
    print(f"rec_date range:       {min(dates) if dates else '—'} → {max(dates) if dates else '—'}")
    print(f"current COMPOSITE_WEIGHTS_VERSION = {COMPOSITE_WEIGHTS_VERSION}")
    print("\nrec_type × weights_version × asset_type (raw values):")
    mx = Counter((r.get("rec_type"), r.get("weights_version"), r.get("asset_type")) for r in enriched)
    for (rt, wv, at), c in sorted(mx.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), str(kv[0][2]))):
        print(f"  {str(rt):<14} v={str(wv):<5} asset={str(at):<6} {c:>6}")


def s1_funnel(enriched, pop) -> None:
    _hdr("§1 FUNNEL")
    types = sorted({r.get("rec_type") or "" for r in enriched})
    per_type_reps = pa.collapse_by_ticker_rec_type(enriched, tuple(types))
    print(f"{'rec_type':<14} {'raw':>6} {'mature':>7} {'graded':>7} {'graded tickers':>15}")
    for t in types:
        rows = [r for r in enriched if (r.get("rec_type") or "") == t]
        mature = sum(1 for r in rows if not r.get("outcome_maturing"))
        graded = sum(1 for r in rows if _graded(r))
        gt = sum(1 for r in per_type_reps if (r.get("rec_type") or "") == t and _graded(r))
        print(f"{t:<14} {len(rows):>6} {mature:>7} {graded:>7} {gt:>15}")
    print("\n(all-types graded tickers above are per type, all versions, incl. ETFs)")
    print(f"\nNew scope {pa.ACTIONABLE_REC_TYPES}, v{COMPOSITE_WEIGHTS_VERSION}, stocks only:")
    for k in ("n_raw_rows", "n_excluded_version", "n_excluded_version_none", "n_excluded_etf",
              "n_excluded_out_of_scope_rows", "n_raw_graded", "n_tickers_graded",
              "n_out_of_scope_anchor"):
        print(f"  {k:<30} {pop[k]}")
    print(f"  {'scoped_raw rows':<30} {len(pop['scoped_raw'])}")
    print(f"  {'reps (tickers, any grade)':<30} {len(pop['reps'])}")


def _band_stats(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        if not _graded(r):
            continue
        f = _band_floor_of(r)
        if f is None:
            continue
        out.setdefault(f, []).append(r)
    return out


def s2_calibration(enriched, pop) -> None:
    _hdr("§2 SCORE CALIBRATION (5-pt bands; * = n < PREDICTIVE_MIN_BAND_N)")
    mn = PREDICTIVE_MIN_BAND_N
    A, B, C = _band_stats(enriched), _band_stats(pop["scoped_raw"]), _band_stats(pop["reps"])
    floors = sorted(set(A) | set(B) | set(C))

    def _cell(rows):
        if not rows:
            return f"{'0':>4} {'—':>7} {'—':>5}"
        al = [r["alpha_pct"] for r in rows]
        hit = sum(1 for a in al if a > 0) / len(al)
        star = "*" if len(rows) < mn else " "
        return f"{len(rows):>3}{star} {_mean(al):>+7.2f} {hit:>5.0%}"

    print(f"{'band':<7} | {'[A] current page':^18} | {'[B] new scope raw':^18} | "
          f"{'[C] new scope collapsed':^40}")
    print(f"{'':<7} | {'n':>4} {'avg':>7} {'hit':>5} | {'n':>4} {'avg':>7} {'hit':>5} | "
          f"{'n':>4} {'avg':>7} {'hit':>5} {'acted':>6} {'missed':>6} {'med days':>8}")
    for f in floors:
        c = C.get(f, [])
        acted = sum(1 for r in c if r.get("acted_on"))
        days = [r.get("days_since") for r in c if r.get("days_since") is not None]
        med = statistics.median(days) if days else None
        print(f"{f}–{f + 4:<4} | {_cell(A.get(f, []))} | {_cell(B.get(f, []))} | "
              f"{_cell(c)} {acted:>6} {len(c) - acted:>6} {_fmt(med, '.0f'):>8}")

    print("\n[A] composition by rec_type:")
    for f in floors:
        comp = Counter(r.get("rec_type") for r in A.get(f, []))
        print(f"  {f}–{f + 4}: {dict(comp)}")
    print("\n[A] composition by weights_version:")
    for f in floors:
        comp = Counter(r.get("weights_version") for r in A.get(f, []))
        print(f"  {f}–{f + 4}: {dict(comp)}")

    bands_a = pa.calibration_by_score_band(enriched)
    bands_c = pa.calibration_by_score_band(pop["reps"])
    t_old_a = _old_threshold(bands_a, mn)
    t_old_c = _old_threshold(bands_c, mn)
    t_new_c = pa.personal_alpha_threshold(bands_c, min_n=mn)
    print("\nThreshold:")
    print(f"  old criterion on [A]: {t_old_a}"
          + (f'  -> "Your alpha turns consistently positive at composite ≥ {t_old_a}."'
             if t_old_a is not None else ""))
    print(f"  old criterion on [C]: {t_old_c}"
          + (f'  -> "Your alpha turns consistently positive at composite ≥ {t_old_c}."'
             if t_old_c is not None else ""))
    print(f"  new criterion on [C]: {t_new_c}")
    bn = pa.threshold_banner(bands_c, t_new_c, min_n=mn)
    if bn is not None:
        print("  banner:\n    " + bn["text"].replace("\n", "\n    "))
    else:
        print(f"  banner: none -- no score band yet both beat SPY on average AND in at "
              f"least half of its tickers, with ≥{mn} tickers")


def s3_discretion(enriched, pop) -> None:
    _hdr("§3 DISCRETION VALUE")
    for label, rows in (("[A]", enriched), ("[C]", pop["reps"]),
                        ("ETR scope (new_pick collapsed, all versions)",
                         collapse_recs_by_ticker(enriched, rec_types=("new_pick",)))):
        avm = pa.acted_vs_missed_comparison(rows)
        a, m = avm["acted"], avm["missed"]
        print(f"{label}: acted n={a['n']} avg={_fmt(a['avg_alpha'])} hit={_fmt(a['p_positive_alpha'], '.0%')} | "
              f"missed n={m['n']} avg={_fmt(m['avg_alpha'])} hit={_fmt(m['p_positive_alpha'], '.0%')} | "
              f"edge={avm['edge']} {_fmt(avm['edge_pp'])}")


def _print_group(rows: list[dict], key: str) -> None:
    if not rows:
        print("    (none)")
    for r in rows:
        print(f"    {str(r[key]):<22} n={r['n']:<4} avg={_fmt(r['avg_alpha'])} "
              f"hit={_fmt(r['p_positive_alpha'], '.0%')}")


def s4_signal(enriched, pop) -> None:
    _hdr("§4 SIGNAL BREAKDOWN (conviction, rec_type)")
    for mn in (3, 5):
        print(f"\nmin_n = {mn}")
        print("  conviction [A]:"); _print_group(pa.by_conviction(enriched, min_n=mn), "conviction")
        print("  conviction [C]:"); _print_group(pa.by_conviction(pop["reps"], min_n=mn), "conviction")
        print("  rec_type [A]:");   _print_group(pa.by_rec_type_stats(enriched, min_n=mn), "label")
        print("  rec_type [C] (reps_by_type):")
        _print_group(pa.by_rec_type_stats(pop["reps_by_type"], min_n=mn), "label")


def s5_sector(enriched, pop) -> None:
    _hdr(f"§5 SECTOR ALPHA (min_n {PREDICTIVE_MIN_BAND_N})")
    print("[A]:"); _print_group(pa.by_sector_alpha(enriched), "sector")
    print("[C]:"); _print_group(pa.by_sector_alpha(pop["reps"]), "sector")
    print_sector_label_map(pop["reps"])


# Labels with no clean curated equivalent (owner decision 2026-09-30,
# sector_labels.py): tickers landing here need a TICKER_SECTORS entry.
_UNRESOLVED_SECTOR_LABELS = ("Other", "Technology", "Consumer Cyclical", "AI & Data Platforms")


def print_sector_label_map(reps: list[dict]) -> None:
    """How stored labels were canonicalised, plus the tickers still unresolved."""
    from collections import Counter, defaultdict
    print()
    print("Sector label canonicalisation (reps): stored label -> canonical label, count")
    pairs = Counter((str(r.get("sector_raw") or "(blank)"), r.get("sector")) for r in reps)
    for (raw, canon), n in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0])):
        mark = "" if raw == canon else "   (relabelled)"
        print(f"  {raw:<28} -> {canon:<26} {n:>4}{mark}")
    todo: dict[str, list[str]] = defaultdict(list)
    for r in reps:
        if r.get("sector") in _UNRESOLVED_SECTOR_LABELS:
            todo[r["sector"]].append(str(r.get("ticker")))
    if todo:
        print("Tickers still unresolved (add them to TICKER_SECTORS to classify):")
        for lab, tks in sorted(todo.items()):
            print(f"  {lab:<22} {', '.join(sorted(set(tks)))}")
    else:
        print("Tickers still unresolved: none")


def s6_sentiment(enriched, pop) -> None:
    _hdr("§6 SENTIMENT ALIGNMENT")
    for label, rows in (("[A]", enriched), ("[C]", pop["reps"])):
        bv = pa.calibration_by_verdict(rows)
        sa = pa.sentiment_alignment_summary(bv, min_n=PREDICTIVE_MIN_BAND_N)
        print(f"{label}: confirmed n={sa['confirmed_n']} avg={_fmt(sa['confirmed_avg_alpha'])} | "
              f"other n={sa['other_n']} avg={_fmt(sa['other_avg_alpha'])} | "
              f"unknown (excluded) n={sa['n_unknown']} | {sa['conclusion']} edge={_fmt(sa['edge_pp'])}")
        for b in bv:
            print(f"    {b['verdict']:<20} n={b['n']:<4} avg={_fmt(b['avg_alpha'])}")


def s7_entry_timing(pop, spy_close_by_date, skip_forward: bool) -> None:
    from stock_analyzer.market_time import today_et
    _hdr("§7 ENTRY TIMING (new_pick, new scope)")
    raw = [r for r in pop["scoped_raw"] if r.get("rec_type") == "new_pick"]
    eps = pa.dedupe_repeated_tickers(raw, window_days=ENTRY_TIMING_DEDUP_WINDOW_DAYS,
                                     rec_types=("new_pick",))
    print(f"raw rows={len(raw)}  episodes={len(eps)}  tickers={len({r.get('ticker') for r in raw})}")
    today = today_et()
    rows = []
    n_young = 0
    for r in eps:
        rec = dict(r)
        rec["divergence"] = pa.divergence_at_entry(rec)
        rec["day20_alpha"] = None
        if (not skip_forward and rec["divergence"] is not None and rec["divergence"] > 0
                and rec.get("rec_date") is not None):
            if pa._advance_trading_days(rec["rec_date"], DAY20) > today:
                n_young += 1
            else:
                rec["day20_alpha"] = pa.forward_alpha_at_horizon(
                    rec["ticker"], rec["rec_date"], rec.get("price_at_surface"),
                    DAY20, spy_close_by_date,
                )
        rows.append(rec)
    if skip_forward:
        print("True Day+20 skipped (--skip-forward).")
    else:
        print(f"{n_young} divergent episode(s) younger than {DAY20} trading days (no Day+20 yet).")

    def _band(div):
        if div <= ENTRY_TIMING_DIVERGENCE_ALIGNED_MAX:
            return "Aligned"
        if div <= ENTRY_TIMING_DIVERGENCE_DIVERGING_MAX:
            return "Diverging"
        return "Extreme"

    old = defaultdict(list)
    for r in rows:
        d = r["divergence"]
        if d is not None and d > 0 and _graded(r):
            old[_band(d)].append(r["alpha_pct"])
    new = {b["band_label"]: b for b in pa.by_divergence_band(
        rows, ENTRY_TIMING_DIVERGENCE_ALIGNED_MAX, ENTRY_TIMING_DIVERGENCE_DIVERGING_MAX)}
    print(f"\n{'band':<10} {'old Day+20 (to-date alpha)':>28} {'true forward Day+20':>22}")
    for lbl in ("Aligned", "Diverging", "Extreme"):
        o = old.get(lbl, [])
        nb = new.get(lbl)
        n20 = nb["day20_n"] if nb else 0
        a20 = nb["day20_alpha"] if nb else None
        print(f"{lbl:<10} {'n=' + str(len(o)):>8} {_fmt(_mean(o)):>19} {'n=' + str(n20):>10} {_fmt(a20):>11}")


def _directives(bands, avm, conv, rtype, sec, n, sa):
    return pa.synthesize_directives(
        bands=bands, thresh=pa.personal_alpha_threshold(bands, min_n=PREDICTIVE_MIN_BAND_N),
        avm=avm, conv=conv, rtype=rtype, sec_alph=sec, n_graded=n,
        min_n=PREDICTIVE_MIN_BAND_N, sentiment_alignment=sa,
    )


def s8_directives(enriched, pop) -> None:
    _hdr("§8 DIRECTIVES (synthesize_directives, verbatim)")
    print("NOTE: [A] runs the CURRENT (post-fix) directive code on the old population;")
    print("      the thresh fed in is the new D1 criterion. Old wording is not reproduced.")
    sets = {
        "[A]": (enriched, enriched, pa.total_graded(enriched)),
        "[C]": (pop["reps"], pop["reps_by_type"], pop["n_tickers_graded"]),
    }
    for label, (rows, rt_rows, n) in sets.items():
        bands = pa.calibration_by_score_band(rows)
        sa = pa.sentiment_alignment_summary(pa.calibration_by_verdict(rows),
                                            min_n=PREDICTIVE_MIN_BAND_N)
        out = _directives(bands, pa.acted_vs_missed_comparison(rows), pa.by_conviction(rows),
                          pa.by_rec_type_stats(rt_rows), pa.by_sector_alpha(rows), n, sa)
        print(f"\n{label}:")
        for d in out:
            print(f"  [{d['type']}] ({d['source_tab']}) {d['text']}")


def s9_collapse(pop) -> None:
    _hdr("§9 COLLAPSE DIAGNOSTICS")
    surf = pop["surfacings_by_ticker"]
    if surf:
        vals = list(surf.values())
        print(f"surfacings per ticker: median={statistics.median(vals)} max={max(vals)}")
        top = sorted(surf.items(), key=lambda kv: -kv[1])[:10]
        print("top 10: " + ", ".join(f"{t}={c}" for t, c in top))
    by_tk = defaultdict(list)
    for r in pop["scoped_raw"]:
        if r.get("ticker"):
            by_tk[r["ticker"]].append(r)
    spans = []
    for rows in by_tk.values():
        ds = [r["rec_date"] for r in rows if r.get("rec_date")]
        if ds:
            spans.append((max(ds) - min(ds)).days)
    if spans:
        print(f"first→last span days: median={statistics.median(spans)} max={max(spans)}")
    oos = [r["ticker"] for r in pop["reps"] if r.get("rec_type") not in pa.ACTIONABLE_REC_TYPES]
    print(f"out-of-scope acted anchors ({len(oos)}): {', '.join(sorted(oos)) or '—'}")
    late = []
    for rep in pop["reps"]:
        if _graded(rep):
            continue
        rd = rep.get("rec_date")
        if any(_graded(r) and rd is not None and r.get("rec_date") is not None
               and r["rec_date"] > rd for r in by_tk.get(rep["ticker"], [])):
            late.append(rep["ticker"])
    print(f"ungraded reps whose ticker has a LATER graded row ({len(late)}): "
          f"{', '.join(sorted(late)) or '—'}")


def caveats() -> None:
    _hdr("CAVEATS")
    print("- Outcomes are to TODAY's price, not a fixed horizon (except §7's true Day+20).")
    print("- Acted outcomes are marked from the fill to today's price, as if still held -- not realized P&L.")
    print("- [C] collapses to each ticker's first graded/priced surfacing; later surfacings are not graded.")
    print("- Per-band N is small; a pattern here is a lead, not proof. Evidence only -- no retuning.")


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--skip-forward", action="store_true",
                    help="skip the true Day+20 forward-alpha fetches in §7")
    args = ap.parse_args()

    recs_df = load_table("recommendations")
    if recs_df is None:
        return 1
    if recs_df.empty:
        print("No recommendations exist yet. Nothing to audit.")
        return 0
    trades_df = load_table("trades")
    if trades_df is None:
        return 1
    # Paginated by id for a stable read; re-sort to the traded_at-desc order
    # db.load_trades / offense_attribution use, since match_recs_to_trades
    # keeps the first same-day trade it iterates.
    if not trades_df.empty and "traded_at" in trades_df.columns:
        trades_df = trades_df.sort_values("traded_at", ascending=False, kind="stable")

    from stock_analyzer.market_time import today_et
    matched = match_recs_to_trades(recs_df, trades_df)
    spy = build_spy_close_by_date()
    prices = fetch_current_prices(sorted({r["ticker"] for r in matched if r.get("ticker")}))
    enriched = compute_outcomes(matched, prices, today_et(),
                                spy_close_by_date=spy, min_days=REC_SCORE_MIN_DAYS)
    pop = pa.prepare_population(enriched)

    s0_integrity(recs_df, trades_df, enriched)
    s1_funnel(enriched, pop)
    s2_calibration(enriched, pop)
    s3_discretion(enriched, pop)
    s4_signal(enriched, pop)
    s5_sector(enriched, pop)
    s6_sentiment(enriched, pop)
    s7_entry_timing(pop, spy, args.skip_forward)
    s8_directives(enriched, pop)
    s9_collapse(pop)
    caveats()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
