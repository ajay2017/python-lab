# RiskListwiseYoung C4 — RiskOffUnchecked spec (lead, 2026-11-25)

Parent: `docs/plans/risk-young-holding.md` §8 item 4 and §9; bug register RiskOffUnchecked. C2 (`a72d6515`) and C3 shipped the young-holding window. This chunk makes the one silent fail-open it leaves visible.

## Problem

`exit_advisor.assess_risk_off_derisk` returns `[]` when `fragility` is None (`(fragility or {}).get("severity")` is not caution / fragile). Fragility is None whenever portfolio beta couldn't be computed: a young holding above `PORTFOLIO_RISK_UNMEASURED_MAX_PCT` (new since C2), SPY missing, too few joined days, or an exception. So on a risk-off day (SPY below its `RISK_OFF_TREND_MA`-day average, or VIX ≥ `RISK_OFF_VIX_LEVEL`) with beta unmeasured, the protective de-risk trim is never considered and nothing says so: the Brief shows no card and the pre-market email is not sent at all if there is no other alert.

## Rule (no new constant, no threshold change)

The de-risk check is **unchecked** when `fragility` is not a dict (None) AND `exit_advisor.risk_off_regime(spy_trend_df, vix_level, trend_ma=RISK_OFF_TREND_MA, vix_threshold=RISK_OFF_VIX_LEVEL)` is armed. If the regime is not armed the check could not have fired anyway: nothing to say. If fragility is a dict (checked, any severity), nothing to say. Never changes any card, size or gate; disclosure only.

## A. New pure module `stock_analyzer/risk_off_check.py` (add to `_GATE_FILES` in `.claude/hooks/pre_tool_checks.py` + its one-line reason in `docs/review-policy.md`)

