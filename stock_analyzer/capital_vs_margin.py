"""
Capital vs. Margin Analysis — pure computation (no I/O, no Streamlit).

Answers "was trading on margin actually worth it?" by reconstructing the
account's net-equity/leverage trajectory BACKWARD from broker-integration
go-live (~2026-08-18) to today, joined with F-266's forward-recorded
`account_daily_snapshots` history (2026-09-10+), and computing interest paid
plus the derived verdicts (margin's net contribution, break-even rate,
drawdown decomposition, a deleverage scenario, an up-week/down-week regime
split).

NO NEW DB TABLE. Everything here is computed live from `trades`,
`account_flows`, `snaptrade_income_events` and `daily_snapshots` — the
render layer (app.py) session-caches the result, keyed on a fingerprint of
the underlying data, rather than persisting it: a cache TABLE would risk
staleness against the broker feed (syncs 2x/day) and new trades, which a
session-state memo re-keyed on every data change does not.

COUNTERFACTUAL MODEL (owner decision, locked 2026-09-10 — see
docs/plans/capital-vs-margin-analysis.md): cost-of-leverage / scale, NOT a
different-decisions replay. Margin is modeled as a scale on the OWNER'S
ACTUAL positions — the synthetic "capital-only" curve is anchored at the
SAME dollar point as the actual (levered) curve (net equity on broker
go-live day), then grown at the book's own realized (unlevered) daily
return. The gap between the two curves is purely margin's effect — this
never invents a smaller/different trade the owner didn't make, which is
exactly the fabricated-number risk the app's zero-hallucination posture
exists to prevent.

Every function here is pure: no Streamlit, no DB calls, no clock reads (the
caller passes `now`/dates in, same convention as `account.py`). Formulas
that already exist in `margin.py` (`call_distance`, `shock_call_outcome`)
are reused VERBATIM, never re-derived.

INTEREST SIGN CONVENTION — UNCONFIRMED as of this build (2026-09-10).
SnapTrade files interest earned on cash and margin interest charged under
the same `event_type='interest'`; sign is the only distinguishing signal,
and which sign this account's margin charges use has not yet been verified
against real synced data (no interest events had landed as of this build).
`interest_partition`/`resolve_interest_charged` below isolate the two
magnitudes WITHOUT netting them, and every caller in this module passes
`charged_sign=None` — ship as unconfirmed/disclosed. A future session must
confirm the real convention (once real interest events exist, cross-check
against the known margin_debit x rate x days magnitude, or a broker
statement) and set `charged_sign` at the call site. Do NOT guess.
"""
from __future__ import annotations

from datetime import date as _date, datetime as _datetime, timedelta as _timedelta

import pandas as pd

from stock_analyzer.daily_pnl import today_trade_cash_delta as _trade_cash_delta
from stock_analyzer.market_time import ET as _ET_TZ
from stock_analyzer import margin as _margin


# ── Self-validation tolerance ──────────────────────────────────────────────
# Module-local, NOT a constants.py policy value: this is a data-integrity
# check on the RECONSTRUCTION MATH (does the backward roll agree with F-266's
# own recorded figures?), not an investment-policy threshold — mirrors
# `account._ANNUALIZE_CAVEAT_MAX_DAYS`'s own precedent for a module-local,
# non-policy constant living beside the pure logic it guards.
_RECON_ABS_TOL = 1.00     # dollars — rounding/timing noise floor
_RECON_REL_TOL = 0.005    # 0.5% of the recorded value, for large balances


# ── Date coercion helpers ───────────────────────────────────────────────────

def _parse_date(d) -> "_date | None":
    """Coerce a plain DATE value (flow_date / event_date / snapshot_date —
    none of which carry a time component in the DB) to a `date`, or None if
    unparseable. Never tz-converts — there is no time-of-day to convert."""
    if d is None:
        return None
    if isinstance(d, _datetime):
        return d.date()
    if isinstance(d, _date):
        return d
    try:
        return _date.fromisoformat(str(d)[:10])
    except Exception:
        return None


def _to_et_date(ts) -> "_date | None":
    """Coerce a `timestamptz` value (`traded_at`, `updated_at`) to its
    America/New_York calendar date, or None if unparseable.

    Project convention (CLAUDE.md: "date math uses America/New_York") — a
    trade filed near midnight UTC is a different ET calendar day, and
    `trades.traded_at` in particular is only safe to bucket by day once
    converted (broker-synced rows are re-anchored to 16:00 ET by
    `trade_time.normalize_traded_at` inside `db.load_trades_or_none`, so this
    conversion lands on the correct trading day for them; a manually-logged
    row with a real intraday ET time is unaffected either way).
    """
    try:
        t = pd.to_datetime(ts, utc=True, errors="coerce", format="ISO8601")
    except Exception:
        return None
    if t is None or pd.isna(t):
        return None
    return t.tz_convert(_ET_TZ).date()


# ── 1. Interest sign-isolation ──────────────────────────────────────────────

