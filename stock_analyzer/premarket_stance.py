"""
Pre-Market Stance — AI-generated narrative + actionable verdict for the open.

Distills futures, global markets, today's macro events, current regime, and
top portfolio names into a 4-6 sentence narrative ending with an explicit
stance verdict (Defensive / Neutral / Constructive at open).

Designed to be cached per trading day and refreshed on demand. Returns
None on API failure so the caller can hide the card gracefully without
breaking the rest of Today's Brief.

The module is provider-agnostic at the input/parser layer; only
generate_stance() binds to Anthropic. Other providers can be wired by
adding a sibling function with the same return contract.
"""


_SYSTEM_PROMPT = """You are a senior portfolio manager writing a brief pre-market stance note for a single-user portfolio intelligence app.

Your job: distill the pre-market data into a 4-6 sentence stance note ending with ONE explicit verdict line.

Rules:
- Open with the dominant tone (futures, overnight tape, key catalyst).
- Name SPECIFIC portfolio positions when relevant to today's setup — don't talk in abstractions when concrete names are in the data.
- Flag the one macro event or catalyst that matters most today.
- Distinguish between "what happened overnight" and "what to do at the open."
- End with a verdict line on its own — EXACTLY one of:
    Stance: Defensive at open
    Stance: Neutral at open
    Stance: Constructive at open
- Be specific. No hedging fluff. No bullet points. Prose only. Plain language a busy retail trader can act on in 30 seconds.
- Do not add disclaimers ("not investment advice", "do your own research"). The app already discloses this elsewhere."""


def assemble_inputs(
    premarket_brief: dict | None,
    regime: dict | None,
    port_df=None,
    news_headlines: list | None = None,
) -> dict:
    """
    Assemble structured inputs for the prompt. Returns a dict consumed by
    format_user_prompt() — kept separate from the prompt format so callers
    can inspect what was sent without re-running the LLM.
    """
    inputs: dict = {
        "futures":          [],
        "global_markets":   [],
        "movers":           [],
        "events":           [],
        "regime_label":     "—",
        "regime_rationale": "",
        "top_holdings":     [],
        "news_headlines":   [],
    }
    if premarket_brief:
        inputs["futures"]        = (premarket_brief.get("futures")        or [])[:4]
        inputs["global_markets"] = (premarket_brief.get("global_markets") or [])[:5]
        inputs["movers"]         = (premarket_brief.get("movers")         or [])[:6]
        inputs["events"]         = (premarket_brief.get("events")         or [])[:3]
    if regime:
        inputs["regime_label"]     = str(regime.get("label", "—"))
        inputs["regime_rationale"] = str(regime.get("rationale", ""))
    if port_df is not None and not port_df.empty and "Weight (%)" in port_df.columns:
        sub = port_df.sort_values("Weight (%)", ascending=False).head(5)
        for _, r in sub.iterrows():
            inputs["top_holdings"].append({
                "ticker": str(r.get("Ticker", "")),
                "weight": float(r.get("Weight (%)", 0) or 0),
                "sector": str(r.get("Sector", "") or ""),
                "signal": str(r.get("Signal", "") or ""),
            })
    if news_headlines:
        inputs["news_headlines"] = [str(h)[:140] for h in news_headlines[:5]]
    return inputs


def format_user_prompt(inputs: dict) -> str:
    """Format the assembled inputs into a structured user message."""
    parts: list[str] = []

    if inputs.get("futures"):
        parts.append(
            "US futures: " + " · ".join(
                f"{f.get('name','?')} {f.get('chg_pct', 0):+.2f}%"
                for f in inputs["futures"]
            )
        )
    if inputs.get("global_markets"):
        parts.append(
            "Global overnight: " + " · ".join(
                f"{g.get('name','?')} {g.get('chg_pct', 0):+.2f}%"
                for g in inputs["global_markets"]
            )
        )
    if inputs.get("events"):
        parts.append(
            "Today's macro events: " + " · ".join(
                f"{e.get('event','?')} ({e.get('time','?')}, impact: {e.get('impact','?')})"
                for e in inputs["events"]
            )
        )
    parts.append(f"Current macro regime: {inputs.get('regime_label', '—')}")
    if inputs.get("regime_rationale"):
        parts.append(f"Regime rationale: {inputs['regime_rationale']}")
    if inputs.get("top_holdings"):
        parts.append(
            "Top portfolio holdings (weight, sector, current signal): " + " · ".join(
                f"{h['ticker']} {h['weight']:.1f}% ({h['sector']}, signal: {h['signal'] or 'n/a'})"
                for h in inputs["top_holdings"]
            )
        )
    if inputs.get("movers"):
        parts.append(
            "Pre-market movers in portfolio + watchlist: " + " · ".join(
                f"{m.get('ticker','?')} {m.get('chg_pct', 0):+.2f}%"
                for m in inputs["movers"]
            )
        )
    if inputs.get("news_headlines"):
        parts.append("Overnight headlines:\n- " + "\n- ".join(inputs["news_headlines"]))

    parts.append(
        "Write the 4-6 sentence stance note now, ending with the verdict line on its own."
    )
    return "\n\n".join(parts)


def parse_stance(text: str) -> dict:
    """
    Split LLM output into narrative + stance verdict.

    Expects the last non-empty line to contain 'Stance:' plus one of
    Defensive / Neutral / Constructive. Falls back to Neutral when the
    verdict line is missing or malformed.
    """
    if not text or not text.strip():
        return {
            "narrative":    "",
            "stance":       "neutral",
            "stance_label": "Neutral at open",
        }

    lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
    stance       = "neutral"
    stance_label = "Neutral at open"
    narrative_lines = lines[:]

    for ln in reversed(lines):
        if "Stance:" in ln or "stance:" in ln:
            ln_lower = ln.lower()
            if "defensive" in ln_lower:
                stance       = "defensive"
                stance_label = "Defensive at open"
            elif "constructive" in ln_lower:
                stance       = "constructive"
                stance_label = "Constructive at open"
            else:
                stance       = "neutral"
                stance_label = "Neutral at open"
            narrative_lines = [
                _l for _l in lines
                if "Stance:" not in _l and "stance:" not in _l
            ]
            break

    return {
        "narrative":    " ".join(narrative_lines).strip(),
        "stance":       stance,
        "stance_label": stance_label,
    }


def generate_stance(
    inputs: dict,
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 500,
) -> dict | None:
    """
    Call Anthropic with the assembled inputs. Returns a parsed stance
    dict on success, or None on any failure so the caller can hide the
    card without disrupting the rest of Today's Brief.

    Returns keys:
        narrative    — joined prose (verdict line stripped)
        stance       — 'defensive' | 'neutral' | 'constructive'
        stance_label — display string
        raw          — original LLM text (for debug)
        model        — model used
    """
    if not api_key or not inputs:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        user_prompt = format_user_prompt(inputs)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text if response.content else ""
        parsed = parse_stance(text)
        parsed["raw"]   = text
        parsed["model"] = model
        return parsed
    except Exception:
        return None
