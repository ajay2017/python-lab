#!/usr/bin/env python3
"""Offense attribution -- engine ranking skill vs owner selection (roadmap A2).

`docs/plans/investor-maturity-roadmap.md` §4 A2. The Engine Track Record card
(🧾 Summary / F-229) reports **+14.4pp acted alpha over 23 matured `new_pick`s**
(live check 2026-08-30) -- but the app cannot currently tell whether that
number reflects the engine's own RANKING skill (higher composite -> better
forward alpha, for everyone who saw the pick) or the owner's own SELECTION
skill (picking the better calls out of what the engine surfaced, regardless
of the engine's own ranking). Those are different claims and the current
headline conflates them.

WHAT THIS DOES. Reuses the EXACT enrichment chain the live Engine Track
Record card itself uses -- `recommendations_history.match_recs_to_trades`
-> `compute_outcomes` (do not write a second alpha; F-259 §5's rule, reused
again here) -- so this script's numbers are directly comparable to the live
headline, not a parallel re-derivation of it. Buckets every priced, mature
`new_pick` row by its composite-score band (65-69 / 70-74 / 75-79 / 80+)
and reports two things per band:
  (a) mean alpha_pct across ALL rows in the band (acted + skipped) -- does
      alpha rise with the composite band at all, for the population the
      engine actually surfaced (not just what was traded)?
  (b) within each band, acted alpha vs skipped alpha -- if these track apart
      WITHIN a band that has similar engine quality, that gap is evidence of
      owner selection skill rather than engine ranking skill.

A SECOND, age-confound-controlled lens is layered on top of (a)/(b): the
to-today `compute_outcomes` alpha lets an older rec accumulate more (or
less) return than a younger one purely from elapsed time, which could bias
a band comparison if bands happen to skew toward different average ages.
`predictive_analytics.forward_alpha_at_horizon` (the SAME fixed-horizon
alpha function `gate_ledger_readout.py` already uses and trusts -- reused,
not reimplemented a second time) gives every gradable row an identical
`GATE_LEDGER_HORIZON_TRADING_DAYS`-day window regardless of rec age, as a
robustness check on whatever the first lens finds.

`recommendations_history.distinct_missed` is reused as a THIRD,
independent cross-check -- its own per-ticker-deduped "missed" view, which
this script's row-level "skipped" tally should roughly agree with (not
identical: one counts per-surfacing rows, the other collapses to one row
per distinct ticker). Reported side-by-side, never blended into the primary
per-band numbers.

PRE-REGISTERED CRITERION (fixed BEFORE this script's first real run,
2026-09-13, matching the roadmap doc verbatim):
  - Alpha rises with composite band across ALL calls, AND acted ~= skipped
    within each band  ==>  engine RANKING skill is real; the existing
    headline stands as an engine-quality claim.
  - Alpha is flat across bands, AND acted >> skipped within band(s)
    ==>  the engine is a candidate GENERATOR and the owner is the FILTER;
    the Engine Track Record headline should be re-labelled to say so
    rather than implying the composite itself is what is being validated.
  - Anything else (e.g. band N too small to read, mixed/non-monotonic
    pattern) ==> report as inconclusive. This script does not force a
    verdict onto a small or ambiguous sample.

REDLINE. Read-only historical measurement. Touches no gate, no constant, no
recommendation, no threshold in constants.py, and it does not change how
the live Engine Track Record card computes or displays its own headline --
it explains that headline, it does not alter it.

HONEST CAVEATS, printed on every run: (1) per-band N will likely be small
(N=23 acted all-time as of 2026-08-30) -- a clean monotonic pattern in a
small sample is suggestive, not proof; (2) `compute_outcomes`'s alpha is
computed to TODAY, not a fixed horizon, so an older rec has had more time
to compound than a younger one in a different band -- the forward_alpha
lens exists specifically to check whether that confound is doing the work;
(3) this reads recommendations exactly as the live card does (`rec_type ==
"new_pick"` only) -- `buy_candidate` and `add_winner` rows are excluded,
matching the headline's own scope, not this script's choice to narrow it.

Requires:
  - Supabase credentials (SUPABASE_URL / SUPABASE_KEY) to read `recommendations`
    and `trades` -- run from a normal shell with Railway's env vars (the
    Railway Console shell is not usable for this; see exit_ladder_replay.py's
    own note).
  - Network access for two live fetches this script CANNOT avoid: SPY price
    history (`stock_analyzer.data.fetch_spy`) and, for the forward_alpha
    lens only, one historical-close lookup per gradable ticker via
    `predictive_analytics.forward_alpha_at_horizon`'s default
    `providers.orchestrator.get_historical_close` -- unlike the primary
    compute_outcomes lens, which only needs CURRENT live prices
    (`stock_analyzer.data.fetch_live_prices`, one batch call).

Usage:
    python scripts/offense_attribution.py
    python scripts/offense_attribution.py --skip-forward-alpha   # first lens only, no extra network calls
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

from stock_analyzer import data as _data  # noqa: E402
from stock_analyzer.constants import (  # noqa: E402
    GATE_LEDGER_HORIZON_TRADING_DAYS,
    REC_SCORE_MIN_DAYS,
)
from stock_analyzer.predictive_analytics import (  # noqa: E402
    _advance_trading_days,
    forward_alpha_at_horizon,
)
from stock_analyzer.recommendations_history import (  # noqa: E402
    compute_outcomes,
    distinct_missed,
    match_recs_to_trades,
)

# Composite bands -- display buckets only, not a threshold, not written to
# constants.py (no gate/policy reads these). Mirrors the roadmap doc's own
# banding.
_BANDS = [
    (65, 69,   "65-69"),
    (70, 74,   "70-74"),
    (75, 79,   "75-79"),
    (80, None, "80+"),
]


def _band_label(score: float) -> "str | None":
    for lo, hi, label in _BANDS:
        if hi is None:
            if score >= lo:
                return label
        elif lo <= score <= hi:
            return label
    return None  # below 65 -- shouldn't occur for new_pick (composite gate), but never crash on it


def _rest_get(table: str, select: str, extra_params: dict | None = None) -> "pd.DataFrame | None":
    """Read-only PostgREST GET, mirroring exit_ladder_replay.load_trades() /
    holding_period_analysis.py's own helper. Returns None on missing creds or
    a failed request; an empty (but successful) result is a genuinely empty
    table, returned as an empty DataFrame."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    params = {"select": select}
    if extra_params:
        params.update(extra_params)
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/rest/v1/{table}",
            params=params,
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        print(f"Could not read the {table} table: {e}")
        return None
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def load_new_picks() -> "pd.DataFrame | None":
    return _rest_get("recommendations", "*", extra_params={"rec_type": "eq.new_pick"})


