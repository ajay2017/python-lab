"""
Regime-Aware Adversarial Stress Testing — Phase 1.

Composes three already-shipped systems into one Haiku-narrated compound
scenario — zero new quantitative modeling:
  - structural_scanner.py's blast_radius() cascade-shock estimate (reused
    verbatim as the "estimated damage" figure, no new stress math here).
  - portfolio_intelligence.py's correlation_clusters() weak-point evidence.
  - macro_calendar.py's FRED-based detect_macro_regime() (imported in app.py
    as detect_macro_regime_fred) — NOT macro.py's older ETF-return-based
    detector, which is out of scope for this feature.
  - cross_asset.py's compute_cross_asset_signals() USD/credit/curve stress
    signals (wrapped by app.py's _cached_cross_asset()).

Full design in docs/plans/regime-aware-stress-testing.md.

Design principles (mirrors structural_scanner.py / debate_agent.py):
- No Streamlit imports — pure logic only.
- api_key passed in; never read from st.secrets.
- Every LLM-calling body wrapped in bare except Exception so a rate-limit or
  outage degrades gracefully (returns None, never raises).
- The confidence score and indicator watchlist are always real, already-
  computed data — never a fabricated forecast probability. indicator_watchlist
  entries are matched against the supplied regime signals via a normalized
  (.strip().casefold()) comparison, and the CANONICAL label from the signals
  tuple is returned — never the LLM's own echoed text — so a casing/spacing
  difference can never fabricate a fake indicator or silently drop a
  legitimately-selected real one.
"""

import json

from stock_analyzer.constants import LLM_REQUEST_TIMEOUT_SEC
from stock_analyzer.util import factor_tilt_evidence_line

_SCENARIO_SYSTEM = """You are a portfolio structural-macro analyst. Given the portfolio's structural weak points (correlated clusters, cascade-shock estimates) and the current macro regime evidence below, name the SINGLE compound macro scenario — combining 2-3 concurrent macro conditions (e.g. rate direction, dollar strength, credit stress) — that would do the most damage to the SPECIFIC weak points identified. Cite the specific tickers, clusters, and regime readings supplied — never invent a macro condition not evidenced below. A "Factor tilt:" line is ALWAYS present and says one of three things: a measured tilt, that it was measured but unusable, or that it was NOT MEASURED. Use a measured tilt only where it reinforces the structural evidence. If it says NOT MEASURED or unknown, do not reason about factor exposure in any direction, and never treat its absence as evidence of low or balanced factor concentration. Then select 2-3 indicators from the supplied regime signals list that would be the earliest sign this scenario is developing — select ONLY from the supplied list, never invent a new indicator name. If the evidence doesn't support a coherent compound scenario (e.g. regime is neutral and no structural weak point stands out), say so plainly instead of manufacturing one. Output ONLY valid JSON: {"scenario_narrative": "2-4 sentences", "indicator_watchlist": ["<exact signal label>", ...]}"""


# ── Evidence assembly ─────────────────────────────────────────────────────

def build_regime_scenario_inputs(blast_radius_results, clusters, regime_data, cross_asset_data, factor_tilt=None):
    """
    Assembles the evidence dict for the Haiku prompt. Never raises — degrades
    gracefully, omitting any section that's empty/None.

    Returns dict with keys:
      "blast_radius": blast_radius_results (list, possibly empty)
      "clusters": clusters (list, possibly empty)
      "regime": {"label", "fed_trend", "cpi_yoy", "confidence", "signals"}
                extracted from regime_data
      "cross_asset": {"label", "score"} extracted from cross_asset_data, plus
                any per-signal detail dicts present (e.g. "dollar" key)
      "factor_tilt": factor_tilt if truthy else None

    NOTE: a None here does NOT mean the prompt omits factor exposure.
    _format_evidence emits a "Factor tilt:" line UNCONDITIONALLY via
    util.factor_tilt_evidence_line, stating explicitly that the data was
    not measured. Silently omitting it was the F-260 defect.
    """
    try:
        regime_data = regime_data or {}
        cross_asset_data = cross_asset_data or {}

        regime_evidence = {
            "label":      regime_data.get("label"),
            "fed_trend":  regime_data.get("fed_trend"),
            "cpi_yoy":    regime_data.get("cpi_yoy"),
            "confidence": regime_data.get("confidence", 0),
            "signals":    list(regime_data.get("signals") or []),
        }

        cross_asset_evidence = {
            "label": cross_asset_data.get("label"),
            "score": cross_asset_data.get("score"),
        }
        for _key, _val in cross_asset_data.items():
            if _key in ("label", "score", "summary"):
                continue
            if isinstance(_val, dict) and _val.get("available"):
                cross_asset_evidence[_key] = _val

        return {
            "blast_radius": list(blast_radius_results or []),
            "clusters":     list(clusters or []),
            "regime":       regime_evidence,
            "cross_asset":  cross_asset_evidence,
            "factor_tilt":  factor_tilt if factor_tilt else None,
        }
    except Exception:
        return {
            "blast_radius": [], "clusters": [], "regime": {},
            "cross_asset": {}, "factor_tilt": None,
        }


