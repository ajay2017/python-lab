"""Sector gate classification — single source of truth for WHICH sector a
gate (Watchlist ENTER_NOW, Grow Today, Compare, Analysis) checks a ticker
against, and how much of the book it is measured against.

Built 2026-09-30 (sector_gate_spec.md, Opus planner) to close two live holes:

- Watchlist's ENTER_NOW sector-ceiling check summed the RAW provider sector
  label (e.g. "Technology") against port_df["Sector"], which is curated via
  portfolio.resolve_sector — so a mapped name (e.g. a curated "Semiconductors"
  ticker) had its weight summed against the wrong bucket, and the hard
  `>= SECTOR_CEILING` downgrade could never fire.
- Grow Today's unmapped watchlist-extra picks carried the scanner's
  display-only "Watchlist" placeholder as if it were a real sector, which
  defeated the same gate from the other side (the macro gate and G-16 could
  never match a sector named "Watchlist").

Pure module — no Streamlit, no DB, and no gate-file threshold comparison of
its own: this module only RESOLVES which sector/weight a caller's gate
should compare; the caller still owns the actual `>= SECTOR_CEILING` (or
macro-sector) comparison.
"""
from __future__ import annotations

from stock_analyzer.constants import UNCLASSIFIED_SECTOR
from stock_analyzer.portfolio import resolve_sector, TICKER_SECTORS
from stock_analyzer.sector_labels import is_placeholder_sector


def gate_sector(ticker: str, *, row_label: str | None = None,
                 provider_sector: str | None = None) -> str:
    """Resolve the sector a ceiling/macro gate should classify `ticker` into.

    Precedence (owner decision D1, sector_gate_spec.md): a curated
    TICKER_SECTORS entry always wins (handled inside `resolve_sector`);
    otherwise `row_label` if it is a real label (not a placeholder like
    "Watchlist"/""/"nan"/"None"/"Unknown"), else `provider_sector` if real,
    else UNCLASSIFIED_SECTOR. FAIL CLOSED: an unmapped ticker with no usable
    row/provider label resolves to UNCLASSIFIED_SECTOR, never a guess.
    """
    fallback = ""
    if row_label is not None and not is_placeholder_sector(row_label):
        fallback = row_label
    elif provider_sector is not None and not is_placeholder_sector(provider_sector):
        fallback = provider_sector
    return resolve_sector(ticker, fallback)


def sector_gate_context(ticker: str, provider_sector: str | None, port_df) -> dict:
    """Build the {sector, classified, label_source, weight_pct} bundle every
    sector gate needs, computed the SAME way regardless of caller — app.py,
    headless_alert_engine.py, and comparison.py all call this one function so
    the interactive and cron paths can never classify a ticker differently
    (the app/cron-parity invariant sector_gate_spec.md requires).

    Returns:
        sector       : the resolved sector label (curated, provider, or
                        UNCLASSIFIED_SECTOR).
        classified   : False when neither TICKER_SECTORS nor a usable
                        row/provider label resolved the ticker — the sector
                        ceiling and macro sector checks could not run at all
                        for it (a data abstention, not "measured at 0%").
        label_source : "curated" (ticker is in TICKER_SECTORS), "provider"
                        (classified via `provider_sector`), or "none"
                        (unclassified).
        weight_pct   : sum of the gate-weight column ("Gate Weight (%)" if
                        present, else "Weight (%)" — same rule as
                        daily_briefing._gate_wt_col) over
                        port_df["Sector"] == sector. This is the PHANTOM-
                        "Other"-BREACH GUARD: always 0.0 when unclassified,
                        when port_df is None/empty, or when port_df has no
                        "Sector" column — never a spuriously-summed value for
                        a bucket the gate must never compare against (mirrors
                        daily_briefing.py's own UNCLASSIFIED_SECTOR exclusion,
                        ~875-876).
    """
    tk = str(ticker or "").strip().upper()
    sector = gate_sector(ticker, provider_sector=provider_sector)
    classified = sector != UNCLASSIFIED_SECTOR

    if tk and tk in TICKER_SECTORS:
        label_source = "curated"
    elif classified:
        label_source = "provider"
    else:
        label_source = "none"

    weight_pct = 0.0
    if classified and port_df is not None and not port_df.empty and "Sector" in port_df.columns:
        _gcol = "Gate Weight (%)" if "Gate Weight (%)" in port_df.columns else "Weight (%)"
        try:
            weight_pct = float(port_df[port_df["Sector"] == sector][_gcol].sum() or 0)
        except Exception:
            weight_pct = 0.0

    return {
        "sector":       sector,
        "classified":   classified,
        "label_source": label_source,
        "weight_pct":   weight_pct,
    }
