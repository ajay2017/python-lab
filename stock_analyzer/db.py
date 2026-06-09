"""
Persistence layer — reads/writes holdings, watchlist, and trades to Supabase.

SECURITY MODEL (single-user, server-side Streamlit Cloud):
    - Streamlit secret `[supabase] key` MUST be the secret/service-role key
      (sb_secret_* in the new key format, or the legacy service_role JWT),
      NOT the publishable/anon key. The secret key bypasses RLS and stays
      server-side only (Streamlit secrets are never sent to the browser).
    - RLS is ENABLED on all three tables in the public schema with a single
      policy per table that grants ALL operations to the service_role.
      Anon/authenticated roles have no matching policy, so a leaked
      publishable key cannot access data — defense in depth.

One-time SQL to set up RLS (run in Supabase SQL Editor):

    ALTER TABLE public.holdings        ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.watchlist       ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.trades          ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.recommendations ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.manual_stops    ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.fundamentals_cache ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS "Allow all (service role)" ON public.holdings;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.watchlist;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.trades;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.recommendations;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.manual_stops;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.fundamentals_cache;

    CREATE POLICY "Allow all (service role)" ON public.holdings
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.watchlist
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.trades
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.recommendations
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.manual_stops
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.fundamentals_cache
        FOR ALL TO service_role USING (true) WITH CHECK (true);

Table schema (run once if tables don't exist):

    create table if not exists holdings (
        id         bigint primary key generated always as identity,
        ticker     text    not null unique,
        shares     numeric not null check (shares > 0),
        avg_cost   numeric not null check (avg_cost > 0),
        updated_at timestamptz default now()
    );

If the holdings table predates the unique constraint, run this once to
add it (required for save_holdings' upsert path — without the constraint
the atomic upsert + sweep pattern degrades to delete-then-insert):

    ALTER TABLE public.holdings
        ADD CONSTRAINT holdings_ticker_unique UNIQUE (ticker);

    create table if not exists watchlist (
        id       bigint primary key generated always as identity,
        ticker   text not null unique,
        added_at timestamptz default now()
    );

    create table if not exists trades (
        id               bigint primary key generated always as identity,
        ticker           text    not null,
        action           text    not null,
        shares           numeric not null check (shares > 0),
        price            numeric not null check (price > 0),
        cost_basis       numeric,
        realized_pnl     numeric,
        notes            text,
        trigger_type     text default 'MANUAL',
        signal_seen      text,
        followed_signal  text,
        deviation_reason text,
        lesson           text,
        traded_at        timestamptz default now()
    );

If trades table already exists, run this once to add the decision-journal columns:

    ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_seen      text;
    ALTER TABLE trades ADD COLUMN IF NOT EXISTS followed_signal  text;
    ALTER TABLE trades ADD COLUMN IF NOT EXISTS deviation_reason text;
    ALTER TABLE trades ADD COLUMN IF NOT EXISTS lesson           text;

Recommendations log (added 2026-05-26 — first-seen capture of every pick
surfaced by Today's Brief so you can audit the App's recommendation
history over time):

    create table if not exists recommendations (
        id               bigint primary key generated always as identity,
        ticker           text not null,
        rec_date         date not null,
        rec_type         text not null,          -- 'new_pick' | 'add_winner' | 'buy_candidate'
        surfaced_at      timestamptz default now(),
        price_at_surface numeric,                -- price snapshot at first-surface (for would-have-gained math)
        composite_score  numeric,
        momentum_score   numeric,
        sector           text,
        conviction       text,                   -- 'high' | 'moderate' | 'unverified' | NULL
        verdict          text,                   -- 'confirmed' | 'mixed' | 'caution' | 'unverified' | NULL
        thesis           text,
        constraint recommendations_unique_per_day
            unique (ticker, rec_date, rec_type)
    );

    create index if not exists recommendations_rec_date_idx
        on public.recommendations (rec_date desc);

If recommendations table already exists (created before 2026-05-26), run
this once to add the price_at_surface column for the History page:

    alter table public.recommendations
        add column if not exists price_at_surface numeric;

Manual stops (added 2026-05-29 — user-set stop overrides recorded when
the Brief's "raise stop" recommendation is actioned. Without this the
recommendation re-fires every render because the system has no record
the user acted on it):

    create table if not exists manual_stops (
        id            bigint primary key generated always as identity,
        ticker        text not null unique,
        stop_price    numeric not null check (stop_price > 0),
        set_at        timestamptz default now(),
        note          text,
        source_action text                            -- e.g. 'review_tighten_only' / 'review_trim_and_tighten' / 'manual'
    );

    -- Last-known-good fundamentals fallback (load_all serves this when the live
    -- .info leg is sparse and FMP can't backfill, bounded by
    -- FUNDAMENTALS_CACHE_MAX_AGE_DAYS). Optional: until created, the app runs
    -- live-only exactly as before (load/save degrade to no-ops).
    create table if not exists fundamentals_cache (
        ticker      text primary key,
        financials  jsonb not null,
        fetched_at  timestamptz not null default now()
    );

    -- Daily snapshots — Tier B day-over-day P&L baseline (added 2026-06-09).
    -- Prior-close snapshot of held positions; the positions day-P&L =
    --   current marked value − this baseline value + today's trade cash.
    -- Optional: until created, load_recent_snapshots returns empty and
    -- save_daily_snapshot no-ops, so the app runs exactly as before (the
    -- held-only "Today's P&L (held)" mark). Written once per trading day when
    -- the market is CLOSED, so close_price is the final settled close.
    create table if not exists public.daily_snapshots (
        snapshot_date date    not null,
        ticker        text    not null,
        shares        numeric not null check (shares > 0),
        close_price   numeric not null check (close_price > 0),
        created_at    timestamptz default now(),
        primary key (snapshot_date, ticker)
    );
    alter table public.daily_snapshots enable row level security;
    drop policy if exists "Allow all (service role)" on public.daily_snapshots;
    create policy "Allow all (service role)" on public.daily_snapshots
        for all to service_role using (true) with check (true);
"""