def load_trades() -> "pd.DataFrame | None":
    return _rest_get("trades", "*", extra_params={"order": "traded_at.desc"})


def build_spy_close_by_date(period: str = "1y") -> dict:
    """{date: close}, same shape compute_outcomes/forward_alpha_at_horizon
    both expect -- built directly from stock_analyzer.data.fetch_spy(), the
    SAME function app.py's _cached_spy() wraps (just a longer period here,
    since this script has no cache to keep warm and no reason to under-fetch:
    the live card uses "6mo", this uses "1y" so an older rec_date is still
    covered). Never re-derives the alpha math itself."""
    spy_close: dict = {}
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
                    spy_close[d] = c
    except Exception as e:
        print(f"Could not fetch SPY history: {e}")
    return spy_close


def fetch_current_prices(tickers: list[str]) -> dict:
    """{ticker: price} via ONE batch live-price fetch, mirroring the live
    Engine Track Record card's own construction (app.py ~12011) exactly."""
    if not tickers:
        return {}
    try:
        px = _data.fetch_live_prices(tickers)
        return {
            t: float(d.get("price", 0))
            for t, d in (px or {}).items()
            if d and d.get("price")
        }
    except Exception as e:
        print(f"Could not fetch current prices: {e}")
        return {}


def _mean(vals: list[float]) -> "float | None":
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _print_band_table(enriched: list[dict]) -> dict:
    """First lens: compute_outcomes' to-today alpha, bucketed by composite
    band. Returns the per-band summary dict for the pre-registered-criterion
    check that follows."""
    gradable = [
        r for r in enriched
        if not r.get("outcome_maturing")
        and r.get("alpha_pct") is not None
        and r.get("composite_score") is not None
    ]
    n_no_composite = sum(
        1 for r in enriched
        if not r.get("outcome_maturing")
        and r.get("alpha_pct") is not None
        and r.get("composite_score") is None
    )
    print(
        f"\nLENS 1 -- compute_outcomes to-today alpha (SAME methodology as the "
        f"live Engine Track Record card).\nN={len(gradable)} mature, priced, "
        f"composite-scored new_pick row(s)."
        + (f" ({n_no_composite} more mature+priced row(s) had no recorded "
           f"composite_score -- excluded from banding, not silently dropped "
           f"from this count.)" if n_no_composite else "")
        + "\n"
    )
    if not gradable:
        print("No gradable rows. Nothing to bucket.")
        return {}

    band_summary: dict = {}
    for _lo, _hi, label in _BANDS:
        rows = [r for r in gradable if _band_label(r["composite_score"]) == label]
        if not rows:
            band_summary[label] = {"n": 0, "alpha_all": None, "n_acted": 0,
                                    "alpha_acted": None, "n_skipped": 0, "alpha_skipped": None}
            print(f"  {label:<8} N=0 -- no rows in this band.")
            continue
        acted = [r for r in rows if r.get("acted_on")]
        skipped = [r for r in rows if not r.get("acted_on")]
        alpha_all = _mean([r["alpha_pct"] for r in rows])
        alpha_acted = _mean([r["alpha_pct"] for r in acted])
        alpha_skipped = _mean([r["alpha_pct"] for r in skipped])
        band_summary[label] = {
            "n": len(rows), "alpha_all": alpha_all,
            "n_acted": len(acted), "alpha_acted": alpha_acted,
            "n_skipped": len(skipped), "alpha_skipped": alpha_skipped,
        }
        print(
            f"  {label:<8} N={len(rows):<4} all-alpha={alpha_all!s:<8} | "
            f"acted N={len(acted):<3} alpha={alpha_acted!s:<8} | "
            f"skipped N={len(skipped):<3} alpha={alpha_skipped!s:<8}"
        )
    return band_summary


