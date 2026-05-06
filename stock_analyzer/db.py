"""
Persistence layer — reads/writes holdings and watchlist to Supabase.

Supabase setup (run once in the SQL Editor at supabase.com):

    create table holdings (
        id    bigint primary key generated always as identity,
        ticker    text    not null,
        shares    numeric not null check (shares > 0),
        avg_cost  numeric not null check (avg_cost > 0),
        updated_at timestamptz default now()
    );

    create table watchlist (
        id    bigint primary key generated always as identity,
        ticker text not null unique,
        added_at timestamptz default now()
    );

When Supabase is not configured the module falls back to Streamlit session
state so the app still works fully in local development.
"""

import streamlit as st
import pandas as pd

_DEFAULT_HOLDINGS = [
    {"Ticker": "AVGO", "Shares": 10, "Avg Cost ($)": 180.0},
    {"Ticker": "AAPL", "Shares": 20, "Avg Cost ($)": 165.0},
    {"Ticker": "TSLA", "Shares": 15, "Avg Cost ($)": 220.0},
    {"Ticker": "CRWD", "Shares": 8,  "Avg Cost ($)": 300.0},
    {"Ticker": "DELL", "Shares": 25, "Avg Cost ($)": 85.0},
    {"Ticker": "PLTR", "Shares": 50, "Avg Cost ($)": 25.0},
    {"Ticker": "NET",  "Shares": 20, "Avg Cost ($)": 90.0},
]
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
        except Exception as e:
            st.warning(f"DB read error: {e} — using session cache")
    # Fallback: session state
    return st.session_state.get(
        "_holdings_fallback",
        pd.DataFrame(_DEFAULT_HOLDINGS),
    )


def save_holdings(df: pd.DataFrame) -> bool:
    """Persist the holdings DataFrame. Returns True on success."""
    # Always keep session fallback in sync
    st.session_state["_holdings_fallback"] = df.copy()

    if not has_db():
        return True

    try:
        client = _client()
        # Full replace: delete all rows then re-insert
        client.table("holdings").delete().gte("id", 0).execute()
        records = []
        for _, row in df.iterrows():
            ticker = str(row.get("Ticker", "")).strip().upper()
            shares = float(row.get("Shares", 0))
            avg_cost = float(row.get("Avg Cost ($)", 0))
            if ticker and shares > 0 and avg_cost > 0:
                records.append({"ticker": ticker, "shares": shares, "avg_cost": avg_cost})
        if records:
            client.table("holdings").insert(records).execute()
        return True
    except Exception as e:
        st.error(f"Failed to save holdings: {e}")
        return False


# ── Watchlist ─────────────────────────────────────────────────────────────────

def load_watchlist() -> list[str]:
    if has_db():
        try:
            rows = _client().table("watchlist").select("ticker").execute().data
            if rows:
                return [r["ticker"] for r in rows]
        except Exception:
            pass
    return st.session_state.get("_watchlist_fallback", list(_DEFAULT_WATCHLIST))


def save_watchlist(tickers: list[str]) -> bool:
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    st.session_state["_watchlist_fallback"] = tickers

    if not has_db():
        return True

    try:
        client = _client()
        client.table("watchlist").delete().gte("id", 0).execute()
        if tickers:
            client.table("watchlist").insert(
                [{"ticker": t} for t in tickers]
            ).execute()
        return True
    except Exception as e:
        st.error(f"Failed to save watchlist: {e}")
        return False
