#!/usr/bin/env python3
"""Holding-period / entry-discipline analysis (roadmap item A4).

`docs/plans/investor-maturity-roadmap.md` §4 A4 -- alternative #2 of the six
strategy alternatives that analysis ranked by evidence support. Runs FIRST
of the roadmap's read-only analyses because it is upstream of a build
decision: `exit_advisor`'s deterioration ladder needs 2-of-3 sessions below
SMA50 plus a drawdown from peak to confirm TRIM/EXIT -- structurally a
multi-WEEK mechanism. `scripts/exit_ladder_replay.py` (W6) already found
52% of 54 closed losing round trips got no protective signal at all, and
most were held 0-10 days. THIS script asks the question that finding left
open: is that a genuine strategy/design mismatch (positions closed before
any weeks-scale mechanism has room to act), or an artefact of small /
deliberately-short trades that were never meant to be held that long?

The answer gates the roadmap's own B1 item (score-history decay readout,
also a weeks-scale signal): if the real holding period is mostly days, a
future decay readout may be answering a question that doesn't apply to
this book. This script does not decide that -- it reports the distribution
so that decision can be made with real numbers instead of the single
"most were held 0-10 days" data point W6 already surfaced.

WHAT THIS DOES. Builds every completed FIFO-matched round-trip lot fragment
(wins AND losses -- deliberately not scoped to losers only, unlike W6,
because a holding-period mismatch would show up across outcomes, not just
losing ones) via `investor_mirror.build_closed_lots`, the SAME pure
FIFO-lot replay the My Edge / Investor Mirror page uses -- not a
re-implementation. Reports:
  1. Holding-period distribution in buckets (days), overall and split by
     win/loss -- reuses `investor_mirror.disposition_effect` for the
     share-weighted winner/loser average (do not write a second average).
  2. Position-size cut: dollar exposure (shares x buy_price) split into
     terciles, holding period by tercile.
  3. OPTIONAL, separately dated: an app-aligned vs self-initiated cut via
     `self_track_record.classify_sells`, restricted to sells on/after
     `SELF_TRACK_SELL_RELIABLE_LOG_START` -- a materially shorter window
     than the full trade history, so this is reported as a distinct,
     labelled subset, never blended into the primary distribution.
  4. Two cross-references joining that same classify_sells() output back to
     the closed-lot fragments from item 1 (a join classify_sells never does
     itself -- it operates on raw SELL trade rows, not FIFO lot fragments):
     (a) hold-duration bucket x engine_aligned/self_initiated counts, and
     (b) realized $ pnl_abs summed by the same two buckets. Both answer
     "are short holds mechanical (engine-driven) or discretionary
     (self-initiated)?" -- the open question this docstring's second
     paragraph raised. The join is id-based, not date-string-based, to
     avoid a documented mixed-ISO-offset date-parsing disagreement between
     the two functions' independent date derivations -- see
     `_sell_id_key_map` for why.

REDLINE. Read-only historical measurement. Touches no gate, no constant, no
recommendation, no threshold in constants.py. If the results argue for a
policy change (position sizing, holding-period discipline, or re-scoping
the deterioration ladder's confirmation window), that is a separate policy
conversation with the owner -- this script produces evidence, not a
recommendation.

HONEST CAVEAT, printed on every run: this reads the FULL trade journal, not
just closed losers, so N is larger than W6's -- but still a single retail
account's history. A short average holding period is not by itself a
verdict on strategy quality; it could reflect deliberate short-term trades,
stop-outs, or profit-taking working as designed. Read the win/loss split
and the position-size cut before drawing a conclusion from the headline
median alone.

NOT A FALSIFIABLE-CRITERION SCRIPT (unlike offense_attribution.py's
pre-registered pass/fail). A4's own question -- "is this a real mismatch or
an artefact of small/short trades" -- is a judgment call for the owner to
make from the reported distribution, not a criterion this script can
mechanically resolve. It reports; it does not conclude.

Requires:
  - Supabase credentials (SUPABASE_URL / SUPABASE_KEY) to read `trades` and
    `exit_signals` -- this project's DB is hosted-only (see CLAUDE.md), so
    run this with the same env vars Railway uses. Per
    `exit_ladder_replay.py`'s own note: the Railway Console shell is NOT a
    usable environment for this -- run from a normal shell instead.
  - No price history / yfinance calls -- this script never fetches prices,
    it only replays the trade journal already in Supabase.
  - `requests` (already a project dependency) -- talks to Supabase's
    PostgREST endpoint directly, same reasoning as exit_ladder_replay.py:
    a single read-only GET has no reason to pull in the `supabase` SDK's
    heavier dependency chain (pyiceberg etc.) for this.

Usage:
    python scripts/holding_period_analysis.py
    python scripts/holding_period_analysis.py --ticker MU   # one ticker only
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import investor_mirror  # noqa: E402
from stock_analyzer import self_track_record as _stv  # noqa: E402
from stock_analyzer.constants import (  # noqa: E402
    BEHAVIORAL_MIN_SAMPLE_N,
    SELF_TRACK_SELL_RELIABLE_LOG_START,
    SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
)

# Holding-period buckets (days), upper-bound inclusive. Display buckets only
# -- not a threshold, not written to constants.py (no gate/policy reads these).
_BUCKETS = [
    (0, 3,   "0-3d"),
    (4, 10,  "4-10d"),
    (11, 20, "11-20d"),
    (21, 45, "21-45d"),
    (46, 90, "46-90d"),
    (91, None, "91d+"),
]


def _bucket_label(days: int) -> str:
    for lo, hi, label in _BUCKETS:
        if hi is None:
            if days >= lo:
                return label
        elif lo <= days <= hi:
            return label
    return "?"


def _rest_get(table: str, select: str, order: str | None = None) -> "pd.DataFrame | None":
    """Read-only PostgREST GET, mirroring exit_ladder_replay.load_trades().

    Returns `None` when credentials are absent or the request itself fails
    (network, auth, RLS) -- distinguished from a genuinely empty table,
    which returns an empty DataFrame.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    params = {"select": select}
    if order:
        params["order"] = order
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