import streamlit as st
import pandas as pd

_DEFAULT_WATCHLIST = ["NVDA", "AMD", "INTC", "MU"]

# Read-only viewer mode. When enabled, every USER/OWNER-data write function
# below becomes a safe no-op — the security backstop behind the UI's disabled
# controls (a missed UI gate still cannot mutate data). Set once at startup by
# app.py after resolving the viewer's identity. NOTE: save_fundamentals_cache is
# intentionally NOT gated — it's a system cache (not user data), and cache
# warming on a viewer's visit is harmless/desirable.
_READONLY = False

def set_readonly(flag: bool) -> None:
    global _READONLY
    _READONLY = bool(flag)

def is_readonly() -> bool:
    return _READONLY


def has_db() -> bool:
    """True when Supabase credentials are present in st.secrets."""
    try:
        url = st.secrets.get("supabase", {}).get("url", "")
        return bool(url) and not url.startswith("https://your-project")
    except Exception:
        return False


@st.cache_resource
def _client():
    from supabase import create_client
    return create_client(
        st.secrets["supabase"]["url"],
        st.secrets["supabase"]["key"],
    )


# ── Holdings ──────────────────────────────────────────────────────────────────

def load_holdings() -> pd.DataFrame:
    from stock_analyzer import api_health as _ah
    if has_db():
        try:
            rows = _client().table("holdings").select("*").order("ticker").execute().data
            _ah.record("supabase", "success")
            if rows:
                df = pd.DataFrame(rows)[["ticker", "shares", "avg_cost"]]
                df.columns = ["Ticker", "Shares", "Avg Cost ($)"]
                df["Shares"] = df["Shares"].astype(float)
                df["Avg Cost ($)"] = df["Avg Cost ($)"].astype(float)
                return df
            # Table is empty — return empty frame so user starts fresh
            return pd.DataFrame(columns=["Ticker", "Shares", "Avg Cost ($)"])
        except Exception as e:
            err = str(e)
            _ah.record("supabase", "error", msg=err[:120])
            if "row-level security" in err.lower() or "rls" in err.lower() or "42501" in err:
                st.error(
                    "⛔ Supabase RLS is blocking reads. The Streamlit secret "
                    "`[supabase] key` must be the **service-role / secret key** "
                    "(starts with `sb_secret_` or is the legacy service_role JWT) — "
                    "not the publishable/anon key. Update it in Streamlit Cloud → "
                    "Settings → Secrets, then Reboot the app."
                )
            else:
                st.error(f"⛔ DB read error: {err}")
    # No DB configured — return empty frame
    return pd.DataFrame(columns=["Ticker", "Shares", "Avg Cost ($)"])