def _format_evidence(evidence: dict) -> str:
    """Render the evidence dict into a readable text block. Never raises."""
    lines = []
    try:
        blast = evidence.get("blast_radius") or []
        if blast:
            lines.append("Blast radius (single-position shock cascade):")
            for b in blast:
                lines.append(
                    f"  - Shocking {b.get('shocked_ticker')} by "
                    f"{b.get('shock_pct')}% → estimated portfolio impact "
                    f"{b.get('portfolio_impact_pct'):+.2f}%"
                )
                for c in (b.get("contributing_tickers") or [])[:3]:
                    lines.append(
                        f"      cascades via {c.get('ticker')} "
                        f"(corr {c.get('corr'):+.2f}, comove {c.get('comove_pct'):+.2f}%)"
                    )
        else:
            lines.append("Blast radius: none computed.")

        clusters = evidence.get("clusters") or []
        if clusters:
            lines.append("Correlation clusters:")
            for c in clusters:
                names = ", ".join(c.get("tickers") or [])
                lines.append(
                    f"  - {c.get('size')} positions ({names}): avg internal "
                    f"correlation {round(c.get('avg_internal_corr', 0), 2)}, "
                    f"combined weight {round(c.get('combined_weight_pct', 0), 1)}%, "
                    f"tier={c.get('tier')}"
                )
        else:
            lines.append("Correlation clusters: none detected.")

        regime = evidence.get("regime") or {}
        if regime:
            lines.append(
                f"Macro regime: {regime.get('label')} "
                f"(fed trend: {regime.get('fed_trend')}, "
                f"CPI YoY: {regime.get('cpi_yoy')}, "
                f"confidence: {regime.get('confidence', 0)}/100)"
            )
            signals = regime.get("signals") or []
            if signals:
                lines.append("Regime signals:")
                for s in signals:
                    try:
                        label, reading = s[0], s[1]
                        lines.append(f"  - {label}: {reading}")
                    except Exception:
                        continue

        cross_asset = evidence.get("cross_asset") or {}
        if cross_asset.get("label"):
            lines.append(
                f"Cross-asset stress: {cross_asset.get('label')} "
                f"(score {cross_asset.get('score')})"
            )
            for k, v in cross_asset.items():
                if k in ("label", "score") or not isinstance(v, dict):
                    continue
                if v.get("label"):
                    lines.append(f"  - {v.get('label')} ({v.get('detail', '')})")

        # Unconditional by design — see util.factor_tilt_evidence_line. This
        # used to be `if factor:` + `if valid:`, which silently emitted NOTHING
        # both when factor data was never loaded and when it was loaded but
        # unusable, so the model could not distinguish either from "measured,
        # nothing notable" and wrote as if its evidence were complete (F-260).
        lines.append(factor_tilt_evidence_line(evidence.get("factor_tilt")))

        return "\n".join(lines)
    except Exception:
        return ""


# ── Scenario generation ───────────────────────────────────────────────────

def generate_regime_scenario(evidence, api_key, model="claude-haiku-4-5-20251001"):
    """
    Single Haiku call. Returns {"scenario_narrative": str,
    "indicator_watchlist": list[str]} or None on any failure (no key,
    timeout, malformed response, empty narrative).

    indicator_watchlist entries are matched against evidence["regime"]
    ["signals"] labels via a normalized (.strip().casefold()) comparison,
    and the CANONICAL label from the signals tuple is returned — never the
    LLM's own echoed text. Entries with no normalized match are dropped (not
    causing the whole response to fail). If scenario_narrative is missing/
    empty after parsing, the whole call is treated as failed (returns None).

    Never raises.
    """
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        evidence_text = _format_evidence(evidence)
        response = client.messages.create(
            model=model,
            max_tokens=400,
            temperature=0.3,
            system=_SCENARIO_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Evidence:\n{evidence_text}\n\nWrite the scenario JSON now.",
            }],
            timeout=LLM_REQUEST_TIMEOUT_SEC,
        )
        if not response.content:
            return None
        raw_text = response.content[0].text.strip()
        return _parse_regime_scenario_response(raw_text, evidence)
    except Exception:
        return None


def _parse_regime_scenario_response(raw_json: str, evidence: dict) -> dict | None:
    """Parse the Haiku JSON response, validate, and canonicalize indicator
    labels against the supplied regime signals. Returns None on any failure.
    Never raises."""
    if not raw_json:
        return None
    try:
        cleaned = raw_json.strip()
        # Strip markdown fences (same pattern as debate_agent._parse_judge).
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

        narrative = parsed.get("scenario_narrative")
        if not isinstance(narrative, str) or not narrative.strip():
            return None

        raw_watchlist = parsed.get("indicator_watchlist") or []
        if not isinstance(raw_watchlist, list):
            raw_watchlist = []

        signals = (evidence.get("regime") or {}).get("signals") or []
        canonical_by_norm = {}
        for s in signals:
            try:
                label = s[0]
            except Exception:
                continue
            norm = str(label).strip().casefold()
            canonical_by_norm[norm] = label

        watchlist = []
        for entry in raw_watchlist:
            norm_entry = str(entry).strip().casefold()
            canonical = canonical_by_norm.get(norm_entry)
            if canonical is not None:
                watchlist.append(canonical)

        return {
            "scenario_narrative": narrative.strip(),
            "indicator_watchlist": watchlist,
        }
    except Exception:
        return None