def load_trades() -> "pd.DataFrame | None":
    return _rest_get("trades", "*", order="traded_at.desc")


def load_exit_signals() -> "pd.DataFrame | None":
    return _rest_get("exit_signals", "*", order="signal_date.desc")


def _tercile_labels(values: pd.Series) -> pd.Series:
    """Small/Medium/Large position-size terciles, by RELATIVE RANK rather
    than raw quantile bin edges. A retail trade journal routinely has tied
    dollar-exposure values (e.g. several trades sized to the same $1,000
    round number) -- a raw pd.qcut on the values themselves raises on
    duplicate bin edges even when there is genuine spread across the full
    sample, which would misreport "insufficient spread" when the real
    issue is just ties. Ranking first (method='first' breaks ties by
    original order, never dropping a row) guarantees three genuine terciles
    whenever there are >= 3 distinct rows, at the cost of an assignment
    that can put two identically-sized trades in different terciles --
    an acceptable trade for "always report a real tercile cut."
    Only falls back to a single bucket when there are truly fewer than 3
    rows to rank."""
    if len(values) < 3:
        return pd.Series(["All (< 3 rows)"] * len(values), index=values.index)
    ranks = values.rank(method="first")
    return pd.qcut(ranks, 3, labels=["Small", "Medium", "Large"])