def save_holdings(df: pd.DataFrame) -> bool:
    """Persist the holdings DataFrame to Supabase. Returns True on success.

    Atomic-ish replace: upsert every current row on the ticker unique key,
    then sweep tickers no longer in the DataFrame. Order matters — upsert
    first so a transient failure leaves the prior data intact, never wipes.
    Requires UNIQUE(ticker) on holdings (see one-time SQL at module top).
    """
    if _READONLY: return False  # read-only viewer: no-op
    from stock_analyzer import api_health as _ah
    if not has_db():
        return False

    # Build + validate records up front. Failure here never touches the DB.
    records = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        ticker   = str(row.get("Ticker", "")).strip().upper()
        try:
            shares   = float(row.get("Shares", 0) or 0)
            avg_cost = float(row.get("Avg Cost ($)", 0) or 0)
        except (TypeError, ValueError):
            continue
        if ticker and shares > 0 and avg_cost > 0 and ticker not in seen:
            records.append({"ticker": ticker, "shares": shares, "avg_cost": avg_cost})
            seen.add(ticker)

    try:
        client = _client()
        if records:
            client.table("holdings").upsert(records, on_conflict="ticker").execute()
        # Sweep tickers no longer present. Idempotent — a partial failure here
        # leaves stale rows (recoverable on next save) but never destroys the
        # current truth.
        kept = list(seen)
        sweep = client.table("holdings").delete()
        if kept:
            sweep = sweep.not_.in_("ticker", kept)
        else:
            sweep = sweep.neq("ticker", "")
        sweep.execute()
        # Symmetric sweep on manual_stops — a stop override for a ticker no
        # longer held is an orphan (the override only applies to active
        # positions). Wrapped in its own try so a missing manual_stops table
        # (user hasn't run the one-time DDL yet) doesn't fail the holdings
        # save that already succeeded.
        try:
            ms_sweep = client.table("manual_stops").delete()
            if kept:
                ms_sweep = ms_sweep.not_.in_("ticker", kept)
            else:
                ms_sweep = ms_sweep.neq("ticker", "")
            ms_sweep.execute()
        except Exception:
            pass
        _ah.record("supabase", "success")
        return True
    except Exception as e:
        err = str(e)
        _ah.record("supabase", "error", msg=err[:120])
        if "row-level security" in err.lower() or "42501" in err:
            st.error(
                "⛔ Supabase RLS is blocking writes. The Streamlit secret "
                "`[supabase] key` must be the service-role / secret key "
                "(bypasses RLS), not the publishable/anon key."
            )
        else:
            st.error(f"⛔ Failed to save holdings: {err}")
        return False


# ── Daily snapshots (Tier B day-over-day P&L baseline) ─────────────────────────
# Optional table. Until created, load returns empty and save no-ops, so the
# day-P&L degrades to the held-only mark exactly as before. See DDL at module top.

def load_recent_snapshots(days: int = 10) -> pd.DataFrame:
    """Most-recent daily snapshots (newest first). Empty on any error / missing
    table — the day-P&L then falls back to the held-only mark, never crashes."""
    empty = pd.DataFrame(columns=["snapshot_date", "ticker", "shares", "close_price"])
    if not has_db():
        return empty
    try:
        rows = (
            _client().table("daily_snapshots")
            .select("snapshot_date,ticker,shares,close_price")
            .order("snapshot_date", desc=True)
            .limit(max(1, days) * 200)   # days × generous max plausible positions
            .execute().data
        )
        if not rows:
            return empty
        df = pd.DataFrame(rows)
        df["shares"]      = df["shares"].astype(float)
        df["close_price"] = df["close_price"].astype(float)
        return df
    except Exception:
        # Optional enhancement table — degrade silently (no error banner) so a
        # not-yet-created table doesn't nag; core P&L still renders (held mark).
        return empty