def interest_partition(income_events: list[dict]) -> dict:
    """Split `event_type='interest'` rows into unsigned magnitudes by sign,
    WITHOUT netting them (see module docstring — the sign convention itself
    is unconfirmed, so netting here would bake in a guess).

    Returns {"sum_neg_magnitude": float>=0, "sum_pos_magnitude": float>=0,
    "n_neg": int, "n_pos": int}. Rows with amount==0 are skipped (neither a
    charge nor an earning); non-numeric/NaN amounts are skipped defensively.
    """
    sum_neg = 0.0
    sum_pos = 0.0
    n_neg = 0
    n_pos = 0
    for e in income_events or []:
        if str(e.get("event_type") or "").strip().lower() != "interest":
            continue
        try:
            amt = float(e.get("amount"))
        except (TypeError, ValueError):
            continue
        if amt != amt:  # NaN
            continue
        if amt < 0:
            sum_neg += -amt
            n_neg += 1
        elif amt > 0:
            sum_pos += amt
            n_pos += 1
    return {
        "sum_neg_magnitude": sum_neg,
        "sum_pos_magnitude": sum_pos,
        "n_neg": n_neg,
        "n_pos": n_pos,
    }


def resolve_interest_charged(part: dict, charged_sign: str | None) -> dict:
    """Resolve interest CHARGED (margin) vs EARNED (cash) from `part`
    (`interest_partition`'s output), given which raw sign SnapTrade uses for
    a charge — `charged_sign` in {"negative", "positive", None}.

    `charged_sign=None` (the current, unconfirmed state — see module
    docstring): NEVER guessed. `confirmed=False` is returned, and the two
    magnitudes are assigned to "charged"/"earned" using the negative leg as
    the charged candidate — an arbitrary-but-documented display convention
    (negative-signed = "money left the account" is the more common
    bookkeeping convention) — the caller MUST disclose `confirmed=False`
    rather than presenting the split as fact.
    """
    sum_neg = part.get("sum_neg_magnitude", 0.0)
    sum_pos = part.get("sum_pos_magnitude", 0.0)
    if charged_sign == "negative":
        return {"charged": sum_neg, "earned": sum_pos, "confirmed": True}
    if charged_sign == "positive":
        return {"charged": sum_pos, "earned": sum_neg, "confirmed": True}
    return {"charged": sum_neg, "earned": sum_pos, "confirmed": False}


# ── 2. Anchor selection + backward cash reconstruction ─────────────────────

def resolve_anchor(account_cash_rec: dict | None, recorded_df, stale_days_limit: int, now) -> "dict | None":
    """Pick the freshest known net-cash figure to anchor the backward
    reconstruction.

    Preference order: (1) the LIVE `account_cash` record, if fresh
    (`age_days <= stale_days_limit`, inclusive — matching
    `margin.resolve_net_capital`'s own staleness boundary); (2) else the
    latest `account_daily_snapshots` (F-266) row carrying a non-null
    `cash_balance`; (3) else None — "can't compute", the caller shows a
    message rather than guessing.

    `recorded_df` is `db.load_account_daily_snapshots()`'s shape (or
    None/empty). `now` is a tz-aware "current time" the caller reads via
    `market_time.now_et()` — this function does no clock read of its own.

    Returns {"cash": float, "date": date, "src": "live"|"recorded"} or None.
    """
    if account_cash_rec and account_cash_rec.get("updated_at"):
        cash_balance = account_cash_rec.get("cash_balance")
        if cash_balance is not None:
            try:
                age_days = (now - pd.to_datetime(account_cash_rec["updated_at"], utc=True)).days
            except Exception:
                age_days = None
            if age_days is not None and age_days <= stale_days_limit:
                d = _to_et_date(account_cash_rec["updated_at"])
                if d is not None:
                    return {"cash": float(cash_balance), "date": d, "src": "live"}

    if recorded_df is not None and not recorded_df.empty and "cash_balance" in recorded_df.columns:
        df = recorded_df.copy()
        df["_cb"] = pd.to_numeric(df["cash_balance"], errors="coerce")
        df = df[df["_cb"].notna()]
        if not df.empty:
            df["_d"] = df["snapshot_date"].apply(_parse_date)
            df = df[df["_d"].notna()]
            if not df.empty:
                row = df.sort_values("_d").iloc[-1]
                return {"cash": float(row["_cb"]), "date": row["_d"], "src": "recorded"}

    return None


