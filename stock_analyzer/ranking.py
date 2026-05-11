"""
Universe-relative score ranking.

Takes a portfolio DataFrame and the scan_sectors() result for the full universe,
then computes each holding's rank, percentile, and tier vs every scanned ticker.
"""

import pandas as pd


def tier_label(percentile: float) -> tuple[str, str]:
    """(label, hex_color) for a given percentile (0 = worst, 100 = best)."""
    if percentile >= 90:
        return "Top Decile 🏆", "#00C851"
    elif percentile >= 75:
        return "Top Quartile", "#4CAF50"
    elif percentile >= 50:
        return "Above Median", "#aaaaaa"
    elif percentile >= 25:
        return "Below Median", "#ffbb33"
    elif percentile >= 10:
        return "Bottom Quartile", "#ff8800"
    else:
        return "Bottom Decile ⚠️", "#ff4444"


def rank_holdings_in_universe(
    port_df: pd.DataFrame,
    scan_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Cross-reference each holding against the full scanner universe.

    Returns a DataFrame (sorted best → worst rank) with:
      Ticker, Sector, Universe Rank, of, Percentile, Tier,
      Scanner Score, Composite Score, Sector Rank, In Universe
    """
    if scan_df.empty or port_df.empty:
        return pd.DataFrame()

    total = len(scan_df)
    # Lookup: ticker → full scan row
    universe = {row["Ticker"]: row for _, row in scan_df.iterrows()}

    rows = []
    for _, prow in port_df.iterrows():
        ticker     = prow["Ticker"]
        u          = universe.get(ticker)
        comp_score = round(float(prow["Score"]), 0)

        if u is not None:
            u_rank     = int(u["Rank"])
            u_score    = float(u["Score"])
            u_sector   = str(u.get("Sector", ""))
            percentile = round((total - u_rank + 1) / total * 100, 1)

            # Rank within scanner's own sector grouping
            sec_rows = (
                scan_df[scan_df["Sector"] == u_sector]
                .sort_values("Score", ascending=False)
                .reset_index(drop=True)
            )
            s_pos = sec_rows[sec_rows["Ticker"] == ticker]
            sector_rank = (
                f"{int(s_pos.index[0]) + 1}/{len(sec_rows)}"
                if not s_pos.empty else "—"
            )
        else:
            u_rank = u_score = u_sector = percentile = None
            sector_rank = "—"

        tier, _ = tier_label(percentile) if percentile is not None else ("—", "#888888")

        rows.append({
            "Ticker":          ticker,
            "Sector":          prow["Sector"],
            "Universe Rank":   u_rank,
            "of":              total,
            "Percentile":      percentile,
            "Tier":            tier,
            "Scanner Score":   u_score,
            "Composite Score": comp_score,
            "Sector Rank":     sector_rank,
            "_scanner_sector": u_sector,   # internal — for alternatives lookup
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(
            ["Composite Score", "Universe Rank"],
            ascending=[False, True],
            na_position="last",
        ).reset_index(drop=True)
    return df


def sector_alternatives(
    ticker: str,
    scanner_sector: str,
    scan_df: pd.DataFrame,
    n: int = 3,
) -> list[dict]:
    """Top-N scoring tickers from the same scanner sector, excluding `ticker`."""
    if scan_df.empty or not scanner_sector:
        return []
    sec = (
        scan_df[scan_df["Sector"] == scanner_sector]
        .sort_values("Score", ascending=False)
        .head(n + 1)
    )
    return [
        {"ticker": row["Ticker"], "score": float(row["Score"]), "signal": row["Signal"]}
        for _, row in sec.iterrows()
        if row["Ticker"] != ticker
    ][:n]
