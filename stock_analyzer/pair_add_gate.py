"""G-26 — standing danger-tier correlated pair add-suppression gate.

Pure logic only -- no Streamlit, no DB, no network. Pauses Grow Today's
(and Buy Candidates') "add-to-winner" suggestion for the two HELD tickers at
the endpoints of a STANDING danger-tier correlation pair (`"level" ==
"danger"`, i.e. >= CORR_DANGER_PAIRS_THRESHOLD) -- regardless of whether a
PAIR_RISK trim call actually exists for that pair. portfolio.py's own
PAIR_RISK builder skips a pair when either member lacks "Score Available"
(a fund/ETF, or a stock mid-data-outage), so a MISSING trim card must never
be read as "the pair is safe" -- the correlation itself is the trigger here,
independent of whether a trim card could be built for it.

Purely a suppression -- never a trim/sell recommendation. The hard
concentration ceilings (SINGLE_NAME_CEILING, SECTOR_CEILING) are completely
unaffected and stay fully live regardless of this gate's own state.

Checked BEFORE G-25 (cluster_add_gate) in both _grow_today and
_buy_candidates -- a pair that is both a NEW cluster pairing AND already at
standing danger tier records under G-26, never G-25.

G-26 differs from G-25 in one structural way: it is NOT acknowledge/
override-able. There is no "last Structural Scan" baseline to diff against
-- the pause lasts for as long as the pair stays at danger tier and both
names are held. Trimming the weaker name does NOT lift it on its own; only
the correlation itself dropping below danger tier (or no longer holding one
leg) does.

Three-state contract throughout -- NEVER collapse "couldn't check" into
"checked clean":
  - None            -- couldn't check (corr_df/risk_pairs unavailable, or
                       the underlying computation raised).
  - {}              -- checked; nothing held is on a danger-tier pair.
  - populated       -- checked; this is firing.

Gate threshold note (verified fact, do not re-derive): portfolio.py's
diversification_score() stores "corr" ROUNDED to 2dp but sets "level" from
the UNROUNDED correlation -- a 0.799 pair can display as "0.80" yet still be
"warning" tier. This module therefore gates on `pair["level"] == "danger"`
ONLY, never on a numeric `corr >= CORR_DANGER_PAIRS_THRESHOLD` comparison of
its own -- the "level" field already encodes the correctly-rounded
comparison, computed from the unrounded value.

Owner decisions ratified 2026-10-08 (design doc: docs/plans/pair-add-gate.md;
requirements: F-289 + the 2A.3 G-26 row): trigger = the pair itself, not the
emitted trim call;
no acknowledge/override; checked before G-25; no new constants.py value
(reuses CORR_DANGER_PAIRS_THRESHOLD).
"""
from __future__ import annotations

from stock_analyzer import portfolio

_MAX_REASON_LEN = 300  # matches gate_ledger.py's own free-text reason cap


def _corr_coverage_too_thin(corr_coverage) -> bool:
    """True iff `corr_coverage` names a real, measured `n_obs` below
    `portfolio.CORR_MIN_OBS_TRUSTED` -- i.e. the correlation matrix ran but
    measured too few (or zero) shared observations to trust (an
    entirely-NaN matrix from a holding with a short/non-overlapping history
    collapses the listwise intersection to n_obs==0, yet still produces a
    non-empty matrix and an empty risk_pairs list).

    `corr_coverage` not a dict, or its "n_obs" not a real `int` (bool is
    REJECTED -- True/False are int subclasses -- as are None/str/NaN),
    returns False: an unusable/absent coverage reading must never itself be
    treated as "thin," only a genuinely measured low n_obs may.
    """
    if not isinstance(corr_coverage, dict):
        return False
    n_obs = corr_coverage.get("n_obs")
    if not isinstance(n_obs, int) or isinstance(n_obs, bool):
        return False
    return n_obs < portfolio.CORR_MIN_OBS_TRUSTED