def golive_floor(trades_df, flows_df, income_events: list[dict], daily_snapshots_df) -> "_date | None":
    """The earliest date the backward reconstruction can be trusted to.

    Reconstruction needs BOTH a complete cash ledger AND a gross-book figure
    for every day in the window, so the floor is the LATER of: (a) the
    earliest date across broker-synced ledger rows, and (b) the earliest
    date `daily_snapshots` actually covers.

    Broker-synced detection, per table (checked against the actual schema
    each loader returns):
      - `trades_df`: rows carrying a non-null `broker_txn_id`
        (SnapTrade-imported; `db.load_trades()`/`load_trades_or_none()`
        `select("*")`, so the column is present whenever the DDL has it).
      - `income_events`: EVERY row is broker-synced by construction —
        `db.save_snaptrade_income_events` only ever upserts rows carrying a
        `snaptrade_txn_id` (id-less rows are dropped before saving, and
        `load_snaptrade_income_events` doesn't even select that column back)
        — so a loaded income event is never a manual entry, and its
        `event_date` is usable directly.
      - `flows_df`: `db.load_account_flows()` selects only
        `id,flow_date,flow_type,amount,note` — no sync-id column is exposed
        by that loader — so `flows_df` is NOT used for floor DETECTION here
        (there is no way to tell a manual baseline/deposit row from a
        broker-synced one in this shape). It is still a real input to
        `reconstruct_daily_cash` below; it's excluded only from picking the
        go-live date.

    Returns None when no broker-synced ledger rows exist at all, or when
    `daily_snapshots_df` has no usable dates — never fabricates a date.
    """
    candidates = []

    if trades_df is not None and not trades_df.empty and "broker_txn_id" in trades_df.columns:
        synced = trades_df[trades_df["broker_txn_id"].notna()]
        if not synced.empty and "traded_at" in synced.columns:
            dates = synced["traded_at"].apply(_to_et_date).dropna()
            if not dates.empty:
                candidates.append(min(dates))

    if income_events:
        dates = [_parse_date(e.get("event_date")) for e in income_events]
        dates = [d for d in dates if d is not None]
        if dates:
            candidates.append(min(dates))

    if not candidates:
        return None
    ledger_floor = min(candidates)

    if (daily_snapshots_df is None or daily_snapshots_df.empty
            or "snapshot_date" not in daily_snapshots_df.columns):
        return None
    snap_dates = daily_snapshots_df["snapshot_date"].apply(_parse_date).dropna()
    if snap_dates.empty:
        return None
    snap_floor = snap_dates.min()

    return max(ledger_floor, snap_floor)


def reconstruct_daily_cash(anchor: dict, golive: "_date", trades_df, flows_df: list[dict],
                            income_events: list[dict]) -> list[dict]:
    """Roll the cash balance BACKWARD, day by day, from `anchor["date"]` to
    `golive`: `cash(D-1) = cash(D) - sum(cash deltas dated D)`.

    Cash-delta conventions (verified in code):
      trade:  BUY -= shares*price, SELL += shares*price — via
              `daily_pnl.today_trade_cash_delta`, reused verbatim rather than
              re-derived, so this can never drift from the Today's-P&L delta
              the rest of the app already trusts. SPLIT rows contribute
              ZERO — that function already excludes any action that isn't
              exactly "BUY"/"SELL", and a SPLIT row's `shares` is the
              POST-SPLIT TOTAL, not a delta, so summing it as one would
              fabricate a phantom cash move.
      flow:   deposit +amount, withdrawal -amount. 'baseline' is a
              bookkeeping anchor, not a cash movement (matches
              `account.money_weighted_return`'s own exclusion) and is
              skipped entirely, along with any other unrecognized flow_type.
      income: +amount, RAW SIGNED, uniformly across dividend/interest/fee —
              whatever sign SnapTrade used IS the correct cash delta. This
              is sign-agnostic BY DESIGN: a backward rollback only needs
              "what changed cash that day", never "which direction is a
              charge" — so the still-unconfirmed interest sign convention
              (see module docstring) does not affect this function at all,
              only the separately-disclosed interest TOTAL
              (`resolve_interest_charged`) does.

    Returns one row per CALENDAR date from `golive` to `anchor["date"]`
    inclusive, oldest-first: [{"date": date, "cash_balance": float}, ...].
    Returns [] if `golive` is after the anchor date (nothing to reconstruct).
    """
    anchor_date = anchor["date"]
    anchor_cash = float(anchor["cash"])

    if golive > anchor_date:
        return []

    trade_deltas: dict[_date, float] = {}
    if trades_df is not None and not trades_df.empty and "traded_at" in trades_df.columns:
        df = trades_df.copy()
        df["_d"] = df["traded_at"].apply(_to_et_date)
        df = df[df["_d"].notna()]
        for d, grp in df.groupby("_d"):
            rows = [{"action": r.get("action"), "shares": r.get("shares"), "price": r.get("price")}
                    for _, r in grp.iterrows()]
            trade_deltas[d] = trade_deltas.get(d, 0.0) + _trade_cash_delta(rows)

    flow_deltas: dict[_date, float] = {}
    for f in flows_df or []:
        ftype = str(f.get("flow_type") or "").strip().lower()
        if ftype not in ("deposit", "withdrawal"):
            continue  # 'baseline' (or anything unrecognized) is never a cash movement
        d = _parse_date(f.get("flow_date"))
        if d is None:
            continue
        try:
            amt = float(f.get("amount") or 0.0)
        except (TypeError, ValueError):
            continue
        delta = amt if ftype == "deposit" else -amt
        flow_deltas[d] = flow_deltas.get(d, 0.0) + delta

    income_deltas: dict[_date, float] = {}
    for e in income_events or []:
        d = _parse_date(e.get("event_date"))
        if d is None:
            continue
        try:
            amt = float(e.get("amount") or 0.0)
        except (TypeError, ValueError):
            continue
        income_deltas[d] = income_deltas.get(d, 0.0) + amt

    out = [{"date": anchor_date, "cash_balance": anchor_cash}]
    cash = anchor_cash
    d = anchor_date
    while d > golive:
        day_delta = trade_deltas.get(d, 0.0) + flow_deltas.get(d, 0.0) + income_deltas.get(d, 0.0)
        cash = cash - day_delta
        d = d - _timedelta(days=1)
        out.append({"date": d, "cash_balance": cash})

    out.reverse()
    return out


