"""G-25 — new-correlation-cluster add-suppression gate.

Pure logic only -- no Streamlit, no DB, no network. Suppresses Grow Today's
(and Buy Candidates') "add-to-winner" suggestion for the two held tickers at
the endpoints of a newly-formed warning-tier correlation pair (>=
CORR_HIGH_PAIRS_THRESHOLD) since the owner's last acknowledged 🧬 Structural
Scan. Purely a suppression -- never a trim/sell recommendation, and the hard
concentration ceilings (SINGLE_NAME_CEILING, SECTOR_CEILING) are completely
unaffected and stay fully live regardless of this gate's own state.

Full design, all 7 owner decisions, and known limitations:
docs/plans/cluster-add-gate.md.

Three-state contract throughout -- NEVER collapse "couldn't check" into
"checked clean":
  - None            -- couldn't check (corr_df/baseline unavailable, or the
                       underlying cluster detection raised).
  - [] / {}         -- checked; nothing new / nothing to block.
  - populated       -- checked; this is firing.

D2 (scope): only tickers that are themselves an endpoint of a verified NEW
pair (each cluster's own "new_pairs" field) are ever blocked -- never every
transitive member of the broader cluster. D3: no new combined-weight floor,
no new constants.py value -- CORR_HIGH_PAIRS_THRESHOLD is reused as-is.
"""
from __future__ import annotations

from stock_analyzer import portfolio_intelligence
from stock_analyzer import structural_scanner


def baseline_signature(state: dict | None) -> tuple:
    """Collapse a load_structural_scan_baseline_state() result into a small,
    hashable signature so it can be folded into app.py's `_synth_sig` memo
    key -- a fresh Structural Scan (which changes the baseline) must
    invalidate any cached Brief still suppressing an acknowledged cluster.

    ("offline",)       -- state is None (couldn't check this render).
    ("none",)          -- state["status"] == "none" (no scan has ever run).
    ("ok", scan_date)  -- a real baseline exists; keyed on its scan_date so a
                          NEW scan (different scan_date) changes the
                          signature even though the dict "shape" is the same.
    """
    if state is None:
        return ("offline",)
    status = state.get("status") if isinstance(state, dict) else None
    if status == "none":
        return ("none",)
    if status == "ok":
        return ("ok", state.get("scan_date"))
    return ("offline",)  # malformed/unknown shape -- fail safe, never crash


def resolve_new_clusters(corr_df, weights: dict | None, baseline_state: dict | None):
    """Return today's newly-formed warning-tier clusters, or None/[] per the
    three-state contract above.

    None  -- corr_df is None/empty, baseline_state is None (couldn't check --
             D4: this must flow through as "couldn't check," never silently
             become []), or the underlying detection raised.
    []    -- baseline_state["status"] == "none" (no scan has ever run, so
             there is nothing to diff against -- same "first-ever comparison
             with nothing to diff against is not everything is new" rule
             detect_new_clusters_strict itself already applies) OR a real
             baseline exists but nothing new formed.
    list  -- the clusters flagged by detect_new_clusters_strict (each
             carrying its own "new_pairs").
    """
    if corr_df is None or getattr(corr_df, "empty", True):
        return None
    if not isinstance(baseline_state, dict):
        return None
    _status = baseline_state.get("status")
    if _status == "none":
        return []
    if _status != "ok":
        # Anything other than the two recognized shapes ("none" / "ok") is
        # couldn't-check, same as a bare None -- never let a malformed dict
        # fall through and collapse into "checked clean" via an empty
        # prior_snapshot. db.py cannot produce this shape today; guarded
        # anyway so a future change to the loader fails safe by construction.
        return None
    try:
        clusters_today = portfolio_intelligence.correlation_clusters(corr_df, weights)
        prior_snapshot = baseline_state.get("cluster_snapshot")
        return structural_scanner.detect_new_clusters_strict(
            clusters_today, prior_snapshot, corr_df
        )
    except Exception:
        # A crash means "couldn't check," not "checked clean" -- None, not [].
        return None


def add_block_map(new_clusters, corr_df, held_tickers, baseline_scan_date=None) -> dict | None:
    """Build {TICKER: {...}} for every HELD ticker that is itself an endpoint
    of a verified new pair (D2 scope -- never a transitive cluster member
    with no new direct edge of its own).

    None straight through if new_clusters is None (couldn't check).
    {} if new_clusters is [] or nothing held is on a new pair's endpoints.

    Each entry:
        {
            "partners": [(other_ticker, corr), ...],
            "max_new_corr": float,
            "cluster_tickers": [...],   # the broader cluster this pair sits in
            "tier": "danger" | "warning",
            "combined_weight_pct": float,
            "baseline_scan_date": baseline_scan_date,
        }
    Never raises -- any malformed input degrades to None (couldn't verify),
    never a fabricated {}.
    """
    if new_clusters is None:
        return None
    try:
        held = {str(t).upper() for t in (held_tickers or [])}
        blocks: dict[str, dict] = {}
        for cluster in new_clusters:
            pairs = cluster.get("new_pairs", [])
            tier = cluster.get("tier")
            cluster_tickers = cluster.get("tickers", [])
            combined_weight_pct = cluster.get("combined_weight_pct")
            for pair in pairs:
                if not pair or len(pair) != 2:
                    continue
                a, b = str(pair[0]).upper(), str(pair[1]).upper()
                try:
                    corr_ab = float(corr_df.loc[a, b])
                except Exception:
                    continue
                if corr_ab != corr_ab:  # NaN
                    continue
                for this_ticker, other_ticker in ((a, b), (b, a)):
                    if this_ticker not in held:
                        continue  # not currently held -- never suppressed
                    entry = blocks.setdefault(this_ticker, {
                        "partners": [],
                        "max_new_corr": corr_ab,
                        "cluster_tickers": cluster_tickers,
                        "tier": tier,
                        "combined_weight_pct": combined_weight_pct,
                        "baseline_scan_date": baseline_scan_date,
                    })
                    entry["partners"].append((other_ticker, corr_ab))
                    if corr_ab > entry["max_new_corr"]:
                        entry["max_new_corr"] = corr_ab
        return blocks
    except Exception:
        return None
