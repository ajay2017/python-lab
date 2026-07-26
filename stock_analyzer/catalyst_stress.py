"""
Catalyst-Specific Stress — D4, Agentic Intelligence Roadmap v2.

Event-driven twin of regime_stress.py's compound-scenario synthesis: instead of
"what ongoing macro CONDITION threatens this book," asks "what single upcoming
DATED EVENT (a HIGH-impact, sector-specific macro print, or a held position's
own earnings report) threatens the SAME structural weak points P3/P5 already
find."

Two SEPARATE candidate lists (macro, earnings) are ranked independently and
NEVER cross-scored — summing a many-ticker macro event's overlap against a
one-ticker earnings event's weight would always favor the macro side, and
__ALL__-category macro events (Fed Policy/FOMC, Growth/GDP) would trivially
win by construction since they threaten every sector equally, saying nothing
about which SPECIFIC weak point is exposed. Both lists degrade to empty
gracefully — "nothing catalyst-specific stands out" is a legitimate, expected
result, not an error.

Full design in docs/plans/catalyst-specific-stress.md.

Design principles (mirrors regime_stress.py):
- No Streamlit imports — pure logic only.
- api_key passed in; never read from st.secrets.
- Every LLM-calling body wrapped in bare except Exception so a rate-limit or
  outage degrades gracefully (returns None, never raises).
- Never invents a threat mechanism, or a "contribution" number, not present
  in the supplied evidence.
"""

import json
from datetime import date

from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC
from stock_analyzer.macro_calendar import is_all_sector_category

_NARRATIVE_SYSTEM = """You are a portfolio structural-risk analyst. You are given up to two dated catalysts (a macro event and/or a held position's earnings report) and the portfolio's structural weak-point evidence (correlated clusters, cascade-shock estimates). For EACH candidate that is supplied, write 1-2 sentences naming WHY that specific dated event threatens the SPECIFIC weak points it overlaps with, using ONLY that candidate's own supplied fields (its own date, tickers, weight, and blast-radius impact) — cite only the tickers, clusters, and numbers supplied below. Never invent a threat mechanism, a number, or a real-world catalyst not present in the evidence, and never merge two distinct candidates into one fabricated compound event. If both candidates are supplied, treat them as if writing two separate paragraphs that never reference each other: no shared theme, no ordering language ("three days later"), no "if X then Y", no claim that one event's outcome would signal, confirm, or amplify risk for the other, and no combined figure spanning both. Any relationship between the two candidates is not in the evidence, full stop. If neither candidate has genuine structural overlap with the evidence, say so plainly instead of manufacturing a concern. Output ONLY valid JSON: {"narrative": "1-4 sentences covering whichever candidate(s) were supplied, or a plain 'nothing stands out' statement"}"""


# ── Weak-point identification (pure Python) ──────────────────────────────────

def _weak_point_tickers(blast_radius_results, clusters) -> set:
    """Union of blast-radius shocked tickers, their cascade contributors, and
    every correlation-cluster member — the structural weak-point ticker set
    both candidate lists are ranked against. Never raises; degrades to an
    empty set on malformed input."""
    tickers = set()
    try:
        for b in (blast_radius_results or []):
            shocked = b.get("shocked_ticker")
            if shocked:
                tickers.add(str(shocked).upper())
            for c in (b.get("contributing_tickers") or []):
                t = c.get("ticker")
                if t:
                    tickers.add(str(t).upper())
        for cl in (clusters or []):
            for t in (cl.get("tickers") or []):
                tickers.add(str(t).upper())
    except Exception:
        return set()
    return tickers


# ── Candidate ranking (pure Python, two separate lists) ──────────────────────

