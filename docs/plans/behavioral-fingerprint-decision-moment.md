# Behavioral Fingerprint → the live decision moment

**Date:** 2026-08-06
**Design pass:** `planner` (Opus 4.8, 1M context) — verdict **PROCEED**, scope reduced from 6 patterns to 1-per-side.
**Status:** DESIGN LOCKED (user confirmed both open decisions 2026-08-06) — mock pending approval, no code written yet.

Picked up from `docs/plans/next-evolution-2026-08-05.md` Lens 2, item 1 — closing the gap where the app diagnoses the user's own historical decision patterns (🧬 Behavioral Fingerprint tab, `stock_analyzer/behavioral_fingerprint.py`) but only shows them retrospectively, never *at the instant* a matching trade is being logged.

## Governing invariant (from Pass #1, still binding)

Bias-aware framing may amplify salience but must **NEVER** re-order recommendations, change the composite score, or gate a trade. This is a mirror, not a nudge with teeth — and it must never block or slow the trade-write submission itself.

## v1 scope (locked)

One quiet line per trade side. Not a second dashboard.

| Pattern | In scope? | Why |
|---|---|---|
| **Momentum-recency** (BUY) | ✅ v1 | Ticker's momentum `Score` is reliably resolvable from `_last_port_df`/`scanner_results` on the Log Trade page with zero new fetches. |
| **Signal-response-rate + lag** (SELL) | ✅ v1 | If the ticker has a current exit signal (WATCH/TRIM/EXIT/RISK_OFF) within `EXIT_SIGNAL_ACT_WINDOW_DAYS`=7, mirror that type's historical action-rate + median lag. **User confirmed:** reuse `EXIT_SIGNAL_ACT_WINDOW_DAYS`, no new constant. |
| Conviction-tier (BUY) | ⏸ Deferred | Composite score isn't reliably in `session_state` on this page (it's a Home/Brief-build artifact) — would render nothing almost always. Revisit if a per-ticker composite map ever gets published to session. |
| Opening-window (BUY) | ❌ Rejected | It's an outcome/alpha stat, not an action-rate mirror — surfacing predicted alpha at the entry instant nudges timing, crossing the mirror-not-nudge invariant and the calm-advisor posture. |
| Escalation-ignored (SELL) | ❌ Rejected | No coherent single-trade bucket — a SELL is the opposite of "ignored," not a classification of one. |

**User confirmed both points 2026-08-06:** reuse `EXIT_SIGNAL_ACT_WINDOW_DAYS=7` for the SELL "is this signal still active" window (no new constant); lock the one-per-side scope as-is (conviction-tier NOT pulled into v1).

## Where it renders

📒 Trade Journal → 📝 Log Trade tab, in `app.py`'s pre-form region — immediately after the "📋 Decision Context" expander closes, before `st.form("log_trade_form", ...)`. `_live_ticker`/`_live_action` are already resolved above this point. Structurally outside the form, so no submit/validation path can ever read its output. Whole block wrapped in one `try/except: pass` (matches the existing lesson-category chip precedent) — a classification failure degrades to silence, never a crash, never a placeholder.

**Degrade-to-nothing conditions** (render nothing, no "insufficient data" text — silence is correct in the live flow, unlike the retrospective tab which explains withholds):
- BUY: ticker's momentum score unresolvable (off-universe/new name), or `momentum_recency_pattern` below `BEHAVIORAL_MIN_SAMPLE_N`=8 per bucket.
- SELL: no exit-signal row for the ticker within the 7-day window, or that signal_type's sample below `BEHAVIORAL_MIN_SAMPLE_N`.

## Copy (reuses verbatim disclaimer, never invents new framing)

- BUY: *"📊 This is a high-momentum entry (Score {s:.0f}). Historically you've acted on **{high}%** of your high-momentum signals vs **{low}%** of low-momentum ones."*
- SELL: *"⏱️ This SELL matches an active {TYPE} signal. Historically you act on {TYPE} signals **{rate}%** of the time within {N} days (typically ~{median} days)."*
- Both followed by, verbatim from the My Edge tab (`app.py:32305`/`32346`/`32411`): *"An observed correlation in your own decisions, not a verdict on it."*
- Visual: muted caption line, no border/card — a quiet aside, not a panel.

## Implementation spec

1. **`stock_analyzer/behavioral_fingerprint.py`** — additive only, existing return keys/gating unchanged:
   - `momentum_recency_pattern(...)` gains a `"median"` key in its return dict (the value it already computes internally).
   - New: `classify_live_buy_momentum(this_momentum_score, matched, min_n, meaningful_delta_pp) -> dict|None` — places the score into the SAME `>= median` bucket boundary the retrospective card uses; returns that bucket's + the other bucket's action_rate, or `None`.
   - New: `latest_active_signal_type(exit_signals_df, ticker, as_of_date, act_window_days) -> str|None`.
   - New: `classify_live_sell_signal(signal_type, exit_signals_df, trades_df, act_window_days, min_n) -> dict|None`.
2. **`app.py`** — render block in the pre-form region (~after the Decision Context expander, before `log_trade_form`). Cached loads via `@st.cache_data(ttl=300)` (matches `_tj_market_price` precedent at ~20787) so per-keystroke reruns stay cheap. Dynamic ticker/signal_type interpolation goes through `stock_analyzer.util.safe_html` per the antipattern gate.
3. **Tests** in `tests/test_behavioral_fingerprint.py`:
   - Median-boundary drift guard: a score exactly at the historical median lands in the `high` bucket (matches `>= median`), and its rate equals the retrospective card's high-bucket rate.
   - `classify_live_buy_momentum` → `None` on unresolvable score or sub-`min_n` pattern.
   - `momentum_recency_pattern` still returns `None` below `min_n`; existing tests stay green.
   - `latest_active_signal_type` exact-boundary test (signal exactly `act_window_days` old → active; one day older → not — the same off-by-one class as the 2026-08-04 Critical fix).
   - `classify_live_sell_signal` → `None` on falsy signal_type or sub-`min_n`.
   - Never-raise: all four helpers return `None`, never raise, on malformed/empty input.
4. **No new `constants.py` value.** Reuses `BEHAVIORAL_MIN_SAMPLE_N`, `BEHAVIORAL_MEANINGFUL_ACTION_RATE_DELTA_PP`, `EXIT_SIGNAL_ACT_WINDOW_DAYS`.
5. **Docs sync same session:** F-ID in `docs/requirements.md`, in-app User Guide note, `docs/shipped-log.md` entry, update memory `project_behavioral_fingerprint.md`.

## Review requirement

**Opus `reviewer` pass REQUIRED before commit** — new user-facing decision-adjacent surface + cross-feature coordination sitting directly on the trade-write path (CLAUDE.md hard rule #4), even though display-only and never gating. Note: neither `behavioral_fingerprint.py` nor `app.py` is in the commit-hook's mechanically-enforced file list, so the hook will not force this citation — it's on the author by policy. Reviewer must also confirm no literal duplication with any existing reduce-call/exit banner already in the log-trade pre-form region (none found in the design pass, but re-check), and that the copy doesn't editorialize past "observation, not verdict."

## Risks flagged by the design pass

- **Boundary-coherence drift**: the live classifier must use the identical `>= median` rule as the retrospective card, or the two surfaces will silently disagree — covered by the median-boundary test above.
- **Performance**: un-memoized DB reads on every keystroke would slow the form — mitigated via `@st.cache_data`.
- **Mirror vs nudge**: BUY momentum framing could read as validating momentum-chasing — mitigated structurally (engine never reads these outputs) and via neutral, both-buckets-reported copy with the verbatim disclaimer.