def _print_forward_alpha_lens(enriched: list[dict], spy_close_by_date: dict,
                               skip: bool) -> None:
    print(
        f"\nLENS 2 -- forward_alpha_at_horizon, fixed "
        f"{GATE_LEDGER_HORIZON_TRADING_DAYS}-trading-day window from rec_date "
        f"for EVERY row (age-confound control -- removes 'older recs have had "
        f"more time to compound' as a possible explanation for a band effect).\n"
    )
    if skip:
        print("Skipped (--skip-forward-alpha).")
        return
    today = date.today()
    rows_with_bands: list[tuple[str, "float | None"]] = []
    n_not_matured = 0
    for r in enriched:
        composite = r.get("composite_score")
        rec_date = r.get("rec_date")
        price_at_surface = r.get("price_at_surface")
        ticker = r.get("ticker")
        if composite is None or rec_date is None or not ticker:
            continue
        target_date = _advance_trading_days(rec_date, GATE_LEDGER_HORIZON_TRADING_DAYS)
        if target_date > today:
            n_not_matured += 1
            continue
        alpha = forward_alpha_at_horizon(
            ticker, rec_date, price_at_surface,
            GATE_LEDGER_HORIZON_TRADING_DAYS, spy_close_by_date,
        )
        label = _band_label(composite)
        if label is not None:
            rows_with_bands.append((label, alpha))
    print(
        f"N={len(rows_with_bands)} row(s) old enough to have a "
        f"{GATE_LEDGER_HORIZON_TRADING_DAYS}-day forward window "
        f"({n_not_matured} not yet matured, excluded).\n"
    )
    for _lo, _hi, label in _BANDS:
        band_rows = [a for lbl, a in rows_with_bands if lbl == label]
        priced = [a for a in band_rows if a is not None]
        n_unpriceable = len(band_rows) - len(priced)
        print(
            f"  {label:<8} N={len(band_rows):<4} fwd-alpha={_mean(priced)!s:<8}"
            + (f" ({n_unpriceable} unpriceable -- forward close not found)" if n_unpriceable else "")
        )


def _print_distinct_missed_crosscheck(enriched: list[dict]) -> None:
    print(
        "\nCROSS-CHECK -- distinct_missed() (per-ticker-deduped, "
        "recommendations_history.py's own existing view). Should be "
        "ROUGHLY consistent with LENS 1's row-level 'skipped' tally above, "
        "not identical (this collapses repeat surfacings of the same "
        "ticker to one row; Lens 1 counts every surfacing separately).\n"
    )
    missed = distinct_missed(enriched, rec_types=("new_pick",))
    if not missed:
        print("No distinct missed new_pick tickers gradable.")
        return
    alphas = [r["alpha_pct"] for r in missed if r.get("alpha_pct") is not None]
    print(f"N={len(missed)} distinct missed ticker(s). Mean alpha_pct: {_mean(alphas)}")


