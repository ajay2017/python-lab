# Signal Reconciliation — momentum_available display flag (add-winner false-corroboration fix)

**Status:** DESIGNED, NOT BUILT
**Designed by:** planner (Opus 4.8, model id claude-opus-4-8[1m]), 2026-08-27
**Design verdict:** PROCEED (see end of doc)
**Precedent:** commit 6202784 — "fix(brief): label the scanner score Momentum, drop a fabricated RSI" — added scanner_row_is_synthetic: bool = False to _cross_reference and used it to suppress a display-only line WITHOUT touching any numeric input into reconcile_signals. This fix follows the same discipline one layer deeper.

---

## 1. The defect (verified against HEAD, not recollection)

### Call chain (confirmed)
- daily_briefing.py::_cross_reference (def at line 388, signature lines 388-393) already carries scanner_row_is_synthetic: bool = False.
- It has THREE call sites in daily_briefing.py:
  - line 1022 — _grow_today real scanner rows (new_pick). scanner_row_is_synthetic defaults False.
  - line 2086 — brief "New Positions" real scanner rows (new_pick). Defaults False.
  - line 2149 — the add-winner path. Passes scanner_row_is_synthetic=True. This is the ONLY site that sets the flag True.
- _cross_reference calls reconcile_signals exactly once, at line 596, currently passing momentum_score=scan_score (line 598).
- On the add-winner path the "scanner row" is a synthetic dict built at lines 2142-2144: _synthetic = {"Signal": sig, "Score": scr} where scr = _f(row.get("Score"), 0) is the 4-pillar COMPOSITE read from port_df (line 2109), not a momentum reading.
- Inside _cross_reference: scan_score = _f(scanner_row.get("Score", 0)) (line 435) = the composite. Then comp_sig, comp_scr = lookup_composite(ticker, port_df, composites) (line 455) resolves the composite from the SAME port_df row. So reconcile_signals is called with momentum_score == composite_score == scr.

### The false claim (reproduced exactly)
In reconcile_signals (signal_reconciliation.py), the add-winner branch that fires is the GO branch (lines 162-174), because the synthetic path always has a "Strong Buy" composite (the branch that builds _synthetic at line 2110 requires "Strong Buy" in sig and scr >= COMPOSITE_BUY). With is_mover=False (the add-winner call does not pass is_mover), the one-liner renders:

> "Momentum 71 · Score: Strong Buy (71/100) — technical momentum and full-score analysis agree. Cleared to act within position-sizing rules."

That "technical momentum and full-score analysis agree" is a corroboration claim asserting TWO independent sources when there was only ever one (the composite, compared to itself). This is the large coloured _v_one line in the "ADD — Winning Position" card, rendered in app.py at line 9644 (_v_one = _reconciled.get("one_liner") ...) and printed at lines 9691-9692. Confirmed live and currently shipping.

---

## 2. The trap — do NOT touch the numeric inputs (verified)

reconcile_signals has a protective suppression at lines 118-130:

    if negative_news and momentum_score >= COMPOSITE_BUY:
        return {... "verdict": "skip", "label": "❌ Skip — Negative News" ...}

COMPOSITE_BUY = 65 (constants.py:238), NEWS_SENTIMENT_NEGATIVE = -0.15 (constants.py:697).

On the add-winner path the SYNTHETIC composite (momentum_score = scr) is exactly what satisfies momentum_score >= COMPOSITE_BUY, and the add-winner branch is only reached when the real composite already cleared COMPOSITE_BUY (line 2110). If momentum_score going into reconcile_signals were nulled / zeroed / omitted, this negative-news skip would silently stop firing for every add-winner with negative news — flipping the reconciled verdict from skip to go. This was mutation-verified in the prior related fix.

**Hard constraint carried into this design: the numeric arguments to reconcile_signals at daily_briefing.py:596 stay byte-identical on every path. Only the returned COPY string may differ.**

---

## 3. Which lines of reconcile_signals branch on momentum_score vs. use it only for display

This distinction is the entire safety argument. Verified by reading the function end to end (lines 62-187):

| Use of momentum_score | Line | Kind | Touched by this fix? |
|---|---|---|---|
| ... and momentum_score >= COMPOSITE_BUY (SKIP — composite contradicts) | 103 | branch condition | NO — untouched |
| if negative_news and momentum_score >= COMPOSITE_BUY (SKIP — negative news, PROTECTIVE) | 119 | branch condition | NO — untouched |
| mom_str = "Breakout today" if is_mover else f"Momentum {momentum_score:.0f}" | 88 | display string only | YES |

verdict, label, color, icon, and composite_available are all derived from comp_class / composite_available / earnings_imminent / negative_news — NONE of them reads momentum_score except the two branch conditions above, which stay put. The new flag will be read ONLY where mom_str and the one-liner strings are built. That is the invariant the tests must pin.