# ── 3. Book price-return + gross-book series ────────────────────────────────

def gross_book_by_date(daily_snapshots_df) -> dict:
    """{date: sum(shares*close_price)} from `daily_snapshots`-shaped rows.

    A date absent from `daily_snapshots_df` is simply absent from the
    result — never zero-filled (a day the ledger has no snapshot for is a
    genuine gap, not "no holdings").
    """
    out: dict[_date, float] = {}
    if daily_snapshots_df is None or daily_snapshots_df.empty:
        return out
    df = daily_snapshots_df.copy()
    df["_d"] = df["snapshot_date"].apply(_parse_date)
    df = df[df["_d"].notna()]
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")
    df = df.dropna(subset=["shares", "close_price"])
    df["_val"] = df["shares"] * df["close_price"]
    for d, v in df.groupby("_d")["_val"].sum().items():
        out[d] = float(v)
    return out


def book_daily_returns(daily_snapshots_df) -> dict:
    """Fixed-weight (prior-holdings) return between each pair of CONSECUTIVE
    dates actually present in `daily_snapshots_df`:

        r(D) = Σ[shares(t, D_prev)*price(t, D)] / Σ[shares(t, D_prev)*price(t, D_prev)] - 1

    over tickers `t` held on BOTH `D_prev` and `D` — a name added/removed
    between the two snapshot dates would otherwise inject a TRADING
    decision's cash flow into what is meant to be a pure PRICE-return
    series, which is the entire point of isolating this from
    `margin_contribution`'s debit-driven P&L.

    `D_prev` is the nearest EARLIER date with data in `daily_snapshots_df`,
    not necessarily the literal calendar day before — `daily_snapshots` has
    real gaps (days with no snapshot row at all), and per the module-wide
    "gap, never zero-fill" convention, this treats a gap as "no evidence for
    the skipped days", folding the whole elapsed return into the single
    later date `D` rather than inventing a flat return for the missing
    days in between.

    A date with no computable ticker overlap (e.g. the very first date in
    the frame, which has no `D_prev`, or a total holdings turnover) is
    simply ABSENT from the result — never zero.
    """
    out: dict[_date, float] = {}
    if daily_snapshots_df is None or daily_snapshots_df.empty:
        return out
    df = daily_snapshots_df.copy()
    df["_d"] = df["snapshot_date"].apply(_parse_date)
    df = df[df["_d"].notna()]
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")
    df = df.dropna(subset=["shares", "close_price"])

    by_date: dict[_date, dict[str, tuple[float, float]]] = {}
    for _, row in df.iterrows():
        by_date.setdefault(row["_d"], {})[row["ticker"]] = (float(row["shares"]), float(row["close_price"]))

    dates_sorted = sorted(by_date.keys())
    for i in range(1, len(dates_sorted)):
        d_prev, d = dates_sorted[i - 1], dates_sorted[i]
        prev_map = by_date[d_prev]
        cur_map = by_date[d]
        common = set(prev_map) & set(cur_map)
        if not common:
            continue
        numer = 0.0
        denom = 0.0
        for t in common:
            prev_shares, prev_price = prev_map[t]
            _, cur_price = cur_map[t]
            if prev_shares == 0:
                continue
            numer += prev_shares * cur_price
            denom += prev_shares * prev_price
        if denom > 0:
            out[d] = numer / denom - 1.0
    return out


# ── 4. Assemble the account series ──────────────────────────────────────────