def save_daily_snapshot(snapshot_date, rows: list[dict]) -> bool:
    """Upsert the snapshot for `snapshot_date` (today's held positions at the
    final, market-closed close price). Sweeps tickers no longer held for that
    date so a same-day exit doesn't linger. Read-only viewers no-op; a missing
    table degrades to a silent no-op (returns False)."""
    if _READONLY: return False  # read-only viewer: no-op
    if not has_db():
        return False
    _date = str(snapshot_date)
    records = []
    seen: set[str] = set()
    for r in rows:
        tk = str(r.get("ticker", "")).strip().upper()
        try:
            sh = float(r.get("shares") or 0)
            px = float(r.get("close_price") or 0)
        except (TypeError, ValueError):
            continue
        if tk and sh > 0 and px > 0 and tk not in seen:
            records.append({"snapshot_date": _date, "ticker": tk,
                            "shares": sh, "close_price": px})
            seen.add(tk)
    if not records:
        return False
    try:
        client = _client()
        client.table("daily_snapshots").upsert(
            records, on_conflict="snapshot_date,ticker"
        ).execute()
        # Sweep tickers for THIS date no longer held (idempotent; a partial
        # failure leaves a stale row, recoverable next write, never destructive).
        client.table("daily_snapshots").delete().eq(
            "snapshot_date", _date
        ).not_.in_("ticker", list(seen)).execute()
        return True
    except Exception:
        return False


# ── Watchlist ─────────────────────────────────────────────────────────────────

def load_watchlist() -> list[str]:
    if has_db():
        try:
            rows = _client().table("watchlist").select("ticker").execute().data
            return [r["ticker"] for r in rows] if rows else []
        except Exception as e:
            st.warning(f"Watchlist read error: {e}")
    return list(_DEFAULT_WATCHLIST)


# ── Trades ───────────────────────────────────────────────────────────────────

_TRADE_COLS = ["id", "ticker", "action", "shares", "price",
               "cost_basis", "realized_pnl", "notes", "trigger_type",
               "signal_seen", "followed_signal", "deviation_reason", "lesson",
               "traded_at"]


def load_trades() -> pd.DataFrame:
    empty = pd.DataFrame(columns=_TRADE_COLS)
    if has_db():
        try:
            rows = (
                _client().table("trades")
                .select("*")
                .order("traded_at", desc=True)
                .execute().data
            )
            if rows:
                df = pd.DataFrame(rows)
                # Backfill decision-journal columns for rows pre-dating the feature
                for col in ("signal_seen", "followed_signal", "deviation_reason", "lesson"):
                    if col not in df.columns:
                        df[col] = None
                return df
            return empty
        except Exception as e:
            err = str(e)
            if "row-level security" in err.lower() or "42501" in err:
                st.error(
                    "⛔ Supabase RLS is blocking the trades table. The Streamlit "
                    "secret `[supabase] key` must be the service-role / secret "
                    "key (bypasses RLS), not the publishable/anon key."
                )
            else:
                st.error(f"⛔ Trades read error: {err}")
    return empty


def save_trade(record: dict) -> bool:
    if _READONLY: return False  # read-only viewer: no-op
    if not has_db():
        return False
    try:
        _client().table("trades").insert(record).execute()
        return True
    except Exception as e:
        st.error(f"⛔ Failed to save trade: {e}")
        return False


def delete_trade(trade_id: int) -> bool:
    if _READONLY: return False  # read-only viewer: no-op
    if not has_db():
        return False
    try:
        _client().table("trades").delete().eq("id", int(trade_id)).execute()
        return True
    except Exception as e:
        st.error(f"⛔ Failed to delete trade: {e}")
        return False


def update_trade_realized_pnl(trade_id: int, realized_pnl: float,
                               cost_basis: float | None = None) -> bool:
    """
    Update an existing SELL trade's realized_pnl (and optionally cost_basis).
    Used by recalculate_from_trades() to correct stale figures stored on rows
    that were saved when holdings_df was in a corrupted state.
    """
    if _READONLY: return False  # read-only viewer: no-op
    if not has_db():
        return False
    try:
        update_record = {"realized_pnl": float(realized_pnl)}
        if cost_basis is not None:
            update_record["cost_basis"] = float(cost_basis)
        _client().table("trades").update(update_record).eq("id", int(trade_id)).execute()
        return True
    except Exception as e:
        st.error(f"⛔ Failed to update trade {trade_id}: {e}")
        return False


