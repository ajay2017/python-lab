"""State of the Portfolio — standing thesis (weekly stability ledger).

Pure, deterministic composition of 5 portfolio-scoped claims from ALREADY-
DECIDED existing sources (never a new computation) — see
docs/plans/state-of-portfolio-standing-thesis.md for the full design.

**No LLM anywhere in this module** — enforced by import-absence: this file
must never import `anthropic`/`openai` or any LLM client. `compose_thesis()`
only copies and classifies values its callers already computed elsewhere in
the app; `grade_prior()` only compares two such snapshots. This is the
"AI narrates, never originates" redline (already drawn on Portfolio Q&A,
F-225) applied here as a structural guarantee rather than a policy note.

**Never a predictive score.** `grade_prior()` produces a stability/consistency
ledger — HELD / SHIFTED / not_comparable per claim, never a "was last week's
read right?" verdict. This avoids §5.8's point-forecast prohibition
(docs/plans/next-evolution-strategy.md).

Both public functions are pure (no Streamlit/DB/network I/O) and defensive:
malformed/missing input degrades a claim to `"unavailable"` (or the whole
call to `None`) rather than raising.
"""
from __future__ import annotations

from datetime import date

from stock_analyzer.constants import COMPOSITE_BUY, SECTOR_CEILING, SINGLE_NAME_CEILING
from stock_analyzer.market_time import today_et

# The 5 graded claims, in the fixed order they're rendered/graded. Holding this
# as a tuple (not re-derived from a dict's key order) keeps compose_thesis's
# output and grade_prior's ledger in lockstep even if a future claim is added.
CLAIM_KEYS: tuple[str, ...] = (
    "risk_posture",
    "concentration",
    "correlation_structure",
    "holdings_health",
    "action_posture",
)

_VALID_RAG_LABELS = {"All Clear", "Monitor", "Action Required"}
_VALID_DIV_LABELS = {"Well Diversified", "Moderate", "High Correlation Risk"}


# ── Per-claim classifiers — each independently defensive: a missing/malformed
# input for THIS claim's own source degrades only this claim to "unavailable",
# never raises, and never blanks out the other 4 claims. ────────────────────

def _classify_risk_posture(bundle: dict) -> str:
    label = bundle.get("rag_label")
    return label if label in _VALID_RAG_LABELS else "unavailable"


def _classify_concentration(acct_gate) -> str:
    if not isinstance(acct_gate, dict):
        return "unavailable"
    name_wt   = acct_gate.get("max_name_wt")
    sector_wt = acct_gate.get("max_sector_wt")
    if name_wt is None or sector_wt is None:
        return "unavailable"
    try:
        name_wt   = float(name_wt)
        sector_wt = float(sector_wt)
    except (TypeError, ValueError):
        return "unavailable"
    if name_wt >= SINGLE_NAME_CEILING:
        return "single_name_elevated"
    if sector_wt >= SECTOR_CEILING:
        return "sector_elevated"
    return "within"


def _classify_correlation(bundle: dict) -> str:
    div_label = bundle.get("div_label")
    if div_label not in _VALID_DIV_LABELS:
        return "unavailable"
    # structural_new_clusters is None when the cluster scan itself was offline
    # this session (surface-proprioception F-260 finding #5) -- distinct from
    # `[]` (scan ran, nothing new). Falling through to a div_label-only verdict
    # here would assert "diversified" for a check that never ran, and that
    # verdict gets PERSISTED via save_portfolio_thesis, becoming next week's
    # HELD/SHIFTED grading baseline -- the only claim in this module that can
    # poison a durable record. Matches _classify_concentration/
    # _classify_action_posture's own pattern: a missing required sub-input
    # degrades the WHOLE claim to "unavailable", never a partial verdict.
    new_clusters = bundle.get("structural_new_clusters")
    if not isinstance(new_clusters, (list, tuple)):
        return "unavailable"
    if len(new_clusters) > 0:
        return "concentrated_cluster"
    if div_label == "Well Diversified":
        return "diversified"
    return "elevated"   # "Moderate" or "High Correlation Risk"


def _classify_holdings_health(bundle: dict):
    scores = bundle.get("holdings_scores")
    if not isinstance(scores, (list, tuple)) or len(scores) == 0:
        return "unavailable"
    try:
        numeric = [float(s) for s in scores if s is not None]
    except (TypeError, ValueError):
        return "unavailable"
    if not numeric:
        return "unavailable"
    n_total    = len(numeric)
    n_buy_plus = sum(1 for s in numeric if s >= COMPOSITE_BUY)
    return {"n_buy_plus": n_buy_plus, "n_below": n_total - n_buy_plus, "n_total": n_total}