def _print_distribution(lots: pd.DataFrame, ticker: str | None) -> None:
    n = len(lots)
    print(
        f"\n{'=' * 78}\n"
        f"HONEST CAVEAT: N = {n} completed round-trip lot fragment(s) "
        f"(wins AND losses). A short\naverage holding period is not by "
        f"itself a verdict on strategy quality -- read the\nwin/loss split "
        f"and position-size cut below before concluding from the median "
        f"alone.\nThis is a JUDGMENT-CALL report, not a pass/fail "
        f"criterion (unlike offense_attribution.py).\n{'=' * 78}\n"
    )
    if n == 0:
        print("No completed round trips found. Nothing to analyze.")
        return

    valid = lots.dropna(subset=["days_held", "pnl_abs", "shares", "buy_price"])
    if valid.empty:
        print("No round trips had complete days_held/pnl_abs/shares/buy_price data.")
        return

    valid = valid.copy()
    valid["bucket"] = valid["days_held"].astype(int).apply(_bucket_label)
    valid["outcome"] = valid["pnl_abs"].apply(lambda p: "WIN" if p >= 0 else "LOSS")
    valid["dollar_exposure"] = valid["shares"] * valid["buy_price"]

    # ── 1. Overall holding-period distribution ──────────────────────────────
    print(f"HOLDING-PERIOD DISTRIBUTION (N={len(valid)}{f', ticker={ticker}' if ticker else ''}):")
    order = [b[2] for b in _BUCKETS]
    counts = valid["bucket"].value_counts().reindex(order, fill_value=0)
    for label, cnt in counts.items():
        pct = cnt / len(valid) * 100.0
        bar = "#" * int(round(pct / 2))
        print(f"  {label:<10} {cnt:>4} ({pct:5.1f}%)  {bar}")
    median_days = valid["days_held"].median()
    print(f"\n  Median holding period: {median_days:.0f} days")

    # ── 2. Win/loss split via distribution + disposition_effect() reuse ─────
    print("\nBY OUTCOME:")
    for outcome in ("WIN", "LOSS"):
        sub = valid[valid["outcome"] == outcome]
        if sub.empty:
            print(f"  {outcome}: no rows")
            continue
        sub_counts = sub["bucket"].value_counts().reindex(order, fill_value=0)
        dist = ", ".join(f"{lbl}={cnt}" for lbl, cnt in sub_counts.items() if cnt > 0)
        print(f"  {outcome} (N={len(sub)}, median {sub['days_held'].median():.0f}d): {dist}")

    disp = investor_mirror.disposition_effect(lots, min_n=BEHAVIORAL_MIN_SAMPLE_N)
    if disp is None:
        print(
            f"\n  disposition_effect(): below BEHAVIORAL_MIN_SAMPLE_N="
            f"{BEHAVIORAL_MIN_SAMPLE_N} winners or losers -- no share-weighted "
            f"avg reported (this is the SAME house floor used elsewhere in "
            f"the app for exactly this reason: too few samples per bucket "
            f"renders a misleading pattern)."
        )
    else:
        print(
            f"\n  disposition_effect() [share-weighted, from investor_mirror.py -- "
            f"not re-derived here]:\n"
            f"    Winners held {disp['winner_avg_days']}d avg (N={disp['n_winners']})\n"
            f"    Losers  held {disp['loser_avg_days']}d avg (N={disp['n_losers']})\n"
            f"    Ratio (loser/winner): {disp['ratio']}"
        )

    # ── 3. Position-size (dollar exposure) cut ───────────────────────────────
    print("\nBY POSITION SIZE (dollar exposure = shares x buy_price, terciles):")
    valid["size_tercile"] = _tercile_labels(valid["dollar_exposure"])
    for tercile in valid["size_tercile"].cat.categories if hasattr(valid["size_tercile"], "cat") else []:
        sub = valid[valid["size_tercile"] == tercile]
        if sub.empty:
            continue
        lo, hi = sub["dollar_exposure"].min(), sub["dollar_exposure"].max()
        print(
            f"  {tercile:<8} (${lo:,.0f}-${hi:,.0f}, N={len(sub)}): "
            f"median {sub['days_held'].median():.0f}d held"
        )
    if not hasattr(valid["size_tercile"], "cat"):
        print(f"  Fewer than 3 completed round trips (N={len(valid)}) -- no tercile cut possible.")