def rank_catalyst_threats(
    macro_events: list,
    earnings_events: list,
    blast_radius_results: list,
    clusters: list,
    port_df,
    window_days: int,
    today: "date | None" = None,
) -> dict:
    """
    Ranks upcoming dated catalysts by overlap with the portfolio's structural
    weak points. Returns {"macro": [...], "earnings": [...]}, each sorted by
    score descending — either or both may be empty, a legitimate "nothing
    catalyst-specific stands out" result. Never raises.

    macro candidates: HIGH-impact events from macro_calendar.build_macro_calendar()
    within window_days, EXCLUDING __ALL__-category events (they threaten every
    sector equally and can't discriminate a specific weak point). Scored by the
    combined portfolio weight% of affected_tickers that are also weak points.

    earnings candidates: held tickers with an upcoming earnings date (soonest
    date only, per ticker) within window_days, where that ticker IS a weak
    point. Scored by that ticker's own weight% plus — if it appears as a
    shocked_ticker in blast_radius_results — that entry's own
    portfolio_impact_pct (the only two real, already-computed numbers used;
    no other "contribution" figure is invented).
    """
    empty = {"macro": [], "earnings": []}
    try:
        today = today or date.today()
        weak_points = _weak_point_tickers(blast_radius_results, clusters)
        if not weak_points:
            return empty

        weights = {}
        if (
            port_df is not None
            and hasattr(port_df, "empty")
            and not port_df.empty
            and "Ticker" in port_df.columns
            and "Weight (%)" in port_df.columns
        ):
            for _, row in port_df.iterrows():
                try:
                    weights[str(row["Ticker"]).upper()] = float(row["Weight (%)"])
                except (TypeError, ValueError):
                    continue

        # ── Macro candidates ─────────────────────────────────────────────
        macro_candidates = []
        for ev in (macro_events or []):
            try:
                if ev.get("impact") != "HIGH":
                    continue
                category = ev.get("category")
                if is_all_sector_category(category):
                    continue
                ev_date = ev.get("date")
                if not isinstance(ev_date, date):
                    continue
                days_out = (ev_date - today).days
                if days_out < 0 or days_out > window_days:
                    continue
                affected = {str(t).upper() for t in (ev.get("affected_tickers") or [])}
                overlap = sorted(affected & weak_points)
                if not overlap:
                    continue
                score = sum(weights.get(t, 0.0) for t in overlap)
                macro_candidates.append({
                    "event":           ev.get("event"),
                    "date":            ev_date.isoformat(),
                    "days_out":        days_out,
                    "category":        category,
                    "overlap_tickers": overlap,
                    "score":           round(score, 2),
                })
            except Exception:
                continue
        macro_candidates.sort(key=lambda c: c["score"], reverse=True)

        # ── Earnings candidates — soonest date per ticker ───────────────
        soonest_by_ticker: dict = {}
        for ev in (earnings_events or []):
            try:
                t = str(ev.get("ticker", "")).upper()
                raw_d = ev.get("date")
                if not t or not raw_d:
                    continue
                d = date.fromisoformat(str(raw_d)[:10])
                if t not in soonest_by_ticker or d < soonest_by_ticker[t]:
                    soonest_by_ticker[t] = d
            except Exception:
                continue

        shocked_impact = {}
        for b in (blast_radius_results or []):
            st = b.get("shocked_ticker")
            if st:
                shocked_impact[str(st).upper()] = b.get("portfolio_impact_pct", 0.0) or 0.0

        earnings_candidates = []
        for t, d in soonest_by_ticker.items():
            try:
                if t not in weak_points:
                    continue
                days_out = (d - today).days
                if days_out < 0 or days_out > window_days:
                    continue
                # abs() deliberately: portfolio_impact_pct is signed (a hedge
                # can be negative), but this is a MAGNITUDE-of-exposure score,
                # not a directional one — a large negative impact is still a
                # large exposure and must not shrink/zero-out a real weak point.
                score = weights.get(t, 0.0) + abs(shocked_impact.get(t, 0.0))
                if score <= 0:
                    continue
                earnings_candidates.append({
                    "ticker":   t,
                    "date":     d.isoformat(),
                    "days_out": days_out,
                    "score":    round(score, 2),
                })
            except Exception:
                continue
        earnings_candidates.sort(key=lambda c: c["score"], reverse=True)

        return {"macro": macro_candidates, "earnings": earnings_candidates}
    except Exception:
        return empty


# ── Evidence assembly ─────────────────────────────────────────────────────