def recalculate_from_trades(trades_df: pd.DataFrame) -> dict:
    """
    Replay every trade chronologically to derive the truthful holdings table
    plus the correct realized_pnl for each SELL row. Use this whenever the
    trades table has been modified in a way that could put holdings out of
    sync — most commonly after deleting trades, since holdings updates are
    incremental and don't auto-revert on delete.

    The bug this catches: bad SELL rows reduce holdings_df.Shares at save
    time. Deleting those SELLs removes the trade row but leaves Shares in
    the corrupted state — and the next legitimate SELL then auto-fills its
    cost_basis from the wrong avg_cost, producing wrong realized_pnl (e.g.
    NFLX +$177 when it should have been negative).

    Returns:
      {
        "holdings_df":             pd.DataFrame with columns
                                    [Ticker, Shares, Avg Cost ($)],
        "realized_pnl_corrections": dict mapping trade_id → corrected
                                    realized_pnl for SELL rows whose stored
                                    value differs from the replay result,
        "warnings":                 list[str] — SELLs encountered with no
                                    prior BUY in the trades table (history
                                    gap, can't compute correct cost basis),
      }
    """
    holdings: dict[str, dict] = {}                # ticker → {shares, avg_cost}
    corrections: dict[int, dict] = {}             # trade_id → {realized_pnl, cost_basis}
    warnings: list[str] = []

    if trades_df is None or trades_df.empty:
        return {
            "holdings_df":              pd.DataFrame(columns=["Ticker", "Shares", "Avg Cost ($)"]),
            "realized_pnl_corrections": {},
            "warnings":                 [],
        }

    # Chronological replay — oldest first so cost basis builds up correctly.
    # format='ISO8601' + utc=True handles mixed-precision timestamp strings
    # in the same column: raw-SQL inserts (rebaseline rows, no microseconds)
    # vs Python-SDK inserts (default `now()`, microsecond precision). Without
    # format='ISO8601', pandas infers format from the first row only, and
    # errors='coerce' silently turns the non-matching rows into NaT — which
    # then sort last via na_position='last' and trigger spurious "no prior
    # BUY" drift warnings on later SELLs.
    df = trades_df.copy()
    df["_sort_ts"] = pd.to_datetime(
        df["traded_at"], errors="coerce", utc=True, format="ISO8601"
    )
    df = df.sort_values(["_sort_ts", "id"], ascending=True, na_position="last")

    for _, row in df.iterrows():
        ticker = str(row.get("ticker", "")).upper().strip()
        action = str(row.get("action", "")).upper()
        try:
            shares = float(row.get("shares") or 0)
            price  = float(row.get("price")  or 0)
        except (TypeError, ValueError):
            continue
        if not ticker or shares <= 0 or price <= 0:
            continue

        # Stock-split adjustment rows OVERWRITE the holding state rather than
        # accumulating. The Apply-Split handler in the Portfolio page inserts a
        # row with action='SPLIT', shares=adjusted_total_shares, price=
        # adjusted_avg_cost — exactly the new state of the holding after the
        # split. Without this branch, a Rebuild after a split would replay the
        # pre-split BUYs and overwrite the user's approved post-split holdings.
        if "SPLIT" in action:
            if shares > 0 and price > 0:
                holdings[ticker] = {"shares": shares, "avg_cost": price}
            continue

        if "BUY" in action:
            if ticker in holdings:
                h = holdings[ticker]
                new_shares = h["shares"] + shares
                if new_shares > 0:
                    h["avg_cost"] = (h["shares"] * h["avg_cost"] + shares * price) / new_shares
                    h["shares"]   = new_shares
            else:
                holdings[ticker] = {"shares": shares, "avg_cost": price}

        elif "SELL" in action:
            trade_id = row.get("id")
            if ticker in holdings:
                h               = holdings[ticker]
                correct_basis   = h["avg_cost"]
                correct_pnl     = round((price - correct_basis) * shares, 2)
                stored_pnl      = None
                try:
                    stored_pnl  = float(row.get("realized_pnl") or 0)
                except (TypeError, ValueError):
                    stored_pnl  = None
                stored_basis    = None
                try:
                    stored_basis = float(row.get("cost_basis") or 0)
                except (TypeError, ValueError):
                    stored_basis = None
                # Record a correction only when the stored figures actually differ —
                # avoid no-op DB writes on already-correct rows.
                needs_pnl_fix   = stored_pnl   is None or abs(stored_pnl   - correct_pnl)   > 0.01
                needs_basis_fix = stored_basis is None or abs(stored_basis - correct_basis) > 0.01
                if trade_id is not None and (needs_pnl_fix or needs_basis_fix):
                    corrections[int(trade_id)] = {
                        "realized_pnl":     correct_pnl,
                        "cost_basis":       round(correct_basis, 4),
                        "stored_pnl":       stored_pnl,
                        "stored_basis":     stored_basis,
                    }
                new_shares = h["shares"] - shares
                # Over-sell detection: a SELL larger than the open position
                # would silently drive shares negative and delete the row,
                # leaving no breadcrumb that the trade history is internally
                # inconsistent. Surface it as a warning and clamp at 0.
                if new_shares < -1e-6:
                    warnings.append(
                        f"SELL {ticker} {shares:.4f}sh exceeds current {h['shares']:.4f}sh "
                        f"on hand — trade history is internally inconsistent. "
                        f"Position closed; the over-sold {abs(new_shares):.4f}sh delta is unaccounted for."
                    )
                    new_shares = 0.0
                h["shares"] = new_shares
                if h["shares"] <= 1e-6:
                    del holdings[ticker]
            else:
                warnings.append(
                    f"SELL {ticker} {shares:.0f}sh @ ${price:.2f} has no prior BUY "
                    "in the trade history — cost basis can't be computed."
                )

    holdings_list = [
        {"Ticker": tk, "Shares": h["shares"], "Avg Cost ($)": round(h["avg_cost"], 4)}
        for tk, h in holdings.items()
    ]
    holdings_df = pd.DataFrame(holdings_list, columns=["Ticker", "Shares", "Avg Cost ($)"])

    return {
        "holdings_df":              holdings_df,
        "realized_pnl_corrections": corrections,
        "warnings":                 warnings,
    }


