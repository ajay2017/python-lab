# Money-surface validation (screenshot pass)

**Status:** Drafted 2026-10-30. First pass planned for the week of 2026-11-02 (market hours for the live items).

**Why this exists.** `app.py` has no tests, so a screenshot is the only check that a screen tells the truth. Not every screen matters equally: this list covers the surfaces where a wrong number or a wrong call moves money (a sell that shouldn't happen or doesn't happen, a wrong share count, a buy that breaks a cap). The 2026-10-29 and 10-30 passes found 27 such defects, most of them a sentence or a number on one surface disagreeing with another.

**How a pass works.** The owner sends screenshots in the order below; the lead checks each against the code's rule and the arithmetic, and against the same ticker on the other surfaces. Each finding goes to the CLAUDE.md queue with its file:line. For every check: no literal `**`, and `$` amounts render as text (not LaTeX).

## P1 — Exits and stops (does a sell fire when it should, and only then)

The highest-impact area. Note: until finding #26 C3 ships (deploy weekend 2026-11-07/08), only manual stops can fire; until then, verify that the screens never imply an automatic stop is protecting a position.

| Surface | Screenshot | What gets checked |
|---|---|---|
| 🏠 Brief → Act Today | every 🛑 stop-breach, 📉 Sell / deterioration card | "Sell all N shares" = the holding's exact shares; the price vs stop wording ("is below" / "at or just above"); gap = (price − stop) / price; one card per name |
| 📡 Signals → Active Signals → Stop Loss | the whole box | same stop and gap as the Brief for each name; a breached name reads as breached (finding #24 until C3) |
| 🥧 Portfolio Overview holdings table | the Stop / Stop Type / Gap columns | stop type matches the badge on the Brief (manual vs auto); manual stop shown only when it is the tighter one |
| 📈 Analysis → Trade Plan (held name) | the stop / exit-plan block | same stop as the Brief and the table |
| Morning protective email (09:xx) | the email body | the same breaches and share counts as the Brief that morning |

## P2 — Position sizing (how many dollars go into a buy)

| Surface | Screenshot | What gets checked |
|---|---|---|
| 🏠 Grow Today pick / add cards | each sized card + the risk footer | shares × price = the $ shown; risk = shares × (entry − stop) within the per-trade risk rule; no name over its single-name / capital cap; the footer's "if every stop hits" total = the sum of the cards |
| 🏠 Grow Today budget lines | the "New positions today" and "Today's new buys" lines | the two figures agree (finding #21: 99.6% vs 84.3% seen 10-29) and match the 40% daily budget statement |
| 📋 Watchlist Enter Now / Near Entry | sized cards | the same sizing rule as Grow Today; "No size yet" where nothing should be sized |
| 📈 Analysis → Trade Plan (not held) | the position-size block | the same share count Grow Today / Watchlist would give for that name today |

## P3 — Trims (how much of a position to sell)

| Surface | Screenshot | What gets checked |
|---|---|---|
| 🏠 Brief → Review / Monitoring | trim cards (weak-large, earnings-week, macro protective) | shares to sell take the weight to the stated target; under one share reads Watch, never "sell 0"/"sell 1 share" wrongly |
| 🔔 Catalyst Watch → Pre-Earnings Playbook | each held name reporting within 7 days | the same action and share count as the Brief and Signals (no rating-change REDUCE since #22) |
| 📡 Signals → Signal-Driven Actions | each card's Recommended Action | share count and % of position add up; agrees with the Brief |
| 📡 Signals → Diversification → Reduce / Rebalance | each sector card | "Reduce by $X" = (sector weight − target) × book value; trim candidates lowest conviction first |
| 🏠 Home → Rebalancer / Tune-up | trim rows | the same share counts as the cards above; zero-share trims not counted as "trims would move" |

## P4 — Entry gates (whether today is a buy day at all)

| Surface | Screenshot | What gets checked |
|---|---|---|
| 🏠 Brief header + Grow Today | the market-tone line and any Protect Mode / flat-day box | the tone matches the S&P move; on a non-bull day only fully confirmed setups (the higher flat-day bar) |
| 📡 Signals → Diversification → Add | the redeploy headline + caption | it agrees with Grow Today's call for the day (⏸ / 🛡️ off bull days) |
| 📋 Watchlist / 📈 Analysis | R:R gate cards (below 2:1, not validated, can't be computed) | R:R shown to 2 decimals never rounds across 2:1; pullback price math |
| 📅 Economic Calendar / 🔔 Catalyst Watch | pre-event actions on release / report days | "Released at …" after the time passes (#13); report-day earnings cards (#25) |

## P5 — Book numbers every rule above depends on

| Surface | Screenshot | What gets checked |
|---|---|---|
| 🥧 Portfolio Overview | the holdings table (Shares, Avg Cost, Price, Market Value, P&L, Weight) | MV = shares × price; weights sum to ~100% and match the Brief; avg cost matches the broker |
| 🏠 Home → Today's P&L / account value | the P&L and capital tiles | matches the broker's day change and value (within live-price timing) |
| ⚙️ / 🩺 Broker drift, System Trust | the drift line and ② data stores | holdings match the broker; no stale store |

## P6 — Scores and labels at a threshold

Composite scores at 64.5–64.99 must read "64.6", never "65" (#9); Buy / Hold / Sell labels must match the score shown beside them; R:R near 2.0 and 2.5 shown to two decimals.

## Order for the first pass

1. P1 + P5 together (one market-hours session): they decide sells and every weight.
2. P2 + P3 on a day Grow Today has sized picks and a trim exists.
3. P4 on a non-bull day and a bull day; the release / report-day items on their dates (NFP 2026-11-06 08:30 ET).
4. P6 opportunistically.