### Reachability of each branch on the synthetic (momentum_available=False) path
On that path: comp_class is always "buy" (Strong Buy required at the producer), composite_available is always True, and momentum_score == composite >= COMPOSITE_BUY.

| Branch | Lines | Reachable when momentum_available=False? | Needs new copy |
|---|---|---|---|
| SKIP — composite contradicts | 102-116 | NO (comp_class is buy, never sell/hold) | defensive only |
| SKIP — negative news (protective) | 118-130 | YES | YES |
| CAUTION — earnings imminent | 132-146 | YES | YES |
| VERIFY — composite not loaded | 148-160 | NO (composite always available) | n/a |
| GO — composite confirms | 162-174 | YES (the reported bug) | YES |
| FALLBACK — verify / mixed conviction | 176-187 | NO (comp_class is buy, not hold) | defensive only |

---

## 4. The mechanism

Add one parameter to reconcile_signals:

    def reconcile_signals(
        ticker, momentum_score, momentum_signal=None,
        composite_score=None, composite_signal=None,
        is_held=False, is_mover=False,
        earnings_days=None, news_sentiment=None,
        momentum_available: bool = True,      # NEW — default True = "assume a real momentum reading"
    ) -> dict:

- Default True preserves current behaviour at every existing call site.
- Read it in exactly two display-only places: (a) the mom_str construction (line 88), and (b) the one-liner strings of the branches listed above. Never in a branch condition, never in a returned verdict/label/color/icon/composite_available value.

mom_str (line 88) becomes:

    if is_mover:
        mom_str = "Breakout today"
    elif not momentum_available:
        mom_str = None      # no independent scanner momentum reading for a held position
    else:
        mom_str = f"Momentum {momentum_score:.0f}"

Then each affected one-liner is written conditionally on momentum_available (a small if/else around the one_liner string inside each branch — verdict/label/color/icon stay OUTSIDE the conditional and are byte-identical). Because mom_str can be None, the False-case strings must be authored to not reference it at all (they lead with the composite). The True-case strings are copied verbatim from today's code.

### Wiring at the producer (the only other edit)
daily_briefing.py:596 — thread the existing signal through:

    _reconciled = reconcile_signals(
        ...,
        momentum_score=scan_score,            # UNCHANGED — byte-identical
        ...,
        momentum_available=not scanner_row_is_synthetic,   # NEW
    )

scan_score is NOT changed. The synthetic path passes momentum_available=False; both real new_pick paths pass True (their scanner_row_is_synthetic is False).

---

## 5. Exact replacement copy (momentum_available=False)

Tone matched to the existing function. comp_str = "Score: <signal> (<score>/100)" exactly as built at lines 89-92.

**GO branch (lines 162-174) — the reported bug.**
True (unchanged):
> "Momentum 71 · Score: Strong Buy (71/100) — technical momentum and full-score analysis agree. Cleared to act within position-sizing rules."

False (new):
> "{comp_str} — full multi-factor analysis confirms this add; there is no separate scanner momentum reading for a position you already hold. Cleared to act within position-sizing rules."

e.g. "Score: Strong Buy (71/100) — full multi-factor analysis confirms this add; there is no separate scanner momentum reading for a position you already hold. Cleared to act within position-sizing rules."

**SKIP — negative news (lines 118-130) — PROTECTIVE, reachable.**
True (unchanged):
> "Momentum 71 but news sentiment is negative (-0.45). Wait for the news catalyst to clear before entering."

False (new):
> "{comp_str} but news sentiment is negative ({news_sentiment:+.2f}). Hold off adding until the news catalyst clears."

**CAUTION — earnings imminent (lines 132-146) — reachable.**
True (unchanged):
> "Momentum 71 · Score: Strong Buy (71/100) — but earnings in 3d make entry a binary event. Wait for the post-print setup."

False (new):
> "{comp_str} — but earnings {earnings_label} make adding a binary event. Wait for the post-print setup."

**SKIP — composite contradicts (lines 102-116) — unreachable on synthetic path, defensive copy.**
False (new):
> "{comp_str} — full multi-factor analysis says {verb}. The full score weighs fundamentals and sentiment, not just price action. Skip until it turns."

(No "technical momentum" phrase; no claim of a second source.)

**FALLBACK — verify / mixed conviction (lines 176-187) — unreachable on synthetic path, defensive copy.**
False (new):
> "{comp_str} — the full score is neutral. Review the Analysis page before adding."

**VERIFY — composite not loaded (lines 148-160):** unreachable on the synthetic path (composite is always present there). Recommended for completeness so no branch can dereference a None mom_str:
> False: "The full score hasn't loaded yet for this held position — open Analysis to confirm before adding."

None of the False strings contain the words "momentum", "technical momentum", or any "agree"/"two sources" claim.

---

## 6. Blast radius