def _print_self_track_cut(trades_df: pd.DataFrame, exit_signals_df) -> list[dict] | None:
    """Returns the raw `classify_sells()` output (or `None`/`[]`) so callers
    downstream (the hold-duration x classification cross-tab, item 4 in the
    module docstring) can reuse it without a second call -- classify_sells
    is deterministic given the same inputs, but there is no reason to pay
    for the iteration twice in one process."""
    print(
        f"\n{'-' * 78}\n"
        f"OPTIONAL CUT -- app-aligned vs self-initiated SELLS, restricted to sells "
        f"on/after\n{SELF_TRACK_SELL_RELIABLE_LOG_START} "
        f"(SELF_TRACK_SELL_RELIABLE_LOG_START). This window is MATERIALLY "
        f"SHORTER\nthan the full trade history above -- report it separately, "
        f"never blend it into the\nprimary distribution.\n{'-' * 78}\n"
    )
    if exit_signals_df is None:
        print(
            "exit_signals could not be read (see error above, or credentials "
            "issue) -- skipping this cut. The primary holding-period "
            "distribution above is unaffected."
        )
        return None
    classified = _stv.classify_sells(
        trades_df, exit_signals_df,
        SELF_TRACK_SELL_RELIABLE_LOG_START, SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS,
    )
    if classified is None:
        print("classify_sells() returned None unexpectedly -- skipping this cut.")
        return None
    if not classified:
        print("No SELL rows to classify.")
        return classified
    buckets: dict[str, int] = {}
    for row in classified:
        buckets[row["bucket"]] = buckets.get(row["bucket"], 0) + 1
    for bucket, cnt in buckets.items():
        print(f"  {bucket}: {cnt}")
    n_reliable = buckets.get("engine_aligned", 0) + buckets.get("self_initiated", 0)
    print(
        f"\n  N={n_reliable} sells within the reliable-log window "
        f"(coverage_limited rows are pre-{SELF_TRACK_SELL_RELIABLE_LOG_START} "
        f"and excluded from this split by definition)."
    )
    return classified


def _sell_id_key_map(trades_df: pd.DataFrame) -> dict[tuple, list]:
    """Maps (ticker, lot-consistent sell date) -> [trade id, ...] for every
    SELL row in `trades_df`, using the EXACT SAME UTC-normalization
    `investor_mirror.build_closed_lots` uses for its own `sell_date` field
    (`pd.to_datetime(..., utc=True, format="ISO8601").dt.date`).

    This project has a documented history of two independently-computed
    date derivations from the same raw `traded_at` string disagreeing by a
    calendar day for a non-UTC-zero offset timestamp (see
    feedback_pandas_mixed_tz_parsing.md) -- `classify_sells()` derives its
    own sell-date via `recommendations_history._to_date`, a raw string-slice
    on the first 10 characters, which is NOT the same computation. Joining
    on either function's own date field directly, trusting they already
    agree, would silently mis-join on exactly that class of row. Instead
    this function recomputes the date independently, matching
    build_closed_lots bit-for-bit, and the caller joins to classify_sells'
    output via trade `id` only -- never via a second, possibly-disagreeing
    date string.

    A list of ids per key (not a single id) because more than one SELL
    trade for the same ticker on the same UTC calendar date is possible and
    must be handled, not crash -- see `_classify_lots_by_self_track` for how
    the resulting join ambiguity is resolved.
    """
    if trades_df is None or trades_df.empty:
        return {}
    ts = pd.to_datetime(trades_df["traded_at"], errors="coerce", utc=True, format="ISO8601")
    key_map: dict[tuple, list] = {}
    for idx, row in trades_df.iterrows():
        action = str(row.get("action", "") or "").strip().upper()
        if action != "SELL":
            continue
        t = ts.loc[idx]
        if pd.isna(t):
            continue
        # No .strip() -- must match build_closed_lots' ticker normalization
        # (investor_mirror.py: str(ticker).upper(), no strip) bit-for-bit, or
        # a whitespace-padded ticker would key-mismatch here but not there.
        tk = str(row.get("ticker", "") or "").upper()
        if not tk:
            continue
        key_map.setdefault((tk, t.date()), []).append(row.get("id"))
    return key_map