def add_block_map(corr_df, risk_pairs, div_recs, held_tickers, corr_coverage=None) -> "dict | None":
    """Build {TICKER: {"partners": [...], "max_corr": float}} for every HELD
    ticker sitting at the danger-tier end of a pair where BOTH members are
    held.

    None  -- corr_df is None/empty, OR risk_pairs isn't a list, OR
             NOTHING FIRED (the map would be empty) *and* corr_coverage
             names a real n_obs below CORR_MIN_OBS_TRUSTED -- a map that
             DID fire is always returned, never downgraded
             (couldn't check this render -- fail-open: the caller must treat
             this as "couldn't verify," never as "checked, clean").
    {}    -- checked; risk_pairs has no danger-tier pair with both legs held.
    dict  -- checked; this is firing.

    corr_coverage is optional and additive -- portfolio.correlation_coverage()'s
    own dict, passed through unchanged (never `or {}`). This is a THIRD
    "couldn't check" condition alongside the corr_df/risk_pairs guards above:
    when the correlation matrix ran but measured too few shared observations
    to trust (e.g. an entirely-NaN matrix, n_obs==0), this gate must not
    assert "no dangerous pairs" from zero measured data. Omitting
    corr_coverage, or passing None/a non-dict/an n_obs that isn't a real int,
    leaves behaviour completely unchanged from before this parameter
    existed -- this check SUPPRESSES NOTHING on its own; it only stops the
    gate claiming a check it could not actually perform.

    div_recs is diversification_recommendations()'s output (or None when
    that stage itself failed) -- used ONLY to classify each partner's
    trim_call as "named" / "not_named" / "unknown". A None div_recs does
    NOT disable the gate -- the correlation pairing itself (risk_pairs) is
    the trigger, independent of whether a trim card could be built for it.

    Each partner dict: {"partner": TICKER, "corr": float, "trim_call":
    "named"|"not_named"|"unknown", "weaker": TICKER | None}.

    Never raises -- any malformed input degrades to None (couldn't verify),
    never a fabricated {}.
    """
    if corr_df is None or getattr(corr_df, "empty", True):
        return None
    if not isinstance(risk_pairs, list):
        return None
    # NOTE: the thin-coverage check is deliberately NOT here. See the return
    # at the bottom -- it may only downgrade an EMPTY map to "couldn't check",
    # never discard a map that actually fired.
    try:
        held = {str(t).upper() for t in (held_tickers or [])}

        # {frozenset({t1, t2}): weaker_ticker} for every PAIR_RISK rec.
        # None means div_recs itself is unavailable -- every match becomes
        # trim_call="unknown", NOT "no pairs have a named trim."
        _named: "dict[frozenset, str | None] | None" = None
        if isinstance(div_recs, list):
            _named = {}
            for rec in div_recs:
                if not isinstance(rec, dict) or rec.get("type") != "PAIR_RISK":
                    continue
                _t1 = str(rec.get("t1", "")).upper()
                _t2 = str(rec.get("t2", "")).upper()
                if not _t1 or not _t2:
                    continue
                _named[frozenset((_t1, _t2))] = str(rec.get("weaker", "")).upper() or None

        blocks: "dict[str, dict]" = {}
        for pair in risk_pairs:
            if not isinstance(pair, dict) or pair.get("level") != "danger":
                continue
            a = str(pair.get("t1", "")).upper()
            b = str(pair.get("t2", "")).upper()
            if not a or not b or a not in held or b not in held:
                continue  # both must be held -- never suppressed otherwise
            try:
                corr_ab = float(pair.get("corr"))
            except (TypeError, ValueError):
                continue
            if corr_ab != corr_ab:  # NaN
                continue

            pair_key = frozenset((a, b))
            if _named is None:
                trim_call, weaker = "unknown", None
            elif pair_key in _named:
                trim_call, weaker = "named", _named[pair_key]
            else:
                trim_call, weaker = "not_named", None

            for this_ticker, other_ticker in ((a, b), (b, a)):
                entry = blocks.setdefault(this_ticker, {
                    "partners": [],
                    "max_corr": corr_ab,
                })
                entry["partners"].append({
                    "partner":   other_ticker,
                    "corr":      corr_ab,
                    "trim_call": trim_call,
                    "weaker":    weaker,
                })
                if corr_ab > entry["max_corr"]:
                    entry["max_corr"] = corr_ab
        # Thin-coverage downgrade, applied ONLY to an empty map.
        #
        # This ordering is load-bearing and was a blocking review finding
        # (2026-10-09). Checking coverage BEFORE building the map discarded
        # maps that genuinely fired: between 2 and 19 overlapping
        # observations `correlation_matrix` returns REAL correlations, so
        # `risk_pairs` can hold a danger-tier pair and this map can be
        # non-empty. Returning None there would have REMOVED a live
        # suppression -- Grow Today saying "add to AAA" while 📡 Signals &
        # Advice shows a PAIR_RISK trim card for the same pair, built from
        # the same `risk_pairs`. Reproduced at n_obs=11 with a real 1.0
        # danger pair.
        #
        # An EMPTY map is the only ambiguous case: "checked, found nothing"
        # and "couldn't measure anything" are indistinguishable there, and
        # on an all-NaN matrix (n_obs == 0) it is the latter. Downgrading
        # only that case suppresses nothing new and fires the existing
        # "couldn't check" caption honestly.
        if not blocks and _corr_coverage_too_thin(corr_coverage):
            return None
        return blocks
    except Exception:
        return None