reconcile_signals has exactly TWO production call sites (verified by repo-wide grep for reconcile_signals( ; all other hits are imports, docstrings, tests, or plan/review docs):

1. app.py:19150 — 🏆 Top Picks card. Passes a REAL momentum score (momentum_score=float(row["Score"]) from scanner_results). Does not pass the new kwarg → defaults True → behaviour unchanged. **No edit needed.**
2. daily_briefing.py:596 (inside _cross_reference) — the single reconcile call that feeds ALL of Daily Briefing, Grow Today, Market Scanner, and Watchlist (those surfaces consume _cross_reference's output via the xref/verdict_reconciled dict; they do NOT call reconcile_signals themselves). **This is the only edit** — add momentum_available=not scanner_row_is_synthetic.

So the "Grow Today / Market Scanner / Watchlist call it too" note is true at the SURFACE level but the actual reconcile invocation for all of them is the single line 596; only the add-winner branch that reaches it (via _cross_reference call site line 2149) passes scanner_row_is_synthetic=True. New default True means every other path is untouched.

**Conclusion: the flag defaults to "assume a real momentum reading" everywhere; exactly one call path flips it to False; no other caller changes.**

---

## 7. Tests to specify (do not write here — for the implementer)

Add to tests/test_signal_reconciliation.py:

1. **Verdict-invariance (the core safety proof).** For a representative input in each reachable-and-defensive branch (GO buy; SKIP negative news; CAUTION earnings; SKIP composite-contradicts; FALLBACK hold), call reconcile_signals twice — once momentum_available=True, once False, all other args identical — and assert verdict, label, color, icon, and composite_available are IDENTICAL across the pair. Only one_liner may differ. Parametrize so a future branch that forgets the invariant fails.

2. **Protective-gate mutation guard (the trap).** reconcile_signals(momentum_score=90, composite_score=90, composite_signal="Strong Buy", news_sentiment=-0.9, momentum_available=False) must STILL return verdict == "skip" and label == "❌ Skip — Negative News". This is the exact mutation a "clean up the fabricated value" fix would break; assert it explicitly with a comment citing this plan.

3. **Copy test — no false corroboration.** For momentum_available=False across every branch, assert the one_liner does NOT contain any of: "technical momentum and full-score analysis agree", "technical momentum", "Momentum " (the fabricated numeric phrase), or the bare substring "agree". And positively assert the GO-branch False one-liner contains "no separate scanner momentum reading".

4. **True-case regression.** Assert the momentum_available=True GO one-liner still contains "technical momentum and full-score analysis agree" (proves default behaviour is byte-identical, guarding every existing surface).

Add to tests/test_daily_briefing.py (integration):

5. **Synthetic path produces honest copy.** Drive the add-winner branch (a held port_df row with Signal="Strong Buy", Score >= COMPOSITE_BUY, Gap to Stop (%) >= ADD_WINNER_MIN_GAP_PCT, no suppressions) through the brief builder and assert the resulting add_winner item's xref["verdict_reconciled"]["one_liner"] contains "no separate scanner momentum reading" and does NOT contain "agree". Guards the wiring at line 596, not just the unit function.

6. **Real new_pick path unchanged.** A real scanner new_pick with a confirming composite still yields the "...agree..." one-liner (proves momentum_available defaulted True on the non-synthetic path).

---

## 8. Review classification

signal_reconciliation.py is NOT in _GATE_FILES, so editing it alone would not mechanically force a review citation. HOWEVER, this change also edits daily_briefing.py:596, and daily_briefing.py IS in _GATE_FILES. The pre_tool_checks.py commit hook will therefore MECHANICALLY REQUIRE an Opus-review citation in the commit body regardless. Do not treat this as an optional review.

Beyond the mechanical trigger, a review is genuinely warranted on the merits: reconcile_signals is the central authority feeding four decision surfaces, and this change sits one refactor away from the exact protective negative-news suppression that a prior planner + Opus reviewer mutation-tested. **Invoke reviewer (Opus) before shipping.** The reviewer's job here is specifically to confirm (a) the numeric args at line 596 are byte-identical, (b) momentum_available is never read in a branch condition or in any returned verdict/label/color/icon/composite_available, and (c) test #2 actually fails if the protective gate is disabled.

Because this is a fix( (not feat( ), the Design=/Build= trailers are not hook-required, but the Opus-review citation (Hard Rule #4) is — both because daily_briefing.py is a gate file and because it touches recommendation-copy on a live decision surface.

---

## Design verdict: PROCEED

The fix is well-scoped, follows the established scanner_row_is_synthetic precedent exactly, changes only what is SAID and never what is COMPUTED, and the protective negative-news gate is provably preserved because both branch conditions on momentum_score (lines 103, 119) are untouched and the new flag is read only in display-copy construction. One parameter, one producer-side wiring line, no other caller changes, six specified tests including the mutation guard that pins the trap. Ship it through implementer (mechanical, decided spec) and route the commit through reviewer (Opus) — required, not optional.