# ── Recommendations log ──────────────────────────────────────────────────────
#
# Every pick surfaced by Today's Brief (new_picks, add_positions, buy_candidates)
# is first-seen-captured here so we can answer questions like "how often did the
# App recommend X over the last month" and "which recs got acted on." Stored
# with a unique constraint on (ticker, rec_date, rec_type) so re-renders of the
# Brief during the same day don't create duplicates — only the first surface
# of each (ticker, type) on a given date is recorded.

_REC_COLS = ["id", "ticker", "rec_date", "rec_type", "surfaced_at",
             "price_at_surface", "composite_score", "momentum_score",
             "sector", "conviction", "verdict", "thesis"]


def save_recommendations(records: list[dict]) -> dict:
    """
    Persist new recommendations.

    Returns a diagnostic dict:
      {"attempted": N, "saved": M, "error": str | None}

    `attempted` counts records that passed normalization (had a ticker, date,
    and rec_type). `saved` is len(attempted) on success — supabase-py with
    ignore_duplicates=True doesn't tell us how many rows were actual inserts
    vs ignored, but a non-zero number here means the request didn't error
    out. `error` is a short string on failure (None on success).

    The DB defaults `surfaced_at` to now() — don't set it client-side so the
    first-seen timestamp is server-authoritative.
    """
    if _READONLY: return {"attempted": 0, "saved": 0, "error": "read-only"}  # read-only viewer: no-op
    if not records or not has_db():
        return {"attempted": 0, "saved": 0, "error": None}
    payload = []
    for r in records:
        tk = str(r.get("ticker", "")).strip().upper()
        rt = str(r.get("rec_type", "")).strip()
        rd = r.get("rec_date")
        if not tk or not rt or rd is None:
            continue
        rd_str = rd.isoformat() if hasattr(rd, "isoformat") else str(rd)[:10]
        # price_at_surface is what the rec was priced at when first seen.
        # Coerce defensively — a zero or negative price isn't useful, store
        # NULL in that case so downstream "would-have-gained" math can skip it.
        _pas = r.get("price_at_surface")
        try:
            _pas = float(_pas) if _pas is not None else None
            if _pas is not None and _pas <= 0:
                _pas = None
        except (TypeError, ValueError):
            _pas = None
        payload.append({
            "ticker":           tk,
            "rec_date":         rd_str,
            "rec_type":         rt,
            "price_at_surface": _pas,
            "composite_score":  r.get("composite_score"),
            "momentum_score":   r.get("momentum_score"),
            "sector":           r.get("sector"),
            "conviction":       r.get("conviction"),
            "verdict":          r.get("verdict"),
            "thesis":           (str(r.get("thesis") or "")[:600]) or None,
        })
    if not payload:
        return {"attempted": 0, "saved": 0, "error": None}
    # Try the modern upsert path first. Some older supabase-py versions don't
    # accept ignore_duplicates as a kwarg; if that's the case, fall back to a
    # plain insert and rely on the unique constraint to reject dups.
    try:
        _client().table("recommendations").upsert(
            payload,
            on_conflict="ticker,rec_date,rec_type",
            ignore_duplicates=True,
        ).execute()
        return {"attempted": len(payload), "saved": len(payload), "error": None}
    except TypeError:
        pass  # ignore_duplicates kwarg unsupported on this version
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=f"rec_log_upsert: {str(e)[:100]}")
        return {"attempted": len(payload), "saved": 0, "error": str(e)[:200]}
    # Fallback path — older client
    try:
        _client().table("recommendations").insert(payload).execute()
        return {"attempted": len(payload), "saved": len(payload), "error": None}
    except Exception as e:
        from stock_analyzer import api_health as _ah
        _ah.record("supabase", "error", msg=f"rec_log_insert: {str(e)[:100]}")
        return {"attempted": len(payload), "saved": 0, "error": str(e)[:200]}


