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

from stock_analyzer import portfolio
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


def resolve_new_clusters(corr_df, weights: dict | None, baseline_state: dict | None, *,
                         corr_coverage):
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

    `corr_coverage` (keyword-only, NO default -- a forgotten argument must
    raise, not silently keep the old behaviour) is the SAME matrix's
    portfolio.correlation_coverage() dict. When the detection finds nothing
    AND that sample is THIN, the result is None ("couldn't check"), not []
    (audit 2026-10-11 Low-A, fixed 2026-10-13): on an all-NaN matrix the
    Structural banner used to publish [] as "checked, nothing new" while
    add_block_map, from the same matrix, said "couldn't check". This is
    exactly add_block_map's own rule, mirrored so the two agree:
    ONLY an empty result is downgraded (clusters that were found are never
    discarded -- the F-294 lesson), ONLY a THIN sample downgrades (an
    unknown/unusable coverage leaves [] unchanged, as there), and the
    "no scan has ever run" [] is untouched (nothing to diff, whatever the
    matrix). That last case is the ONE pre-existing disagreement: before any
    Structural Scan baseline exists this returns [] (banner silent) while
    add_block_map([], ..., thin) still reports None. Unreachable once a scan
    has been saved, and unchanged by this fix.
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
        found = structural_scanner.detect_new_clusters_strict(
            clusters_today, prior_snapshot, corr_df
        )
        if not found and portfolio.correlation_sample_state(corr_coverage) == portfolio.CORR_SAMPLE_THIN:
            return None
        return found
    except Exception:
        # A crash means "couldn't check," not "checked clean" -- None, not [].
        return None


def add_block_map(
    new_clusters, corr_df, held_tickers, baseline_scan_date=None, corr_coverage=None
) -> dict | None:
    """Build {TICKER: {...}} for every HELD ticker that is itself an endpoint
    of a verified new pair (D2 scope -- never a transitive cluster member
    with no new direct edge of its own).

    None straight through if new_clusters is None (couldn't check), OR if
    NOTHING FIRED (the map would be empty) *and* corr_coverage names a real
    n_obs below CORR_MIN_OBS_TRUSTED -- a map that DID fire is always
    returned, never downgraded. The
    correlation matrix ran but measured too few shared observations to
    trust (e.g. an entirely-NaN matrix from a short/non-overlapping holding
    history, n_obs==0), so detect_new_clusters having found nothing must not
    be read as "checked, no new cluster."
    {} if new_clusters is [] or nothing held is on a new pair's endpoints.

    corr_coverage is optional and additive -- portfolio.correlation_coverage()'s
    own dict, passed through unchanged (never `or {}`). Omitting it, or
    passing None/a non-dict/an n_obs that isn't a real int (bool is
    REJECTED -- True/False are int subclasses -- as are None/str/NaN),
    leaves behaviour completely unchanged from before this parameter
    existed -- this check SUPPRESSES NOTHING on its own; it only stops the
    gate claiming a check it could not actually perform.

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
    # NOTE: the thin-coverage check is deliberately NOT here -- see the
    # return at the bottom. Same blocking review finding as its
    # pair_add_gate sibling: it may only downgrade an EMPTY map.
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
        # Thin-coverage downgrade, applied ONLY to an empty map -- mirrors
        # pair_add_gate's own return, see that module's fuller comment.
        # Checking before the build discarded maps that genuinely fired
        # (between 2 and 19 overlapping observations the matrix holds REAL
        # correlations, so a new cluster can be detected), which would have
        # REMOVED a live suppression. An empty map is the only case where
        # "checked, found nothing" and "couldn't measure anything" are
        # indistinguishable. Blocking review finding, 2026-10-09.
        if not blocks:
            _sample = portfolio.correlation_sample_state(corr_coverage)
            if _sample == portfolio.CORR_SAMPLE_THIN:
                return None
            if _sample == portfolio.CORR_SAMPLE_UNKNOWN:
                return blocks  # {} -- unusable coverage is never "thin"
            return blocks      # {} -- trusted sample, genuinely nothing fired
        return blocks
    except Exception:
        return None


def baseline_write_allowed(corr_unchecked, corr_coverage) -> bool:
    """May a 🧬 Structural Scan snapshot be SAVED as G-25's baseline today?

    True only when `portfolio.correlation_claim_verified` is True: every held
    ticker entered the correlation matrix AND the overlapping sample is
    trusted (the strict "records withhold" rule, F-291). Never raises -- any
    failure is False.

    Why this exists: the saved `cluster_snapshot` becomes the baseline
    `resolve_new_clusters` diffs against. On an unverified matrix (e.g. 2
    shared bars, where any two same-direction names correlate at exactly 1.0)
    the clusters can be spurious, and saving them would record spurious pairs
    as "acknowledged" so G-25 never fires for them later, while the save's
    memo pop would also lift live add-pauses (audit A-H1, owner decision D3).

    Lives here -- a `_GATE_FILES` member -- so any change to this decision is
    forced through review. It can ONLY ever prevent a pause from being
    LIFTED; it never removes or creates a suppression, so it is
    fail-conservative by construction.
    """
    try:
        return bool(portfolio.correlation_claim_verified(corr_unchecked, corr_coverage))
    except Exception:
        return False