def partners_of(entry: "dict | None") -> list:
    """The partner list for ONE add_block_map() entry, always a real list.

    Exists so render sites never need `entry.get("partners") or []` --
    that idiom is the offline-sentinel-collapse class check_antipatterns.py
    blocks, and it would be the wrong shape here anyway: a malformed entry
    should degrade to "no partners to name", not to a falsy value that
    silently reads the same as an empty one. add_block_map() always builds
    `partners` as a list, so this is a defensive accessor, not a sentinel.
    """
    partners = entry.get("partners") if isinstance(entry, dict) else None
    return partners if isinstance(partners, list) else []


def describe_reason(entry: "dict | None") -> str:
    """Build the suppression reason string for ONE held ticker's G-26 block
    entry (an add_block_map() value). <= 300 chars (gate_ledger's own
    free-text cap). Never raises on a malformed entry -- degrades to a
    generic sentence.
    """
    partners = entry.get("partners") if isinstance(entry, dict) else None
    if not isinstance(partners, list):
        partners = []
    clauses: list[str] = []
    for p in partners:
        _trim = p.get("trim_call") if isinstance(p, dict) else None
        if _trim == "named":
            clauses.append("the pair's trim call is on 📡 Signals & Advice")
        elif _trim == "not_named":
            _partner_raw = p.get("partner", "")
            _partner = str(_partner_raw).upper() if _partner_raw else "the partner"
            # Deliberately does NOT say "right now": Score Available is
            # permanently False for a fund/ETF, so a time-bound phrasing
            # would read as a transient outage on a pair that will never
            # produce a trim card. Covers both causes without guessing
            # which one applies.
            clauses.append(
                f"no trim is named because {_partner}'s conviction isn't "
                "scored (a fund, or a data outage) — paused on the "
                "correlation alone"
            )
        else:  # "unknown" or malformed
            clauses.append("trim-call status couldn't be read this run")
    if not clauses:
        clauses = ["trim-call status couldn't be read this run"]
    text = "; ".join(clauses) + ". This pause adds no recommendation of its own."
    return text[:_MAX_REASON_LEN]


def split_scan_liftable(cluster_blocks: "dict | None", pair_blocks: "dict | None"):
    """Split a G-25 `cluster_add_blocks` map's tickers into those that will
    actually lift once the owner reviews the cluster in 🧬 Structural Scan
    (`liftable`) vs. those that will STAY paused afterward because they also
    sit on a standing G-26 danger-tier pair (`also_pair`).

    Returns (liftable, also_pair), both sorted lists of ticker strings.

    `pair_blocks is None` means G-26 couldn't be checked this render --
    fail-open: every G-25-blocked ticker is reported liftable (we have no
    evidence any of them won't be), and `also_pair` is empty.
    """
    if not cluster_blocks:
        return [], []
    cluster_tickers = set(cluster_blocks.keys())
    if pair_blocks is None:
        return sorted(cluster_tickers), []
    pair_tickers = set((pair_blocks or {}).keys())
    also_pair = cluster_tickers & pair_tickers
    liftable = cluster_tickers - pair_tickers
    return sorted(liftable), sorted(also_pair)