def _print_verdict(band_summary: dict) -> None:
    print(f"\n{'=' * 78}\nPRE-REGISTERED CRITERION CHECK\n{'=' * 78}")
    ordered_labels = [b[2] for b in _BANDS]
    populated = [(lbl, band_summary.get(lbl, {})) for lbl in ordered_labels
                 if band_summary.get(lbl, {}).get("n", 0) > 0]
    if len(populated) < 2:
        print(
            "INCONCLUSIVE: fewer than 2 populated composite bands -- cannot "
            "assess a band-vs-alpha trend from this sample. This is a real, "
            "honest 'not enough data', not a forced verdict."
        )
        return

    alphas_in_order = [b.get("alpha_all") for _, b in populated]
    if any(a is None for a in alphas_in_order):
        print(
            "INCONCLUSIVE: at least one populated band has no alpha reading "
            "(N too thin within the band). Not forcing a verdict on partial data."
        )
        return

    rising = all(alphas_in_order[i] <= alphas_in_order[i + 1] for i in range(len(alphas_in_order) - 1))
    gaps = []
    for lbl, b in populated:
        aa, sa = b.get("alpha_acted"), b.get("alpha_skipped")
        if aa is not None and sa is not None:
            gaps.append(aa - sa)
    mean_abs_gap = _mean([abs(g) for g in gaps]) if gaps else None
    acted_tracks_close = mean_abs_gap is not None and mean_abs_gap <= 3.0
    acted_diverges = bool(gaps) and any(g >= 5.0 for g in gaps)

    print(f"Band order checked (populated only): {[lbl for lbl, _ in populated]}")
    print(f"Alpha rises monotonically with band: {rising}")
    print(f"Acted vs skipped gap per band (acted - skipped): "
          f"{[round(g, 1) for g in gaps] if gaps else 'not enough acted+skipped pairs to compare'}")

    if rising and acted_tracks_close:
        print(
            "\nVERDICT: ENGINE RANKING SKILL reads as real in this sample -- alpha "
            "rises with composite band, and acted/skipped alpha track closely "
            "within band (owner selection isn't doing most of the work). The "
            "existing headline stands as an engine-quality claim."
        )
    elif (not rising) and acted_diverges:
        print(
            "\nVERDICT: reads as a CANDIDATE GENERATOR, not a ranker -- alpha is "
            "flat/non-monotonic across bands, but acted alpha clearly beats "
            "skipped alpha within at least one band. The Engine Track Record "
            "headline should be re-labelled to credit the OWNER'S selection, "
            "not the composite's ranking."
        )
    else:
        print(
            "\nVERDICT: MIXED / does not cleanly match either pre-registered "
            "pattern. Report both readings as-is rather than forcing a label -- "
            "this is itself useful information (the sample may simply be too "
            "small yet, per the honest caveats above)."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--skip-forward-alpha", action="store_true",
                         help="skip Lens 2 (saves one network call per gradable ticker)")
    args = parser.parse_args()

    recs_df = load_new_picks()
    if recs_df is None:
        print("No recommendations could be read (see error above, or missing "
              "SUPABASE_URL/SUPABASE_KEY).")
        return 1
    if recs_df.empty:
        print("Connected fine -- no new_pick recommendations exist yet. Nothing to analyze.")
        return 0

    trades_df = load_trades()
    if trades_df is None:
        print("No trades could be read (see error above, or missing credentials).")
        return 1

    print(
        f"\n{'=' * 78}\n"
        f"HONEST CAVEATS: per-band N will likely be small (roughly two dozen "
        f"acted all-time as\nof 2026-08-30) -- read N before the table. Lens 1's "
        f"alpha is to-TODAY, not a fixed\nwindow, so an older rec has had more "
        f"time to compound than a younger one -- Lens 2\nexists specifically to "
        f"check that. rec_type='new_pick' ONLY, matching the live card's own\n"
        f"scope -- buy_candidate/add_winner excluded by design, not by this "
        f"script's choice.\n{'=' * 78}"
    )

    matched = match_recs_to_trades(recs_df, trades_df)
    spy_close_by_date = build_spy_close_by_date()
    tickers = sorted({r["ticker"] for r in matched if r.get("ticker")})
    current_prices = fetch_current_prices(tickers)
    today = date.today()

    enriched = compute_outcomes(
        matched, current_prices, today,
        spy_close_by_date=spy_close_by_date, min_days=REC_SCORE_MIN_DAYS,
    )

    band_summary = _print_band_table(enriched)
    _print_forward_alpha_lens(enriched, spy_close_by_date, skip=args.skip_forward_alpha)
    _print_distinct_missed_crosscheck(enriched)
    _print_verdict(band_summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