def build_catalyst_stress_inputs(ranked: dict, blast_radius_results: list, clusters: list) -> dict:
    """Assembles the evidence dict for the Haiku prompt. Never raises —
    degrades gracefully, omitting any section that's empty/None.

    Returns dict with keys:
      "top_macro": ranked["macro"][0] or None
      "top_earnings": ranked["earnings"][0] or None
      "blast_radius": blast_radius_results (list, possibly empty)
      "clusters": clusters (list, possibly empty)
    """
    try:
        ranked = ranked or {"macro": [], "earnings": []}
        return {
            "top_macro":    (ranked.get("macro") or [None])[0],
            "top_earnings": (ranked.get("earnings") or [None])[0],
            "blast_radius": list(blast_radius_results or []),
            "clusters":     list(clusters or []),
        }
    except Exception:
        return {"top_macro": None, "top_earnings": None, "blast_radius": [], "clusters": []}


def _format_evidence(evidence: dict) -> str:
    """Render the evidence dict into a readable text block. Never raises."""
    lines = []
    try:
        top_macro = evidence.get("top_macro")
        if top_macro:
            lines.append(
                f"Macro candidate: {top_macro.get('event')} on {top_macro.get('date')} "
                f"(in {top_macro.get('days_out')}d, category={top_macro.get('category')}) — "
                f"overlaps weak-point tickers {', '.join(top_macro.get('overlap_tickers') or [])} "
                f"(combined weight {top_macro.get('score')}%)"
            )
        else:
            lines.append("Macro candidate: none within the window with weak-point overlap.")

        top_earnings = evidence.get("top_earnings")
        if top_earnings:
            lines.append(
                f"Earnings candidate: {top_earnings.get('ticker')} reports on "
                f"{top_earnings.get('date')} (in {top_earnings.get('days_out')}d) — "
                f"this ticker IS one of the structural weak points (score {top_earnings.get('score')}%)"
            )
        else:
            lines.append("Earnings candidate: none within the window with weak-point overlap.")

        blast = evidence.get("blast_radius") or []
        if blast:
            lines.append("Blast radius (single-position shock cascade):")
            for b in blast:
                lines.append(
                    f"  - Shocking {b.get('shocked_ticker')} by "
                    f"{b.get('shock_pct')}% → estimated portfolio impact "
                    f"{(b.get('portfolio_impact_pct') or 0.0):+.2f}%"
                )

        clusters = evidence.get("clusters") or []
        if clusters:
            lines.append("Correlation clusters:")
            for c in clusters:
                names = ", ".join(c.get("tickers") or [])
                lines.append(
                    f"  - {c.get('size')} positions ({names}): avg internal "
                    f"correlation {round(c.get('avg_internal_corr', 0), 2)}, "
                    f"combined weight {round(c.get('combined_weight_pct', 0), 1)}%"
                )

        return "\n".join(lines)
    except Exception:
        return ""


# ── Narrative generation ──────────────────────────────────────────────────

def generate_catalyst_narrative(evidence: dict, api_key: str, model: str = "claude-haiku-4-5-20251001") -> dict | None:
    """
    Single Haiku call. Returns {"narrative": str} or None on any failure (no
    key, timeout, malformed response, empty narrative). Never raises.

    If neither top_macro nor top_earnings is present, does not call the LLM
    at all — there is nothing to narrate, and "nothing stands out" is decided
    by the ranking step, not asserted by an LLM call with no evidence.
    """
    if not api_key:
        return None
    if not evidence.get("top_macro") and not evidence.get("top_earnings"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        evidence_text = _format_evidence(evidence)
        response = client.messages.create(
            model=model,
            max_tokens=350,
            temperature=0.3,
            system=_NARRATIVE_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Evidence:\n{evidence_text}\n\nWrite the narrative JSON now.",
            }],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        if not response.content:
            return None
        raw_text = response.content[0].text.strip()
        return _parse_catalyst_narrative_response(raw_text)
    except Exception:
        return None


def _parse_catalyst_narrative_response(raw_json: str) -> dict | None:
    """Parse the Haiku JSON response. Returns None on any failure. Never
    raises."""
    if not raw_json:
        return None
    try:
        cleaned = raw_json.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[: cleaned.rfind("```")]
            cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
        except Exception:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            try:
                parsed = json.loads(cleaned[start: end + 1])
            except Exception:
                return None

        if not isinstance(parsed, dict):
            return None

        narrative = parsed.get("narrative")
        if not isinstance(narrative, str) or not narrative.strip():
            return None

        return {"narrative": narrative.strip()}
    except Exception:
        return None