- `derisk_unchecked(fragility, spy_trend_df, vix_level, *, coverage=None, trend_ma=RISK_OFF_TREND_MA, vix_threshold=RISK_OFF_VIX_LEVEL) -> dict | None`
  - None when `fragility` is a dict, or when the regime is not armed.
  - Else `{"reasons": [...from risk_off_regime...], "text": <plain text>}`.
  - Text (plain, no `$`, no `**`, no markdown): `"Risk-off de-risk check couldn't run: the market is in a risk-off regime (" + "; ".join(reasons) + "), but your portfolio beta couldn't be measured, so no de-risk trim was considered today. Review your highest-beta holdings yourself before adding risk."` If `risk_window.display_note(coverage)` is not None, append `" " + that note`.
  - Never raises. If `fragility` is a dict → None. Otherwise call the regime; if that call raises, return `{"reasons": [], "text": "Risk-off de-risk check couldn't run: your portfolio beta couldn't be measured, and the market regime couldn't be read either. Review your highest-beta holdings yourself before adding risk."}` (fail visible: we can't show the check was moot).
- Unit tests: fragility dict (any severity) → None; fragility None + regime armed by trend leg → dict with that reason; by VIX leg; both legs; regime not armed → None; regime inputs missing (spy None, vix None) → None (risk_off_regime degrades to not tripped: correct, nothing fired); coverage partial / unmeasured appends the note; full coverage appends nothing; risk_off_regime monkeypatched to raise → generic dict. Clock-free.

## B. Brief (`stock_analyzer/daily_briefing.py`, gate file)

- `build_daily_briefing(..., risk_coverage: dict | None = None)` new optional keyword at the end.
- Right after the `exit_advisor.assess_risk_off_derisk(...)` call (~4279), compute `_ro_unchecked = risk_off_check.derisk_unchecked(fragility, spy_trend_df, vix_level, coverage=risk_coverage)`; add `"risk_off_unchecked": _ro_unchecked` to the returned dict beside `"portfolio_tuneup_deferred"` (~4372). `derisk_unchecked` never raises, so no extra try/except.

## C. App (`app.py`)

- Where `build_daily_briefing(` is called for 🏠 Home, pass `risk_coverage=` the coverage of the same risk build: the `coverage` key of the port_risk dict that build published (`_port_risk` / `_port_risk_cache` in that scope; `.get("coverage")` only if it is a dict, else None). Verify both the full-run and any other call site of `build_daily_briefing`.
- Bump `_SYNTH_SCHEMA_VER` 27 → 28 with comment prefix `bumped: the Brief result carries risk_off_unchecked (RiskOffUnchecked, RiskListwiseYoung C4); was 27: ...`.
- Render: in Home's Act Today section, immediately above the Act Today cards (find where `_daily_brief` act items are rendered, ~9921 neighbourhood), when `_daily_brief.get("risk_off_unchecked")` is a dict: `st.warning("⚠️ " + text)`. Text is plain, so `st.warning` markdown is safe. Must also render on a day with zero Act Today cards (the case that matters most): put it where it shows whether or not the act list is empty. No comparison against constants in app.py.

## D. Cron (gate file `stock_analyzer/headless_alert_engine.py` + `cron_runner.py`)

- `compute_protective_alerts`: after the risk-off block (~493), `risk_off_unchecked = risk_off_check.derisk_unchecked(fragility, spy_1y, vix, coverage=ctx.get("risk_coverage"))` (same inputs the de-risk call uses); add `"risk_off_unchecked": risk_off_unchecked` to its return dict.
- `cron_runner._run_premarket`: when `payload.get("risk_off_unchecked")` is a dict: `_log("RISK-OFF DE-RISK NOT CHECKED: " + text)`; append the text to `_footer_lines`; and append to `failures` `"risk_off_unchecked -- " + text` (precedent: `held_stops_failed` / `manual_stops_unreadable` — a protective check that couldn't run reddens the heartbeat and sends the failure email, so the owner hears about it even on a day with no alert email). Alerts that were computed are still sent.
- Tests: premarket lane with the payload key set → failure recorded + footer line + log; unset → none (follow `tests/test_held_stop_lanes.py`'s premarket harness).

## E. Outage wording (C3 review note, `stock_analyzer/risk_window.py`, gate file)

In `_coverage_note`'s unmeasured branch: when every `left_out` item has reason `"no_prices"`, return `"Portfolio risk can't be measured: no price history could be loaded for " + _names(left_out) + " (market data offline?)."` instead of the "more than the N% limit" sentence. Tests for that case; the existing above-cap texts for short_history unchanged.

## F. Not in this chunk

forward_sim's call (~535) is a simulation, no disclosure needed. The app 📋 Watchlist skipping G-06 on a None beta belongs to entry-fail-closed C2b. RiskBudgetYoung and AnalysisBetaEnvelopeSilent = C4b. Docs (bug register, requirements, architecture, User Guide) = lead after review.

## Checks

py_compile; the new + touched tests and tests/test_daily_brief*.py / tests/test_exit_advisor*.py / tests/test_held_stop_lanes.py / tests/test_headless*.py (`-n 4` max); `python scripts/check_antipatterns.py`; grep tests/ for any pinned `_SYNTH_SCHEMA_VER` literal (27 → 28) and pinned Brief result key sets. No full suite, no commit.

---

## C4 fix round (Opus FIX-FIRST, 2026-11-25)

1. **Home coverage (blocking 1).** `home_risk_synthesis.build_risk_bundle` (~226) calls `compute_portfolio_risk_metrics(..., coverage_out=_cov)` and returns the coverage as a new bundle key `risk_coverage` (`_cov or None`), filled even when `port_risk` is None. app.py: carry it in the Home synth bundle beside `_port_risk` on the MISS path, restore it on the HIT path (schema already 28; no further bump unless a test says otherwise), and pass `risk_coverage=` that value at BOTH `build_daily_briefing` call sites (~7603, ~8051) instead of `_port_risk.get("coverage")`. Fix the comment there. Tests: in the home_risk_synthesis tests, the bundle carries `risk_coverage` with state "unmeasured" when `port_risk` is None (young holding above the cap) and "partial" when not; a source pin that app.py passes the bundle's coverage, not one read from a dict `port_risk`.
2. **Every all-clear surface (blocking 2, option a: fix the class).**
   - 🧾 Summary (app.py ~15564-15578, reads `_sm_bundle["_daily_brief"]`): when that brief's `risk_off_unchecked` is a dict with text, replace the green "🟢 Nothing to act on today — you're clear" with an amber note: `st.warning("⚠️ No trade calls today, but this is not an all-clear: " + text)` (plain text). Only in the no-act case; with Act items, add the same `st.warning("⚠️ " + text)` line above them.
   - Dock digest (`stock_analyzer/home_dock_digest.py`, gate file, ~104-106 / ~135): `build_digest` takes a new optional `risk_off_unchecked: bool = False` (or reads it from the brief it already receives — use whatever input it already has; check its callers); when True, the line never says "✅ nothing to act on" and instead says "⚠️ de-risk check couldn't run" in that slot (keep the existing format and severity scheme of the line; amber/attention, not red). Wire the app caller. Tests at both states.
   - Grep for any other reader of the Brief's empty-Act state that prints an all-clear ("you're set", "you're clear", "nothing to act on") — app.py, `home_dock_digest.py`, `act_today_view`, `notify.py` — and list each in the report with what you did (fixed / not reachable / not an all-clear).
3. **risk_off_check (non-blocking, take it):** append the coverage note only when `coverage.get("state") == "unmeasured"` (a "partial" note beside "beta couldn't be measured" misleads). Update `test_risk_off_check.py` ~110-114 accordingly and add the partial → no note case.
4. **Cron dedupe (take it):** in `_run_premarket` add the coverage footer note unless it is already contained in the RiskOffUnchecked text (`note not in _ro_text`), instead of skipping whenever the dict exists. Add a lane test: unmeasured coverage + the dict → the coverage sentence appears exactly once in the footer; regime-unreadable dict + unmeasured coverage → both lines present.
5. **Cron log contradiction (take it):** on a run with no alerts but a RiskOffUnchecked dict, the "nothing to act on — no email." log line must not print as-is; print "no protective alert — no alert email (the de-risk check couldn't run: see the failure above)." Find the exact line (~833) and keep its existing behaviour otherwise. Do NOT add a per-day dedupe of the failure email (owner accepted one per run).
6. **Outage wording (take it):** in `risk_window._coverage_note`'s all-`no_prices` branch use a ticker-only list ("no price history could be loaded for AAA and BBB (market data offline?)"), not `_names()` (which repeats "no price history" and the share). Update its test.
7. **User Guide (app.py, Act Today bullet "One exception: …"):** after 1–2, replace the end with: "…the header reads **"⚠️ Act Today — de-risk check couldn't run"** and a warning says the risk-off de-risk trim wasn't considered and why; 🧾 Summary and the dock line say the same instead of an all-clear, and the pre-market run emails it (in the alert email's footer, or as a failure email when there is no alert)."
8. **Test tighten:** `tests/test_risk_off_check.py` ~212: assert no card of kind/type `risk_off_derisk` in the brief's act list directly (find the field name the Brief's risk-off cards carry).

Checks as before (py_compile; touched tests + tests/test_home_risk_synthesis*.py, tests/test_home_dock_digest*.py, tests/test_risk_off_check.py, tests/test_held_stop_lanes.py, tests/test_daily_briefing.py, tests/test_headless_alert_engine.py at `-n 4`; check_antipatterns). No full suite, no commit. Don't touch CLAUDE.md, docs/bug-register.md's CorrYoung row, or docs/plans/corr-young-holding.md (another session's work).