def _classify_lots_by_self_track(
    lots: pd.DataFrame, trades_df: pd.DataFrame, classified: list[dict],
) -> pd.Series:
    """Attributes each closed-lot fragment in `lots` a self-track bucket by
    joining on trade `id` (never on either function's own date field --
    see `_sell_id_key_map`'s docstring).

    Per fragment:
      - zero matching SELL ids at (ticker, sell_date) -> "unmatched"
      - exactly one matching id -> that id's own classify_sells bucket
        (engine_aligned / self_initiated / coverage_limited)
      - more than one matching id (same ticker, same UTC calendar day,
        multiple SELL trades) -> if every matching id shares the SAME
        bucket, attribute that bucket unambiguously (no real ambiguity in
        that case); otherwise "ambiguous_same_day_multi_sell" -- a lot
        fragment carries no originating trade id of its own
        (build_closed_lots never tracks one), so which of several
        differently-classified same-day sells produced a given fragment is
        genuinely unknowable and must not be guessed.

    Future refinement, explicitly NOT built here: cross-checking against
    exit_signals' own `trigger_type` (e.g. distinguishing a hard stop-out
    from a discretionary TRIM) could sharpen the engine_aligned bucket
    further, but is out of scope for this pass.
    """
    id_to_bucket = {row["id"]: row["bucket"] for row in classified}
    key_map = _sell_id_key_map(trades_df)
    buckets: list[str] = []
    for _, lot in lots.iterrows():
        tk = str(lot["ticker"]).upper()
        d = lot["sell_date"]
        ids = key_map.get((tk, d), [])
        if not ids:
            buckets.append("unmatched")
            continue
        # Drop ids classify_sells couldn't resolve (id_to_bucket.get -> None)
        # BEFORE judging ambiguity -- a single matching id with no bucket is
        # "unmatched", not "ambiguous" (ambiguity means >=2 REAL, DIFFERING
        # classifications, not one real classification plus a missing one).
        distinct = {id_to_bucket.get(i) for i in ids}
        distinct.discard(None)
        if len(distinct) == 1:
            buckets.append(next(iter(distinct)))
        elif len(distinct) == 0:
            buckets.append("unmatched")
        else:
            buckets.append("ambiguous_same_day_multi_sell")
    return pd.Series(buckets, index=lots.index)


def _print_hold_duration_cross_tab(lots: pd.DataFrame) -> None:
    """Section A -- hold-duration bucket x engine_aligned/self_initiated
    counts, restricted to fragments joined within the reliable-log window.
    `lots` must already carry a `self_track_bucket` column."""
    print(
        "\nSECTION A -- HOLD-DURATION BUCKET x CLASSIFICATION "
        "(engine_aligned vs self_initiated):"
    )
    valid = lots.dropna(subset=["days_held"]).copy()
    if valid.empty:
        print("  No completed round trips with days_held data -- nothing to cross-tab.")
        return
    valid["hold_bucket"] = valid["days_held"].astype(int).apply(_bucket_label)

    coverage_limited_n = int((valid["self_track_bucket"] == "coverage_limited").sum())
    ambiguous_n = int((valid["self_track_bucket"] == "ambiguous_same_day_multi_sell").sum())
    unmatched_n = int((valid["self_track_bucket"] == "unmatched").sum())
    reliable = valid[valid["self_track_bucket"].isin(["engine_aligned", "self_initiated"])]

    print(
        f"  coverage_limited (pre-reliable-log sells, excluded from this cross-tab): "
        f"{coverage_limited_n}\n"
        f"  ambiguous_same_day_multi_sell (excluded, cannot disambiguate which "
        f"same-day sell\n  produced this fragment): {ambiguous_n}\n"
        f"  unmatched (no corresponding classify_sells SELL row found, excluded): "
        f"{unmatched_n}\n"
    )
    if reliable.empty:
        print("  No engine_aligned/self_initiated fragments to cross-tab.")
        return
    order = [b[2] for b in _BUCKETS]
    print(f"  N={len(reliable)} reliable-window fragments:")
    for label in order:
        sub = reliable[reliable["hold_bucket"] == label]
        if sub.empty:
            continue
        ea = int((sub["self_track_bucket"] == "engine_aligned").sum())
        si = int((sub["self_track_bucket"] == "self_initiated").sum())
        print(f"    {label:<10} engine_aligned={ea:>3}  self_initiated={si:>3}  (N={len(sub)})")