def _classify_action_posture(bundle: dict, reduce_calls) -> str:
    buy_candidates = bundle.get("buy_candidates")
    if buy_candidates is None or not isinstance(buy_candidates, (list, tuple)):
        return "unavailable"
    if not isinstance(reduce_calls, dict):
        return "unavailable"
    if len(reduce_calls) > 0:
        return "de_risking"
    if len(buy_candidates) > 0:
        return "deploying"
    return "holding"


def _build_prose(claims: dict, engine_trust, today: date) -> str:
    """Plain-template composition of the classified claims above — no LLM,
    no invented facts, no per-ticker detail. Matches the plan doc's worked
    example tone: state the classified facts plainly, never hedge with a
    forward "will"."""
    date_str = today.strftime("%b %d")
    parts: list[str] = []

    # "alert level", not "risk posture". This claim is `rag_label` — a count of
    # danger-level Active Alerts — while 🧾 Summary's Portfolio Health zone
    # separately renders `exit_advisor.market_risk_posture()` under the name
    # "Risk Posture". Both appear on one screen with different values, and the
    # shared name made "Action Required" read as a contradiction of "nothing to
    # act on today" rather than as a different measurement
    # (feedback_pillar_label_collision). The claim KEY is unchanged, so
    # persisted rows and the ledger's held/shifted grading are unaffected.
    risk = claims["risk_posture"]
    if risk == "unavailable":
        parts.append(f"As of {date_str}, alert level is unavailable this week.")
    else:
        parts.append(f"As of {date_str}, your alert level is {risk}.")

    conc = claims["concentration"]
    if conc == "unavailable":
        parts.append("Concentration read is unavailable this week.")
    elif conc == "within":
        parts.append("Concentration sits within both single-name and sector ceilings.")
    elif conc == "single_name_elevated":
        parts.append(f"A single name is elevated above the {SINGLE_NAME_CEILING:.0f}% ceiling.")
    elif conc == "sector_elevated":
        parts.append(f"A sector is elevated above the {SECTOR_CEILING:.0f}% ceiling.")

    corr = claims["correlation_structure"]
    if corr == "unavailable":
        parts.append("Correlation structure is unavailable this week.")
    elif corr == "diversified":
        parts.append("Correlation structure is diversified.")
    elif corr == "elevated":
        parts.append("Correlation is running elevated.")
    elif corr == "concentrated_cluster":
        parts.append("A new correlated cluster has formed since the last check.")

    health = claims["holdings_health"]
    if not isinstance(health, dict):
        parts.append("Holdings composite-health is unavailable this week.")
    else:
        n_total, n_buy, n_below = health["n_total"], health["n_buy_plus"], health["n_below"]
        if n_below > 0:
            parts.append(
                f"Of {n_total} holdings, {n_buy} remain at Buy or better and "
                f"{n_below} have slipped below the entry bar."
            )
        else:
            parts.append(f"Of {n_total} holdings, all {n_buy} remain at Buy or better.")

    action = claims["action_posture"]
    if action == "unavailable":
        parts.append("Action posture is unavailable this week.")
    elif action == "de_risking":
        parts.append("Active reduce call(s) are in place — a de-risking week.")
    elif action == "deploying":
        parts.append("New deployment candidate(s) are active this week.")
    else:
        parts.append("No new deployments and no active reduce calls — a hold-and-watch week.")

    # Cited as context only, never graded — mirrors the plan doc's "Engine
    # Track Record" citation line. Silently omitted (not a new fetch) when
    # the caller doesn't have it cheaply in scope, or when it's still building.
    if isinstance(engine_trust, dict):
        band  = engine_trust.get("band")
        alpha = engine_trust.get("acted_alpha")
        if band and band != "building" and alpha is not None:
            try:
                parts.append(
                    f"Context: Engine Track Record is {band} at {float(alpha):+.1f}pp vs S&P."
                )
            except (TypeError, ValueError):
                pass

    return " ".join(parts)