def build_account_series(daily_cash: list[dict], gross_by_date: dict, recorded_df, rate: float) -> list[dict]:
    """Assemble the per-date account series: reconstructed/live cash (from
    `reconstruct_daily_cash`) + gross book value (from `gross_book_by_date`),
    tagged with whether that date is F-266 RECORDED (real, 2026-09-10+) or
    RECONSTRUCTED (derived here).

    Reuses `margin.call_distance` VERBATIM for cushion/call_distance_pct —
    never re-derives that formula.

    `recorded_df` is `db.load_account_daily_snapshots()`'s shape (or
    None/empty) — any date present there is `source="recorded"`; everything
    else is `source="reconstructed"`.

    Gross-book-dependent fields (`gross_book`, `net_equity`, `leverage`,
    `margin_debit`, `cushion`, `call_distance_pct`) are None on a
    `daily_snapshots` gap for that date. `cash_balance` is always populated
    (it doesn't depend on gross book) — nothing downstream should treat a
    missing gross book as zero.
    """
    recorded_dates = set()
    if recorded_df is not None and not recorded_df.empty and "snapshot_date" in recorded_df.columns:
        for d in recorded_df["snapshot_date"]:
            pd_d = _parse_date(d)
            if pd_d is not None:
                recorded_dates.add(pd_d)

    out = []
    for row in daily_cash:
        d = row["date"]
        cash_balance = row["cash_balance"]
        gross = gross_by_date.get(d)
        net_equity = None
        leverage = None
        # margin_debit depends only on cash_balance (always populated), never
        # on gross book — computed unconditionally so a daily_snapshots gap
        # doesn't hide a real, known debit from "current" readouts downstream.
        margin_debit = max(0.0, -cash_balance)
        cushion = None
        call_distance_pct = None
        if gross is not None:
            net_equity = gross + cash_balance
            leverage = (gross / net_equity) if net_equity > 0 else None
            cd = _margin.call_distance(gross, net_equity, margin_debit, rate)
            if cd is not None:
                cushion = cd["cushion"]
                call_distance_pct = cd["call_distance_pct"]
        out.append({
            "date": d,
            "gross_book": gross,
            "cash_balance": cash_balance,
            "net_equity": net_equity,
            "leverage": leverage,
            "margin_debit": margin_debit,
            "cushion": cushion,
            "call_distance_pct": call_distance_pct,
            "source": "recorded" if d in recorded_dates else "reconstructed",
        })
    return out


# ── 5. Self-validation ───────────────────────────────────────────────────────

def validate_reconstruction(series: list[dict], recorded_df) -> dict:
    """Compare the reconstructed `cash_balance` against F-266's own RECORDED
    `cash_balance` on every overlap date — the reconstruction's only
    independent check. A mismatch means the backward roll drifted (a missed
    trade/flow/income row, a mis-signed delta, a golive-floor picked wrong)
    and must be surfaced, never hidden — the render layer withholds every
    money verdict spanning the reconstructed window when this fails (see
    app.py's fail-safe wiring).

    Match iff `abs(drift) <= max(_RECON_ABS_TOL, _RECON_REL_TOL * abs(recorded))`.
    Overlap dates where the recorded row's `cash_balance` is None (F-266
    itself couldn't fill it that day, e.g. stale cash) are skipped — not
    evaluated, not counted as either a pass or a mismatch.

    Returns {"ok": bool, "overlap_days": int, "mismatches": [...], "max_drift": float}.
    `ok=True` with `overlap_days=0` is the correct (if uninformative) answer
    when the two windows never overlap — there's nothing to contradict.
    """
    recorded_cash: dict[_date, float] = {}
    if recorded_df is not None and not recorded_df.empty and "snapshot_date" in recorded_df.columns:
        df = recorded_df.copy()
        df["_d"] = df["snapshot_date"].apply(_parse_date)
        df["_cb"] = pd.to_numeric(df.get("cash_balance"), errors="coerce")
        for _, row in df.iterrows():
            if row["_d"] is not None and pd.notna(row["_cb"]):
                recorded_cash[row["_d"]] = float(row["_cb"])

    mismatches = []
    max_drift = 0.0
    overlap_days = 0
    for point in series:
        d = point["date"]
        if d not in recorded_cash:
            continue
        overlap_days += 1
        recon = point["cash_balance"]
        rec = recorded_cash[d]
        drift = recon - rec
        tol = max(_RECON_ABS_TOL, _RECON_REL_TOL * abs(rec))
        if abs(drift) > tol:
            mismatches.append({"date": d, "reconstructed": recon, "recorded": rec, "drift": drift})
        max_drift = max(max_drift, abs(drift))

    return {
        "ok": len(mismatches) == 0,
        "overlap_days": overlap_days,
        "mismatches": mismatches,
        "max_drift": max_drift,
    }


def render_gate(validation: dict) -> dict:
    """Pure render-layer DECISION (extracted per CLAUDE.md's "extract the
    decision, not just the helper" convention — `app.py` isn't test-covered,
    so this is where the fail-safe actually lives and is verified).

    Given `validate_reconstruction`'s output, decide what the 💳 Capital vs
    Margin section may show. `ok=False` means the backward roll disagrees
    with F-266's own recorded truth by more than the tolerance — every
    verdict that SPANS the reconstructed window (equity curves, margin
    contribution/net value, break-even rate, regime split, drawdown
    decomposition) must be WITHHELD outright, not merely captioned, because
    any number built on a mismatched reconstruction could be wrong by an
    unknown amount. Only the raw interest partition (which never touches the
    reconstruction) and any purely-recorded-window figures survive.

    Returns {"show_spanning_verdicts": bool, "worst_mismatch": dict|None} —
    `worst_mismatch` is the single largest-|drift| entry from
    `validation["mismatches"]`, for the banner text (its date + dollar
    amount), or None when there's nothing to report.
    """
    ok = bool(validation.get("ok", True))
    mismatches = validation.get("mismatches")
    mismatches = mismatches if mismatches is not None else []
    worst = max(mismatches, key=lambda m: abs(m.get("drift", 0.0))) if mismatches else None
    return {"show_spanning_verdicts": ok, "worst_mismatch": worst}