def _print_pnl_by_classification(lots: pd.DataFrame) -> None:
    """Section B -- realized $ pnl_abs summed by engine_aligned vs
    self_initiated, restricted to fragments joined within the reliable-log
    window. `lots` must already carry a `self_track_bucket` column."""
    print("\nSECTION B -- REALIZED $ P&L BY CLASSIFICATION:")
    valid = lots.dropna(subset=["pnl_abs"]).copy()
    if valid.empty:
        print("  No fragments with pnl_abs data -- nothing to sum.")
        return
    for bucket in ("engine_aligned", "self_initiated"):
        sub = valid[valid["self_track_bucket"] == bucket]
        total = sub["pnl_abs"].sum() if not sub.empty else 0.0
        print(f"  {bucket:<15} N={len(sub):>4}  realized P&L = ${total:,.2f}")
    for bucket in ("coverage_limited", "ambiguous_same_day_multi_sell", "unmatched"):
        sub = valid[valid["self_track_bucket"] == bucket]
        total = sub["pnl_abs"].sum() if not sub.empty else 0.0
        print(
            f"  {bucket:<15} N={len(sub):>4}  realized P&L = ${total:,.2f}  "
            f"(reported for visibility, excluded from the grouped total above)"
        )


def _print_self_track_join(
    lots: pd.DataFrame, trades_df: pd.DataFrame, classified: list[dict] | None,
) -> None:
    """Item 4 of the module docstring: cross-references classify_sells()'s
    per-SELL classification back onto the closed-lot fragments from
    `build_closed_lots`, via the id-based join in
    `_classify_lots_by_self_track` (never a date-string join -- see
    `_sell_id_key_map`)."""
    print(
        f"\n{'-' * 78}\n"
        f"HOLD-DURATION x CLASSIFICATION CROSS-REFERENCE. Covers ONLY the same "
        f"reliable-log\nwindow as the cut above (sells on/after "
        f"{SELF_TRACK_SELL_RELIABLE_LOG_START}) for its\nengine_aligned/"
        f"self_initiated grouped conclusions -- coverage_limited fragments "
        f"(pre-cron-\nreliability sells) are counted and shown but never "
        f"folded into those grouped stats.\nThis N is MATERIALLY SMALLER "
        f"than the full holding-period distribution printed above,\nwhich "
        f"spans the entire trade history -- do not read this section as "
        f"covering everything.\n\n"
        f"'engine_aligned' means an EXIT/TRIM exit_signals row fired within "
        f"SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS={SELF_TRACK_SELL_SIGNAL_WINDOW_DAYS} "
        f"days before the sell --\na CORRELATIONAL signal-window match, NOT "
        f"proof the signal caused the sell. A\ncoincidental signal counts. "
        f"This is a JUDGMENT-CALL report, not a pass/fail criterion --\nsame "
        f"framing as the rest of this script.\n{'-' * 78}\n"
    )
    if classified is None:
        print(
            "exit_signals could not be read -- skipping the hold-duration x "
            "classification cross-tab and the P&L-by-bucket sections. The "
            "sections above are unaffected."
        )
        return
    if lots is None or lots.empty:
        print("No completed round-trip lot fragments to cross-reference.")
        return
    if not classified:
        print("No SELL rows were classified -- nothing to cross-reference.")
        return
    joined = lots.copy()
    joined["self_track_bucket"] = _classify_lots_by_self_track(joined, trades_df, classified)
    _print_hold_duration_cross_tab(joined)
    _print_pnl_by_classification(joined)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--ticker", default=None, help="restrict to one ticker")
    args = parser.parse_args()

    trades_df = load_trades()
    if trades_df is None:
        print(
            "No trades could be read. Either Supabase credentials "
            "(SUPABASE_URL / SUPABASE_KEY) are not set in this shell's "
            "environment, or the request itself failed -- see any error above."
        )
        return 1
    if trades_df.empty:
        print("Connected fine -- the trades table is genuinely empty. Nothing to analyze.")
        return 0

    if args.ticker:
        want = args.ticker.strip().upper()
        trades_df = trades_df[trades_df["ticker"].astype(str).str.upper() == want]
        if trades_df.empty:
            print(f"No trades found for ticker {want}.")
            return 0

    lots = investor_mirror.build_closed_lots(trades_df)
    _print_distribution(lots, args.ticker)

    exit_signals_df = load_exit_signals()
    classified = _print_self_track_cut(trades_df, exit_signals_df)
    _print_self_track_join(lots, trades_df, classified)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