def compose_thesis(
    bundle: dict | None,
    acct_gate: dict | None,
    reduce_calls: dict | None,
    engine_trust: dict | None = None,
    today: date | None = None,
) -> dict | None:
    """Compose this week's 5-claim standing thesis from already-decided inputs.

    `bundle` carries the claims sourced from Home's synthesis bundle:
      - "rag_label": the bundle's `_rag_label` (All Clear / Monitor / Action Required)
      - "div_label": the bundle's `_div_label` (Well Diversified / Moderate / High Correlation Risk)
      - "structural_new_clusters": the `_structural_alert_cache` list (None when
        offline, [] when checked with nothing new, else the newly-formed clusters)
      - "holdings_scores": list of composite Scores for currently held positions
      - "buy_candidates": the Daily Brief's `buy_candidates` list

    `acct_gate` carries the concentration inputs: {"max_name_wt", "max_sector_wt"}
    (both %, equity-weight basis — the SAME basis the concentration-discipline
    gate uses elsewhere in the app).

    `reduce_calls` is the `_reduce_calls` dict ({ticker: reduce_call_item}).

    `engine_trust` is optional context (never graded) — e.g.
    {"band": ..., "acted_alpha": ...} from Engine Track Record's headline.

    Returns None if `bundle` is missing/malformed (mirrors the
    `_home_synth_cache is None` "offline this session" contract — never
    compose a hollow thesis from a known-missing bundle). Each of the 5
    claims independently degrades to "unavailable" when its OWN source is
    missing — one missing input never blanks out the other 4.
    """
    if not isinstance(bundle, dict) or not bundle:
        return None
    if today is None:
        today = today_et()

    try:
        claims = {
            "risk_posture":          _classify_risk_posture(bundle),
            "concentration":         _classify_concentration(acct_gate),
            "correlation_structure": _classify_correlation(bundle),
            "holdings_health":       _classify_holdings_health(bundle),
            "action_posture":        _classify_action_posture(bundle, reduce_calls),
        }
        prose = _build_prose(claims, engine_trust, today)
        iso_year, iso_week, _ = today.isocalendar()
        return {
            "v":           1,
            "thesis_date": today.isoformat(),
            "iso_year":    int(iso_year),
            "iso_week":    int(iso_week),
            "claims":      claims,
            "prose":       prose,
        }
    except Exception:
        # Defensive backstop — no known path raises past the per-claim guards
        # above, but a composer must never crash the page it's rendered on.
        return None


def grade_prior(this_week: dict | None, prior_week: dict | None) -> dict | None:
    """Stability ledger: per-claim HELD / SHIFTED / not_comparable, comparing
    `this_week["claims"]` to `prior_week["claims"]`.

    Returns None if `prior_week` is None/falsy (caller renders "first standing
    view of the record — nothing to compare yet", never a fabricated grade).

    Per claim (never a single aggregate pass/fail):
      - either side "unavailable"      -> not_comparable
      - identical non-"unavailable"    -> held
      - different non-"unavailable"    -> shifted (with from/to)
    """
    if not isinstance(prior_week, dict) or not prior_week:
        return None
    if not isinstance(this_week, dict) or not this_week:
        return None
    try:
        this_claims  = this_week.get("claims")
        prior_claims = prior_week.get("claims")
        this_claims  = this_claims if isinstance(this_claims, dict) else {}
        prior_claims = prior_claims if isinstance(prior_claims, dict) else {}

        ledger: dict[str, dict] = {}
        for key in CLAIM_KEYS:
            this_v  = this_claims.get(key, "unavailable")
            prior_v = prior_claims.get(key, "unavailable")
            if this_v == "unavailable" or prior_v == "unavailable":
                status = "not_comparable"
            elif this_v == prior_v:
                status = "held"
            else:
                status = "shifted"
            ledger[key] = {"status": status, "from": prior_v, "to": this_v}
        return ledger
    except Exception:
        return None


def should_skip_weekly_write(recent_rows: "list[dict] | None", iso_year: int, iso_week: int) -> bool:
    """True if this week's thesis write should be SKIPPED — either a row for
    (iso_year, iso_week) already exists in `recent_rows`, or the duplicate-
    guard read itself failed (`recent_rows is None`, e.g. a DB outage or
    missing credentials).

    A failed read must never be read as "no thesis written yet this week" —
    that would risk a duplicate weekly row landing on a transient hiccup,
    the exact offline-sentinel-collapse `load_portfolio_thesis_or_none()`
    exists to prevent. Skipping is safe either way: a genuinely-still-needed
    write simply lands on the next successful visit this week, and the DB's
    own UNIQUE(iso_year, iso_week) + upsert is a second backstop against a
    real duplicate row regardless."""
    if recent_rows is None:
        return True
    return any(
        r.get("iso_year") == iso_year and r.get("iso_week") == iso_week
        for r in recent_rows
    )