def load_recommendations(start_date=None, end_date=None) -> pd.DataFrame:
    """
    Read recommendation history. Defaults to last 30 days when no range given.
    Returns a DataFrame ordered by surfaced_at descending.
    """
    empty = pd.DataFrame(columns=_REC_COLS)
    if not has_db():
        return empty
    try:
        q = _client().table("recommendations").select("*")
        if start_date is not None:
            sd = start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date)[:10]
            q = q.gte("rec_date", sd)
        if end_date is not None:
            ed = end_date.isoformat() if hasattr(end_date, "isoformat") else str(end_date)[:10]
            q = q.lte("rec_date", ed)
        rows = q.order("surfaced_at", desc=True).execute().data
        return pd.DataFrame(rows) if rows else empty
    except Exception:
        return empty


def save_watchlist(tickers: list[str]) -> bool:
    """Atomic-ish replace via upsert + sweep — same pattern as save_holdings.

    The watchlist table already has UNIQUE(ticker), so the upsert is safe.
    Building the deduped list first means malformed input never reaches the
    DB; the sweep at the end is idempotent.
    """
    if _READONLY: return False  # read-only viewer: no-op
    cleaned: list[str] = []
    seen: set[str] = set()
    for t in tickers:
        u = t.strip().upper()
        if u and u not in seen:
            cleaned.append(u)
            seen.add(u)
    if not has_db():
        return False
    try:
        client = _client()
        if cleaned:
            client.table("watchlist").upsert(
                [{"ticker": t} for t in cleaned], on_conflict="ticker"
            ).execute()
        sweep = client.table("watchlist").delete()
        if cleaned:
            sweep = sweep.not_.in_("ticker", cleaned)
        else:
            sweep = sweep.neq("ticker", "")
        sweep.execute()
        return True
    except Exception as e:
        st.error(f"⛔ Failed to save watchlist: {e}")
        return False


# ── Manual stops ──────────────────────────────────────────────────────────────
# User-set stop overrides recorded when the Brief's "raise stop" recommendation
# is actioned. build_portfolio_df merges these on top of the ATR-derived stop
# so all downstream consumers (Brief, Analysis, Scorecard, risk advisor) see
# the user's chosen value rather than the computed default. Without this,
# the same recommendation re-fires every render because the system has no
# record the user acted on it.