# ── 6. The v1 outputs ────────────────────────────────────────────────────────

def equity_curves(series: list[dict], book_returns: dict) -> dict:
    """Two equity curves over `series`'s date range:
      - `levered`:   net_equity, straight from `series` (the actual account).
      - `unlevered`: synthetic capital-only curve, anchored at the SAME
        dollar point as `levered` on `series[0]`'s date (owner decision —
        see module docstring), compounded forward at the book's own realized
        (unlevered) daily return `book_returns[d]`.

    A date with no `book_returns` entry (a snapshot gap) leaves the
    unlevered curve FLAT for that single day rather than fabricating a
    return for a day with no price evidence — later real returns still
    compound on top of it, so the running total isn't lost, just not
    updated on days without evidence.

    Returns {"dates": [...], "levered": [...], "unlevered": [...],
    "source": [...]} — all empty when `series` is empty or `series[0]`'s
    `net_equity` is None (nothing to anchor the unlevered curve to).
    """
    dates: list = []
    levered: list = []
    unlevered: list = []
    source: list = []
    if not series or series[0].get("net_equity") is None:
        return {"dates": dates, "levered": levered, "unlevered": unlevered, "source": source}

    anchor_date = series[0]["date"]
    running = series[0]["net_equity"]
    for point in series:
        d = point["date"]
        dates.append(d)
        levered.append(point.get("net_equity"))
        if d != anchor_date:
            r = book_returns.get(d)
            if r is not None:
                running = running * (1.0 + r)
        unlevered.append(running)
        source.append(point.get("source"))
    return {"dates": dates, "levered": levered, "unlevered": unlevered, "source": source}


def margin_contribution(series: list[dict], book_returns: dict, interest_charged: float) -> dict:
    """Decompose margin's net contribution over `series`'s window:

      extra_exposure_pnl = Σ[debit(D_prev) * r_book(D)] for each date D with
        both a preceding point carrying `margin_debit` and a `book_returns`
        entry — the PRIOR day's debit is the amount of extra exposure margin
        bought, and it earns/loses the book's own realized return over the
        following interval (same "prior-holdings weight" convention
        `book_daily_returns` itself uses, keeping the two internally
        consistent).
      net_value  = extra_exposure_pnl - interest_charged.
      curve_gap  = last(levered) - last(unlevered) from `equity_curves` on
        this SAME series/book_returns — an INDEPENDENT cross-check computed
        a completely different way (compounded curves vs. a day-by-day
        debit x return sum). It should land close to `net_value`; a large
        divergence between the two flags a methodology bug, not real money.

    All-unlevered window (never any margin debit) -> `extra_exposure_pnl`
    stays 0.0 and `net_value = -interest_charged` — never a crash.
    """
    extra_exposure_pnl = 0.0
    for i in range(1, len(series)):
        prev = series[i - 1]
        cur = series[i]
        debit_prev = prev.get("margin_debit")
        r = book_returns.get(cur["date"])
        if debit_prev is None or r is None:
            continue
        extra_exposure_pnl += debit_prev * r

    net_value = extra_exposure_pnl - float(interest_charged)

    curves = equity_curves(series, book_returns)
    curve_gap = None
    if curves["levered"] and curves["unlevered"]:
        last_lev = curves["levered"][-1]
        last_unlev = curves["unlevered"][-1]
        if last_lev is not None and last_unlev is not None:
            curve_gap = last_lev - last_unlev

    return {
        "extra_exposure_pnl": extra_exposure_pnl,
        "interest_paid": float(interest_charged),
        "net_value": net_value,
        "curve_gap": curve_gap,
    }


def break_even_rate(interest_charged: float, avg_debit: float, days: int) -> "float | None":
    """Annualized book-return rate at which margin nets to exactly zero —
    "your book needs to return >= X%/yr just to cover the leverage."

    None when `avg_debit<=0` or `days<=0` (nothing to annualize against —
    an unlevered window, or a degenerate zero-length window).
    """
    if avg_debit is None or avg_debit <= 0 or not days or days <= 0:
        return None
    return interest_charged / avg_debit * 365.0 / days


