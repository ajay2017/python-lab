"""Decision-context capture — Concept E, Phase 1 (passive snapshot).

Freezes the state of the world at the moment of an *interactive* trade write
(a live Buy or Sell through the Trade Journal form) as a schema-versioned,
JSON-safe blob stored on ``trades.decision_context``. Retrospective learning
is fundamentally limited if you can only see WHAT you did and not WHY the world
appeared to support it at the time — and past composite decompositions, regimes,
and recommendation states cannot be reconstructed after the fact. So capture
must start now; the retrospective viewer (Phase 3) can wait until history exists.

Design invariants (per docs/plans/next-evolution-strategy.md, Concept E):
  * PURE + None-safe. Every field degrades to None/{} — a missing input never
    raises and never blocks the trade write.
  * No API calls, no I/O. Reads only values the caller already has in session
    state; costs almost nothing.
  * Schema-versioned from day one (``v``). The Phase-3 viewer must handle
    snapshots from earlier versions gracefully, so never repurpose a key —
    add new ones and bump SCHEMA_VERSION.
  * Scoped to interactive writes ONLY. Broker/screenshot/split imports and the
    recalculate_from_trades replay assemble their own record dicts and never
    call this — a retroactive/batch write carries no live decision context.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

# Bump on any change to the snapshot SHAPE so the Phase-3 viewer can branch on
# version. Additive keys within a version are fine (viewer treats absent keys as
# None); a shape change (renamed/removed key, changed nesting) needs a bump.
SCHEMA_VERSION = 1


def _num(x) -> float | None:
    """Coerce to float, or None for missing / non-numeric / NaN."""
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None  # v != v filters NaN
    except (TypeError, ValueError):
        return None


def build_snapshot(
    *,
    ticker: str | None,
    action: str | None,
    signal_seen: str | None = None,
    portfolio_value=None,
    portfolio_beta=None,
    highbeta_share=None,
    port_df: "pd.DataFrame | None" = None,
    macro_regime: dict | str | None = None,
    market_tone: str | None = None,
    actions=None,
    captured_at: datetime | None = None,
) -> dict:
    """Return a schema-versioned, JSON-safe decision-context snapshot.

    All arguments are the values the caller already holds in session state at
    the trade-write moment; every one is optional and degrades to None.

    ``macro_regime`` may be the cached regime dict (from the
    ``_macro_regime_*`` session key) — its ``regime``/``label``/``confidence``
    are extracted — or a bare label string, or None.
    """
    # ── Portfolio shape: top sector share + position count (from Market Value)
    top_sector = None
    n_positions = None
    try:
        if port_df is not None and not port_df.empty:
            n_positions = int(len(port_df))
            if {"Sector", "Market Value"}.issubset(port_df.columns):
                by_sector = port_df.groupby("Sector")["Market Value"].sum()
                total = float(port_df["Market Value"].sum())
                if total > 0 and not by_sector.empty:
                    top_sector = {
                        "sector": str(by_sector.idxmax()),
                        "weight_pct": _num(by_sector.max() / total * 100.0),
                    }
    except Exception:
        top_sector, n_positions = None, None

    # ── Macro regime: accept the cached dict, a bare label, or None
    regime_out = None
    try:
        if isinstance(macro_regime, dict):
            regime_out = {
                "regime": macro_regime.get("regime"),
                "label": macro_regime.get("label"),
                "confidence": macro_regime.get("confidence"),
            }
        elif macro_regime:
            regime_out = {"label": str(macro_regime)}
    except Exception:
        regime_out = None

    # ── Active-recommendation load at decision time
    act_today_n = None
    try:
        if actions is not None:
            act_today_n = int(len(actions))
    except Exception:
        act_today_n = None

    _sig = (str(signal_seen).strip() or None) if signal_seen else None

    snap = {
        "v": SCHEMA_VERSION,
        "captured_at": (captured_at or datetime.now(timezone.utc)).isoformat(),
        "ticker": str(ticker) if ticker else None,
        "action": str(action) if action else None,
        "signal": {"signal_seen": _sig},
        "market": {"macro_regime": regime_out, "tone": market_tone or None},
        "portfolio": {
            "value": _num(portfolio_value),
            "beta": _num(portfolio_beta),
            "highbeta_share_pct": _num(highbeta_share),
            "n_positions": n_positions,
            "top_sector": top_sector,
        },
        "active_recs": {"act_today_n": act_today_n},
    }

    # Strip any non-JSON-serializable types (numpy scalars, Timestamps) so the
    # Supabase client can write it straight to a jsonb column.
    return json.loads(json.dumps(snap, default=str))