def load_manual_stops() -> dict:
    """Return {ticker: {"stop_price", "set_at", "note", "source_action"}}.

    Empty dict when DB is offline or table empty. Failure to read returns
    empty rather than raising so the rest of the app keeps working with
    ATR-derived stops — graceful degradation, not silent gate disable.
    """
    if not has_db():
        return {}
    try:
        rows = (
            _client().table("manual_stops")
            .select("ticker,stop_price,set_at,note,source_action")
            .execute().data
        )
        out: dict = {}
        for r in (rows or []):
            t = str(r.get("ticker", "")).upper().strip()
            try:
                sp = float(r.get("stop_price") or 0)
            except (TypeError, ValueError):
                continue
            if not t or sp <= 0:
                continue
            out[t] = {
                "stop_price":    sp,
                "set_at":        r.get("set_at"),
                "note":          r.get("note") or "",
                "source_action": r.get("source_action") or "",
            }
        return out
    except Exception:
        return {}


def save_manual_stop(ticker: str, stop_price: float,
                     note: str | None = None,
                     source_action: str | None = None) -> bool:
    """Upsert a manual stop for the ticker. Returns True on success."""
    if _READONLY: return False  # read-only viewer: no-op
    t = str(ticker or "").upper().strip()
    try:
        sp = float(stop_price)
    except (TypeError, ValueError):
        return False
    if not t or sp <= 0 or not has_db():
        return False
    try:
        from datetime import datetime, timezone
        record = {
            "ticker":        t,
            "stop_price":    sp,
            "set_at":        datetime.now(timezone.utc).isoformat(),
            "note":          (note or "").strip() or None,
            "source_action": (source_action or "").strip() or None,
        }
        _client().table("manual_stops").upsert(record, on_conflict="ticker").execute()
        return True
    except Exception as e:
        st.error(f"⛔ Failed to save manual stop for {t}: {e}")
        return False


def clear_manual_stop(ticker: str) -> bool:
    """Remove the manual stop override for the ticker (revert to ATR)."""
    if _READONLY: return False  # read-only viewer: no-op
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return False
    try:
        _client().table("manual_stops").delete().eq("ticker", t).execute()
        return True
    except Exception as e:
        st.error(f"⛔ Failed to clear manual stop for {t}: {e}")
        return False


def load_fundamentals_cache(ticker: str) -> dict | None:
    """Return the last-known-good fundamentals for a ticker, or None.

    Shape: {"financials": {...}, "fetched_at": "<iso>"}. Returns None when the
    DB is offline, the table doesn't exist yet, or there's no row — so the
    feature degrades to current behaviour (live-only) until the table is
    created. Never raises: a cache miss must not break the data path.
    """
    t = str(ticker or "").upper().strip()
    if not t or not has_db():
        return None
    try:
        rows = (
            _client().table("fundamentals_cache")
            .select("financials,fetched_at")
            .eq("ticker", t).limit(1).execute().data
        )
        if not rows:
            return None
        row = rows[0]
        fin = row.get("financials")
        if not isinstance(fin, dict) or not fin:
            return None
        return {"financials": fin, "fetched_at": row.get("fetched_at")}
    except Exception:
        return None


def _json_safe(obj):
    """Coerce a value tree to JSON/jsonb-safe Python primitives.

    The fundamentals dict can carry numpy scalars (yfinance/pandas) or non-finite
    floats (NaN/inf) — neither survives the JSON serialisation the Supabase
    client does, and because save_* swallows errors that would fail the write
    SILENTLY (cache never populates, invisibly). Normalise here so the write is
    robust: numpy scalars → native via .item(); NaN/inf → None; recurse dict/list.
    """
    import math
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if hasattr(obj, "item"):          # numpy / pandas scalar
        try:
            v = obj.item()
            return _json_safe(v)
        except Exception:
            return None
    return obj


def save_fundamentals_cache(ticker: str, financials: dict) -> bool:
    """Upsert the last-known-good fundamentals for a ticker (write-through on a
    successful live fetch). Best-effort: a failure (e.g. table not created yet)
    is swallowed so it never disrupts the data path."""
    t = str(ticker or "").upper().strip()
    if not t or not financials or not has_db():
        return False
    try:
        from datetime import datetime, timezone
        record = {
            "ticker":     t,
            "financials": _json_safe(financials),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        _client().table("fundamentals_cache").upsert(record, on_conflict="ticker").execute()
        return True
    except Exception:
        return False