def unchecked_disclosure(
    unchecked: "list | None", pair_checked: bool, cluster_checked: bool
) -> "str | None":
    """Render-ready disclosure caption for the fail-open correlation blind
    spot shared by G-25 and G-26 (disclosure-only follow-on,
    docs/plans/pair-add-gate.md).

    The defect this discloses is entirely PARTNER-SIDE: an unpriceable
    ticker can never be an add candidate itself (both add lanes iterate
    port_df, which drops a no-price holding), so the real gap is a PRICED,
    add-eligible ticker sitting on a danger/new-cluster pair with an
    unpriceable partner — that partner never enters `corr_df`
    (`portfolio._close_series_map`), so the pairing can never be detected and
    the add proceeds with no disclosure. Fail-closed is not an option (see
    pair_add_gate.py's own module docstring) — this caption is the entire
    remedy: name the gap, suppress nothing.

    `unchecked` is `portfolio.correlation_unchecked()`'s output (passed
    through `grow_today["corr_unchecked"]`). `pair_checked` /
    `cluster_checked` are the existing `pair_gate_checked` /
    `cluster_gate_checked` render-layer flags — when NEITHER gate was
    checked this run, the existing 🧬/🔗 "couldn't check" captions already
    cover it, so this returns None rather than stacking a third, redundant
    caption.

    Returns
    -------
    None -- neither gate was checked this run (already disclosed elsewhere),
            OR `unchecked == []` (checked, nothing missing).
    str  -- `unchecked is None` (couldn't confirm, but at least one gate DID
            run) -> the generic "couldn't confirm" caption; otherwise names
            up to 5 missing tickers (then "+N more"), singular/plural
            correctly, and never includes a literal `**` or `$`
            (feedback_streamlit_renderer_mismatch — this project has no
            render-mismatch test coverage, so the string itself must stay
            plain).
    """
    if not pair_checked and not cluster_checked:
        return None
    if unchecked == []:
        return None

    both = pair_checked and cluster_checked
    if both:
        _what = "Correlated-pair and new-cluster add-pause checks"
    elif pair_checked:
        _what = "Correlated-pair add-pause check"
    else:
        _what = "New-cluster add-pause check"

    if unchecked is None:
        return (
            "🔍 (couldn't confirm every holding was included in the "
            "correlation add-pause checks this run)"
        )

    try:
        tickers = sorted({str(t).upper() for t in unchecked if str(t).strip()})
    except Exception:
        tickers = []
    if not tickers:
        return None

    _shown = tickers[:5]
    _more  = len(tickers) - len(_shown)
    _names = ", ".join(_shown) + (f" +{_more} more" if _more > 0 else "")
    _plural = len(tickers) > 1
    # Subject pronoun ("it couldn't be priced") vs. object pronoun ("pairing
    # involving it/them") vs. the final clause, which names the ticker(s)
    # again when singular ("AAPL is uncorrelated") but switches to a plain
    # pronoun when plural ("they are uncorrelated") rather than repeating a
    # 5+N-ticker list a second time.
    _subject = "their" if _plural else "its"
    _object  = "them" if _plural else "it"
    _final   = "they are" if _plural else f"{_names} is"

    # "price HISTORY couldn't be loaded", NOT "couldn't be priced" (Opus
    # review, 2026-10-09). _close_series_map also drops a ticker whose bundle
    # DID load but whose history is empty or has no Close column -- such a
    # ticker can still carry a live current_price and show a price elsewhere
    # on Home, so "couldn't be priced" would contradict a number already on
    # screen. The correlation matrix needs the history, not the last price,
    # and that is what this sentence must say.
    return (
        f"🔍 {_what} didn't include {_names} — {_subject} price history "
        f"couldn't be loaded this run, so no correlated pairing involving "
        f"{_object} could be detected. Add suggestions were checked against "
        f"your other holdings only; this is not a sign {_final} uncorrelated."
    )


def buy_lane_only(pair_blocked_adds: "list | None", buy_lane_skips: "list | None"):
    """Tickers G-26 silently skipped inside `_buy_candidates`' own
    add-to-winner lane that are NOT already disclosed in the
    `pair_blocked_adds` bucket (Grow Today's own block) -- i.e. the ones
    that would otherwise vanish with zero disclosure anywhere (a flat/bear
    day, where `_grow_today`'s bull-day add loop never ran at all).

    `buy_lane_skips is None` passes straight through as None (the map itself
    was never checked this render -- nothing to report).
    """
    if buy_lane_skips is None:
        return None
    already = {str((d or {}).get("ticker", "")).upper() for d in (pair_blocked_adds or [])}
    return sorted({str(t).upper() for t in buy_lane_skips} - already)
