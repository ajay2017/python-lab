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

    ALTER TABLE public.holdings  ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.watchlist ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.trades    ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS "Allow all (service role)" ON public.holdings;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.watchlist;
    DROP POLICY IF EXISTS "Allow all (service role)" ON public.trades;

    CREATE POLICY "Allow all (service role)" ON public.holdings
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.watchlist
        FOR ALL TO service_role USING (true) WITH CHECK (true);
    CREATE POLICY "Allow all (service role)" ON public.trades
        FOR ALL TO service_role USING (true) WITH CHECK (true);

Table schema (run once if tables don't exist):

    create table if not exists holdings (
        id         bigint primary key generated always as identity,
        ticker     text    not null,
        shares     numeric not null check (shares > 0),
        avg_cost   numeric not null check (avg_cost > 0),
        updated_at timestamptz default now()
    );

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
"""

import streamlit as st
import pandas as pd

_DEFAULT_WATCHLIST = ["NVDA", "AMD", "INTC", "MU"]


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
    """Persist the holdings DataFrame to Supabase. Returns True on success."""
    from stock_analyzer import api_health as _ah
    if not has_db():
        return False

    try:
        client = _client()
        # Delete all existing rows then re-insert
        client.table("holdings").delete().neq("ticker", "").execute()
        records = []
        for _, row in df.iterrows():
            ticker   = str(row.get("Ticker", "")).strip().upper()
            shares   = float(row.get("Shares", 0) or 0)
            avg_cost = float(row.get("Avg Cost ($)", 0) or 0)
            if ticker and shares > 0 and avg_cost > 0:
                records.append({"ticker": ticker, "shares": shares, "avg_cost": avg_cost})
        if records:
            client.table("holdings").insert(records).execute()
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
    if not has_db():
        return False
    try:
        _client().table("trades").insert(record).execute()
        return True
    except Exception as e:
        st.error(f"⛔ Failed to save trade: {e}")
        return False


def delete_trade(trade_id: int) -> bool:
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
    df = trades_df.copy()
    df["_sort_ts"] = pd.to_datetime(df["traded_at"], errors="coerce")
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
                h["shares"] -= shares
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


def save_watchlist(tickers: list[str]) -> bool:
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    if not has_db():
        return False
    try:
        client = _client()
        client.table("watchlist").delete().neq("ticker", "").execute()
        if tickers:
            client.table("watchlist").insert(
                [{"ticker": t} for t in tickers]
            ).execute()
        return True
    except Exception as e:
        st.error(f"⛔ Failed to save watchlist: {e}")
        return False
