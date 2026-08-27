"""Gate Suppression Ledger — capture-half builder.

Pure. No DB, no Streamlit, no clock reads. Takes `today` as an argument.
Called by cron_runner._run_scan (source="cron") and app.py (source="app")
after each Grow Today build.

Naming / lane semantics:
  - "new_pick"   : sites 1-2 (macro_blocked_picks, sector_blocked_picks)
  - "add_winner" : sites 3-9 (all add-to-winner suppression buckets)
  - "tone"       : G-23 bear-day synthetic row

sentinel contract — MUST branch `is None` vs `[]`:
  - grow is None  → offline; return [] (gates never ran — do not record)
  - grow is {}    → gates ran; no bucket key → no rows for that bucket
  - bucket is []  → gates ran; bucket checked and found nothing
  Both {} and [] yield no rows, but they are semantically distinct and
  must remain reachable separately in tests.
"""
from __future__ import annotations

from stock_analyzer.constants import MARKET_TONE_BEAR_PCT

# Bucket name → lane label.
_BUCKET_LANES: dict[str, str] = {
    "macro_blocked_picks":       "new_pick",
    "sector_blocked_picks":      "new_pick",
    "sector_blocked_adds":       "add_winner",
    "risk_blocked_adds":         "add_winner",
    "concentration_blocked_adds": "add_winner",
    "cooldown_adds":             "add_winner",
    "deterioration_blocked_adds": "add_winner",
}

_MAX_REASON_LEN = 300


def build_suppression_rows(
    grow: "dict | None",
    *,
    rec_date,
    source: str,
    tone: "str | None" = None,
    sp500_pct: "float | None" = None,
) -> "list[dict]":
    """Build the rows to upsert into gate_suppressions.

    Parameters
    ----------
    grow:
        The grow_today dict returned by build_daily_briefing, or None when
        offline.  `None` means the gates never ran — return [] without
        recording anything.
    rec_date:
        The briefing date (date object or ISO string).
    source:
        "cron" or "app" — goes into the dedup key.
    tone:
        Market tone from the brief ("bull" | "flat" | "bear" | None).
    sp500_pct:
        S&P 500 same-day % change — used for the G-23 bear-day row.
    """
    # Offline: gates never ran. Recording "nothing suppressed" would be a lie.
    if grow is None:
        return []

    rec_date_str = (
        rec_date.isoformat() if hasattr(rec_date, "isoformat") else str(rec_date)[:10]
    )

    # Bear day: the add + pick lane early-returns before any bucket is built,
    # so all buckets are absent (not just empty). Emit exactly ONE synthetic row
    # for the tone gate — the binding suppression that day.
    if tone == "bear":
        return [
            {
                "ticker":          "__MARKET__",
                "gate_id":         "G-23",
                "lane":            "tone",
                "counterfactual":  True,
                "gate_value":      sp500_pct,
                "gate_threshold":  MARKET_TONE_BEAR_PCT,
                "tone":            "bear",
                "composite_score": None,
                "momentum_score":  None,
                "sector":          None,
                "price_at_suppress": None,
                "reason":          None,
                "rec_date":        rec_date_str,
                "source":          source,
            }
        ]

    rows: list[dict] = []

    for bucket_name, lane in _BUCKET_LANES.items():
        bucket = grow.get(bucket_name)
        # Both None (key absent) and [] yield no rows; both are valid — the
        # caller's tests must reach each path to prove this is branched, not
        # collapsed.
        if not bucket:
            continue
        for item in bucket:
            gate_id = item.get("gate_id")
            if gate_id is None:
                # F4: never infer gate_id from the bucket name. Skip unlabelled
                # items — a missing label means step 2 missed a site, and a wrong
                # id is worse than a missing row.
                continue

            ticker = str(item.get("ticker", "")).strip().upper()
            if not ticker:
                continue

            # price_at_suppress: NULL when <= 0, per price_at_surface convention.
            # The producer stores the price as "price" (added at all 9 sites).
            _price = item.get("price")
            try:
                price_val: "float | None" = float(_price) if _price is not None else None
                if price_val is not None and price_val <= 0:
                    price_val = None
            except (TypeError, ValueError):
                price_val = None

            # reason: free text, truncated to 300 chars. Nothing may ever parse it.
            _reason = item.get("reason")
            reason = str(_reason)[:_MAX_REASON_LEN] if _reason is not None else None

            # Scores are explicit at the producer (daily_briefing.py sites 1-9).
            # Read directly; never infer from lane or the generic "score" key.
            composite_score = item.get("composite_score")
            momentum_score  = item.get("momentum_score")

            rows.append({
                "ticker":           ticker,
                "gate_id":          gate_id,
                "lane":             lane,
                "counterfactual":   item.get("counterfactual"),
                "gate_value":       item.get("gate_value"),
                "gate_threshold":   item.get("gate_threshold"),
                "tone":             tone,
                "composite_score":  composite_score,
                "momentum_score":   momentum_score,
                "sector":           item.get("sector"),
                "price_at_suppress": price_val,
                "reason":           reason,
                "rec_date":         rec_date_str,
                "source":           source,
            })

    return rows
