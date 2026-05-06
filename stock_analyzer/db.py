"""
Persistence layer — reads/writes holdings and watchlist to Supabase.

IMPORTANT: Run this SQL once in the Supabase SQL Editor to disable RLS
(required for the anon key to read/write without policy configuration):

    ALTER TABLE holdings  DISABLE ROW LEVEL SECURITY;
    ALTER TABLE watchlist DISABLE ROW LEVEL SECURITY;

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
    if has_db():
        try:
            rows = _client().table("holdings").select("*").order("ticker").execute().data
            if rows:
                df = pd.DataFrame(rows)[["ticker", "shares", "avg_cost"]]
                df.columns = ["Ticker", "Shares", "Avg Cost ($)"]
                df["Shares"] = df["Shares"].astype(int)
                df["Avg Cost ($)"] = df["Avg Cost ($)"].astype(float)
                return df
            # Table is empty — return empty frame so user starts fresh
            return pd.DataFrame(columns=["Ticker", "Shares", "Avg Cost ($)"])
        except Exception as e:
            err = str(e)
            if "row-level security" in err.lower() or "rls" in err.lower() or "42501" in err:
                st.error(
                    "⛔ Supabase RLS is blocking reads. "
                    "Run `ALTER TABLE holdings DISABLE ROW LEVEL SECURITY;` "
                    "in your Supabase SQL Editor, then refresh."
                )
            else:
                st.error(f"⛔ DB read error: {err}")
    # No DB configured — return empty frame
    return pd.DataFrame(columns=["Ticker", "Shares", "Avg Cost ($)"])


def save_holdings(df: pd.DataFrame) -> bool:
    """Persist the holdings DataFrame to Supabase. Returns True on success."""
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
        return True
    except Exception as e:
        err = str(e)
        if "row-level security" in err.lower() or "42501" in err:
            st.error(
                "⛔ Supabase RLS is blocking writes. "
                "Run `ALTER TABLE holdings DISABLE ROW LEVEL SECURITY;` "
                "in your Supabase SQL Editor."
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