def worst_drawdown_window(series: list[dict]) -> "tuple | None":
    """Simple peak-to-trough drawdown on LEVERED `net_equity` across
    `series`. Returns `(peak_date, trough_date, peak_value, trough_value)`
    for the single largest peak-to-trough decline, or None when fewer than
    2 points carry a non-None `net_equity`."""
    points = [(p["date"], p["net_equity"]) for p in series if p.get("net_equity") is not None]
    if len(points) < 2:
        return None

    peak_date, peak_value = points[0]
    best = None  # (drawdown_amount, peak_date, trough_date, peak_value, trough_value)
    for d, v in points[1:]:
        if v > peak_value:
            peak_date, peak_value = d, v
            continue
        drawdown = peak_value - v
        if best is None or drawdown > best[0]:
            best = (drawdown, peak_date, d, peak_value, v)

    if best is None:
        return None
    _, p_date, t_date, p_val, t_val = best
    return (p_date, t_date, p_val, t_val)


def drawdown_decomposition(series: list[dict], book_returns: dict, start: "_date", end: "_date",
                            income_events: "list[dict] | None" = None) -> dict:
    """Decompose the ACTUAL change in levered `net_equity` between `start`
    and `end` (both must be present in `series` with a non-None
    `net_equity`) into what margin amplified vs. what would have happened
    unlevered anyway:

      actual_change          = net_equity(end) - net_equity(start)
      unlevered_change       = net_equity(start) * Π(1+r_book(d)) for every
                                date d in (start, end] carrying a
                                `book_returns` entry, minus net_equity(start)
                                — the SAME compounding `equity_curves` uses,
                                re-anchored at this episode's own start
                                rather than the whole-series golive anchor.
      amplification_portion  = actual_change - unlevered_change
      interest_in_episode    = interest charged strictly within (start, end],
                                via `interest_partition`/`resolve_interest_charged`
                                on the events falling in that window — reuses
                                the SAME still-unconfirmed sign convention as
                                the top-level total (never a parallel guess).
                                0.0 when `income_events` is omitted.

    Returns all-None (except `interest_in_episode=0.0`) when `start`/`end`
    aren't both present with a real `net_equity` — never guesses a partial
    episode.
    """
    by_date = {p["date"]: p for p in series}
    p_start = by_date.get(start)
    p_end = by_date.get(end)
    if (p_start is None or p_end is None
            or p_start.get("net_equity") is None or p_end.get("net_equity") is None):
        return {
            "actual_change": None, "unlevered_change": None,
            "amplification_portion": None, "interest_in_episode": 0.0,
        }

    eq_start = p_start["net_equity"]
    eq_end = p_end["net_equity"]
    actual_change = eq_end - eq_start

    dates_sorted = sorted(d for d in by_date if start < d <= end)
    running = eq_start
    for d in dates_sorted:
        r = book_returns.get(d)
        if r is not None:
            running *= (1.0 + r)
    unlevered_change = running - eq_start
    amplification_portion = actual_change - unlevered_change

    windowed = []
    for e in (income_events or []):
        d = _parse_date(e.get("event_date"))
        if d is not None and start < d <= end:
            windowed.append(e)
    interest_in_episode = resolve_interest_charged(interest_partition(windowed), None)["charged"]

    return {
        "actual_change": actual_change,
        "unlevered_change": unlevered_change,
        "amplification_portion": amplification_portion,
        "interest_in_episode": interest_in_episode,
    }


def projected_annual_interest(current_debit: float, effective_annual_rate: float) -> float:
    """Projected annual interest at the CURRENT debit/rate, extrapolated
    forward. A straight-line "if nothing changes" projection — not a
    forecast of future debit or rate changes."""
    return float(current_debit) * float(effective_annual_rate)


def deleverage_scenario(gross_book: float, debit: float, paydown: float, rate: float,
                         eff_rate: float, shock_pct: float) -> dict:
    """"If you paid down $paydown of debit": interest saved, and the margin
    call distance + shock-loss outcome before/after — composes
    `margin.call_distance`/`margin.shock_call_outcome` VERBATIM, no new
    formula.

    Selling `paydown` worth of stock and using the proceeds to repay the
    matching debit dollar-for-dollar leaves EQUITY invariant (gross_book and
    debit both drop by the same amount) — the same invariant
    `margin.shock_call_outcome`'s own full-repay branch relies on.

    `paydown` is clamped to `[0, debit]` — you cannot pay down more than you
    owe; `paydown >= debit` fully repays (`new_debit=0`, `call_after=None`
    per `call_distance`'s own no-debit guard).
    """
    paydown = max(0.0, min(float(paydown), float(debit)))
    new_debit = max(0.0, debit - paydown)
    new_gross = gross_book - paydown

    interest_now = debit * eff_rate
    interest_after = new_debit * eff_rate
    interest_saved = interest_now - interest_after

    call_now = (
        _margin.call_distance(gross_book, gross_book - debit, debit, rate)
        if debit > 0 else None
    )
    call_after = (
        _margin.call_distance(new_gross, new_gross - new_debit, new_debit, rate)
        if new_debit > 0 else None
    )

    shock_now = (
        _margin.shock_call_outcome(
            stock_value_now=gross_book,
            shocked_stock_value=gross_book * (1.0 + shock_pct / 100.0),
            margin_debit=debit,
            rate=rate,
        ) if debit > 0 else None
    )
    shock_after = (
        _margin.shock_call_outcome(
            stock_value_now=new_gross,
            shocked_stock_value=new_gross * (1.0 + shock_pct / 100.0),
            margin_debit=new_debit,
            rate=rate,
        ) if new_debit > 0 else None
    )

    return {
        "new_debit": new_debit,
        "new_gross": new_gross,
        "interest_now": interest_now,
        "interest_after": interest_after,
        "interest_saved": interest_saved,
        "call_now": call_now,
        "call_after": call_after,
        "shock_now": shock_now,
        "shock_after": shock_after,
    }


def regime_split(series: list[dict], weekly_book_returns: dict, weekly_interest: dict) -> dict:
    """Margin's net contribution broken out by up-weeks vs. down-weeks (ISO
    week), so a blended "roughly even" total can't hide "great in rallies,
    brutal in selloffs."

    `weekly_book_returns` : {(iso_year, iso_week): compounded book return for
                              that week}
    `weekly_interest`     : {(iso_year, iso_week): interest charged that week}

    A week's SIGN determines its bucket: return >= 0 -> "up", < 0 -> "down".
    Extra-exposure P&L per week uses that week's AVERAGE `margin_debit`
    across `series`'s points falling in that ISO week — weekly resampling
    can't preserve the exact prior-day debit pairing `margin_contribution`
    uses daily, so the average debit within the week is the closest
    defensible substitute.

    Returns {"up": {"weeks", "extra_exposure_pnl", "interest", "net"},
    "down": {...}} — a week absent from `weekly_book_returns` is simply not
    counted in either bucket.
    """
    debit_by_week: dict[tuple, list] = {}
    for p in series:
        debit = p.get("margin_debit")
        if debit is None:
            continue
        iso = p["date"].isocalendar()
        key = (iso[0], iso[1])
        debit_by_week.setdefault(key, []).append(debit)

    up = {"weeks": 0, "extra_exposure_pnl": 0.0, "interest": 0.0, "net": 0.0}
    down = {"weeks": 0, "extra_exposure_pnl": 0.0, "interest": 0.0, "net": 0.0}

    for week, r in weekly_book_returns.items():
        week_debits = debit_by_week.get(week)
        avg_debit = (sum(week_debits) / len(week_debits)) if week_debits else 0.0
        extra = avg_debit * r
        interest = weekly_interest.get(week, 0.0)
        net = extra - interest
        bucket = up if r >= 0 else down
        bucket["weeks"] += 1
        bucket["extra_exposure_pnl"] += extra
        bucket["interest"] += interest
        bucket["net"] += net

    return {"up": up, "down": down}


# ── Composition helpers — wiring regime_split from raw daily data ──────────
# Not part of the v1 spec's named-function list, but `regime_split` takes
# already-weekly-bucketed dicts as inputs and the app has no other source
# for them; these are the ONE place that weekly aggregation happens, kept
# here (pure, tested) rather than duplicated as ad hoc pandas in app.py.

def weekly_compounded_returns(daily_returns: dict) -> dict:
    """Compound `book_daily_returns`' per-date returns into one return per
    ISO week: Π(1+r) - 1 over every date in that week carrying a return.

    Returns {(iso_year, iso_week): compounded_return}. A week with zero
    return-bearing dates simply never appears (never a fabricated 0.0%)."""
    out: dict[tuple, float] = {}
    for d in sorted(daily_returns.keys()):
        r = daily_returns[d]
        if r is None:
            continue
        iso = d.isocalendar()
        key = (iso[0], iso[1])
        out[key] = (1.0 + out.get(key, 0.0)) * (1.0 + r) - 1.0 if key in out else r
    return out


def weekly_interest_charged(income_events: list[dict], charged_sign: "str | None" = None) -> dict:
    """Sum interest CHARGED per ISO week, from raw `snaptrade_income_events`
    rows — reuses `interest_partition`/`resolve_interest_charged` per week so
    this can never drift from the top-level total's own (still-unconfirmed,
    see module docstring) sign convention. `charged_sign` defaults to None
    (unconfirmed), matching every other call site in this module.

    Returns {(iso_year, iso_week): charged_magnitude}. A week with no
    interest events never appears."""
    by_week: dict[tuple, list] = {}
    for e in income_events or []:
        if str(e.get("event_type") or "").strip().lower() != "interest":
            continue
        d = _parse_date(e.get("event_date"))
        if d is None:
            continue
        iso = d.isocalendar()
        by_week.setdefault((iso[0], iso[1]), []).append(e)

    out: dict[tuple, float] = {}
    for week, events in by_week.items():
        out[week] = resolve_interest_charged(interest_partition(events), charged_sign)["charged"]
    return out
