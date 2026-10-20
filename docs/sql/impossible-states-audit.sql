-- =====================================================================
-- DRISHTA — Impossible-States Audit
-- Written 2026-10-14. Companion to docs/sql/data-integrity-audit.sql
-- (that pack covers stale bundles, flow duplicates, orphans, the price
-- cross-check and the holdings/trades ledger — not repeated here).
--
-- AUTOMATED (2026-10-14): Q0 also runs weekly in the Saturday maintenance
-- cron and on System Trust (memoized ~5 min), as data_maintenance check 6, via
-- the function in impossible-states-function.sql (GENERATED from Q0 below --
-- after editing Q0, run scripts/gen_impossible_states_function.py and
-- re-apply that file). The BASELINE block below is mirrored in
-- stock_analyzer/impossible_states.py -- update both together.
--
-- PURPOSE: find bugs that have ALREADY reached production data, by
-- looking for rows that should be impossible if the code were correct.
-- Each check targets one of the recurring bug classes:
--   A  "absence reads as a good result"  (fabricated best value / neutral)
--   B  records persisting an unverified claim  ("records withhold")
--   C  two producers of the same fact that disagree
--   D  plain impossible states (identities broken, wrong-day stamps)
--   E  recompute / provenance (a stored value must equal its own derivation)
--
-- READ-ONLY. Every statement is a SELECT. Safe against production.
--
-- HOW TO RUN: the Supabase SQL editor returns only the LAST result set.
--   1. Run Q0 (the summary) alone. It returns one row per check with
--      n_bad / n_total. Paste the result back.
--   2. For any check with n_bad > 0, run its detail block (same ID).
-- If Q0 errors with "relation/column does not exist", that table's DDL
-- was never applied — delete that check's UNION ALL arm and re-run
-- (and paste the error: it is itself a finding).
--
-- Thresholds below are transcribed from stock_analyzer/constants.py at
-- HEAD 2026-10-14: COMPOSITE_BUY=65, COMPOSITE_STRONG_BUY=75 (=HIGH_
-- CONVICTION), SINGLE_NAME_CEILING=15.0, CORR_MIN_OBS_TRUSTED=20,
-- ACCOUNT_CASH_STALE_DAYS=7, and (section E, 2026-10-19) COMPOSITE_WEIGHTS
-- technical .25 / business_quality .35 / valuation .30 / sentiment .10.
-- diversification_score = (1-avg_corr)/2*100
-- (portfolio.py:1915), so its ceiling for a long-only book is 50.
-- Dates of record-side fixes: F-291/F-292 withhold-on-unverified 2026-10-09.
-- =====================================================================


-- ---------------------------------------------------------------------
-- BASELINE — Q0 on 2026-10-14, after ff5ef85 + 3ab4d59 and the owner-run
-- impossible-states-cleanup.sql. Re-run Q0 monthly and compare: a check
-- that rises above its baseline, or a new non-zero check, is a NEW defect.
-- Every residual below was investigated and explained (Q1/Q2):
--   A3  23  exact-50 pillars. WK 2026-10-07 (t 68 -> 50 -> 68) is UNPROVEN,
--           most likely a real full-leg 50 (WK has years of history; a
--           2026-10-15 check found 0 non-integer t_score rows). The real
--           hole was short history, fixed by F-304 (t_available). The rest
--           sit on the 4-point t_score lattice, or are real scored-0.0
--           sentiment / stable bq readings.
--   C1  19  enter_now on an EXIT/WATCH name = owner's 2026-09-16 warn-only
--           design; new_pick + EXIT all <= 2026-09-11 (pre F-39i fix);
--           add_winner then WATCH later the same day = intraday transition.
--   C2   6  non-binding G-20 pre-pass rows (counterfactual=FALSE) beside a
--           BUY-type rec; intraday transitions (ON 09-21 16:08 = probable
--           WATCH flicker, see the hysteresis open item).
--   C3   2  APP 09-18, MU 09-14: morning cron vs later app composite.
--   C7   3  09-22/29/30: exactly one share of a same-day buy logged after
--           the 17:30 cron; the app rewrote the snapshot later.
--   C9   5  (added after the baseline run, from Q1's C1 detail) ON 09-11,
--           APP 08-28, ARM 08-27, GOOG/MRVL 07-31: all before the F-39i fix.
--           Confirm with the first run of the automated function.
--   C8   3  MU 2026-06-25 (pre-fix sweep hole, unrecoverable -- no close
--           stored); EOG 06-15 (a real sell + rebuy); PLTR 2026-10-14 (a real
--           sell + rebuy: SELL 10 @ 200.58 on 10-14, BUY 5 @ 195.00 on 10-15).
--           Baseline raised 2 -> 3 on 2026-10-17 after the owner's SQL.
--   D5   3  INTC/QCOM/MU 2026-05-26/28: composite column not yet populated.
--   E1  0, E2 0  (2026-10-19, owner SQL: 438 / 1,273 rows). E3 not yet
--           baselined: ACCRUING in impossible_states.py until the first count.
--   Every other check: 0.
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- Q0 — SUMMARY. Run this first, alone.
-- ---------------------------------------------------------------------
SELECT * FROM (
  -- A: fabricated best value --------------------------------------------
  SELECT 'A1' AS id, 'risk snapshot: diversification_score >= 50 or avg_corr <= 0 (best-possible/impossible reading)' AS chk,
         COUNT(*) FILTER (WHERE diversification_score >= 50 OR avg_pairwise_corr <= 0) AS n_bad,
         COUNT(*) FILTER (WHERE diversification_score IS NOT NULL) AS n_total
  FROM portfolio_risk_snapshots
  UNION ALL
  SELECT 'A2', 'score_history: pillar flagged unavailable but a score was stored anyway',
         COUNT(*) FILTER (WHERE (bq_available IS FALSE AND bq_score IS NOT NULL)
                             OR (val_available IS FALSE AND val_score IS NOT NULL)),
         COUNT(*)
  FROM score_history
  UNION ALL
  SELECT 'A3', 'score_history: a pillar exactly 50.0 (the fabricated-neutral value)',
         COUNT(*) FILTER (WHERE t_score = 50 OR s_score = 50 OR bq_score = 50 OR val_score = 50),
         COUNT(*)
  FROM score_history
  UNION ALL
  SELECT 'A4', 'score_history: composite >= BUY bar while a pillar was unmeasured',
         COUNT(*) FILTER (WHERE composite >= 65 AND (bq_available IS FALSE OR val_available IS FALSE)),
         COUNT(*) FILTER (WHERE composite >= 65)
  FROM score_history
  UNION ALL
  SELECT 'A5', 'recommendations: momentum_score exactly 0 (fabricated "Momentum 0")',
         COUNT(*) FILTER (WHERE momentum_score = 0),
         COUNT(*)
  FROM recommendations
  UNION ALL
  SELECT 'A6', 'sentiment_history: VADER exactly 0 with no headlines (neutral stored for "no data")',
         COUNT(*) FILTER (WHERE vader_compound = 0 AND COALESCE(headline_count, 0) = 0),
         COUNT(*)
  FROM sentiment_history
  -- B: unverified claim persisted ----------------------------------------
  UNION ALL
  SELECT 'B1', 'risk snapshot since 2026-10-09: score stored on a thin/unknown sample (should be withheld)',
         COUNT(*) FILTER (WHERE diversification_score IS NOT NULL
                            AND (corr_coverage_n IS NULL OR corr_coverage_n < 20)),
         COUNT(*)
  FROM portfolio_risk_snapshots WHERE snapshot_date >= DATE '2026-10-09'
  UNION ALL
  SELECT 'B2', 'account snapshot: equity/leverage stored from stale or unknown cash (>7d)',
         COUNT(*) FILTER (WHERE net_equity IS NOT NULL
                            AND (cash_as_of IS NULL
                                 OR snapshot_date - (cash_as_of AT TIME ZONE 'America/New_York')::date > 7)),
         COUNT(*) FILTER (WHERE net_equity IS NOT NULL)
  FROM account_daily_snapshots
  UNION ALL
  SELECT 'B3', 'weekly thesis: "diversified" claim with no verified risk reading behind it',
         COUNT(*) FILTER (WHERE t.claims->>'correlation_structure' = 'diversified'
                            AND (r.diversification_score IS NULL OR r.corr_coverage_n IS NULL OR r.corr_coverage_n < 20)),
         COUNT(*) FILTER (WHERE t.claims->>'correlation_structure' = 'diversified')
  FROM portfolio_thesis t
  LEFT JOIN LATERAL (SELECT diversification_score, corr_coverage_n FROM portfolio_risk_snapshots
                     WHERE snapshot_date <= t.thesis_date ORDER BY snapshot_date DESC LIMIT 1) r ON TRUE
  -- C: two producers disagree --------------------------------------------
  UNION ALL
  SELECT 'C1', 'BUY-type rec and an exit signal on the same ticker, same day',
         COUNT(DISTINCT (r.ticker, r.rec_date)), NULL
  FROM recommendations r JOIN exit_signals e ON e.ticker = r.ticker AND e.signal_date = r.rec_date
  WHERE r.rec_type IN ('new_pick', 'add_winner', 'enter_now')
  UNION ALL
  -- C9 (added 2026-10-14) splits C1's REGRESSION class out of its by-design
  -- rows: a NEW-position or add buy on a name under an EXIT/TRIM call the
  -- same day is the F-39i whiplash class (every pre-fix row is <= 2026-09-11).
  -- C1 keeps the owner's warn-only enter_now-on-a-WATCH-name design case.
  SELECT 'C9', 'new_pick/add_winner rec and an EXIT/TRIM signal on the same ticker, same day',
         COUNT(DISTINCT (r.ticker, r.rec_date)), NULL
  FROM recommendations r JOIN exit_signals e ON e.ticker = r.ticker AND e.signal_date = r.rec_date
  WHERE r.rec_type IN ('new_pick', 'add_winner') AND e.signal_type IN ('EXIT', 'TRIM')
  UNION ALL
  -- NB (2026-10-14): for G-20, counterfactual=TRUE marks the BINDING row and
  -- FALSE the pre-pass "recorded, non-binding" row (daily_briefing.py ~1883/
  -- ~1931), so this arm counts NON-binding G-20 rows — still a real overlap
  -- (a "don't add" reason and a BUY-type rec on one name/day), not a leak.
  SELECT 'C2', 'gate suppression row (counterfactual not TRUE) AND a BUY-type rec the same day',
         COUNT(DISTINCT (g.ticker, g.rec_date, g.gate_id)), NULL
  FROM gate_suppressions g JOIN recommendations r ON r.ticker = g.ticker AND r.rec_date = g.rec_date
  WHERE g.counterfactual IS NOT TRUE AND r.rec_type IN ('new_pick', 'add_winner', 'enter_now')
  UNION ALL
  SELECT 'C3', 'score_history vs exit_signals composite differ by > 5 pts (same ticker/day)',
         COUNT(*) FILTER (WHERE ABS(s.composite - e.composite_score) > 5),
         COUNT(*)
  FROM score_history s JOIN exit_signals e ON e.ticker = s.ticker AND e.signal_date = s.score_date
  WHERE e.composite_score IS NOT NULL
  UNION ALL
  SELECT 'C4', 'score_history vs add_winner rec composite differ by > 5 pts (same ticker/day)',
         COUNT(*) FILTER (WHERE ABS(s.composite - r.composite_score) > 5),
         COUNT(*)
  FROM score_history s JOIN recommendations r ON r.ticker = s.ticker AND r.rec_date = s.score_date
  WHERE r.rec_type = 'add_winner' AND r.composite_score IS NOT NULL
  UNION ALL
  SELECT 'C5', 'rec_events beta_trim metric_before != that day''s portfolio_beta (same EOD run)',
         COUNT(*) FILTER (WHERE (e.metric_before IS NULL) <> (p.portfolio_beta IS NULL)
                             OR ABS(e.metric_before - p.portfolio_beta) > 0.01),
         COUNT(*)
  FROM rec_events e JOIN portfolio_risk_snapshots p ON p.snapshot_date = e.fired_date
  WHERE e.rec_type = 'beta_trim' AND e.source = 'cron'
  UNION ALL
  SELECT 'C6', 'rec_events diversify_add avg_corr / coverage != that day''s risk snapshot',
         COUNT(*) FILTER (WHERE (e.metric_before IS NULL) <> (p.avg_pairwise_corr IS NULL)
                             OR ABS(e.metric_before - p.avg_pairwise_corr) > 0.001
                             OR e.corr_coverage_n IS DISTINCT FROM p.corr_coverage_n),
         COUNT(*)
  FROM rec_events e JOIN portfolio_risk_snapshots p ON p.snapshot_date = e.fired_date
  WHERE e.rec_type = 'diversify_add' AND e.source = 'cron'
  UNION ALL
  SELECT 'C7', 'account gross_book vs SUM(daily_snapshots shares*close) differ by > $5',
         COUNT(*) FILTER (WHERE ABS(a.gross_book - d.book) > 5),
         COUNT(*)
  FROM account_daily_snapshots a
  JOIN (SELECT snapshot_date, SUM(shares * close_price) AS book FROM daily_snapshots GROUP BY 1) d
    ON d.snapshot_date = a.snapshot_date
  UNION ALL
  SELECT 'C8', 'holding present the day before AND after but missing in between (a hole, not a sale)',
         COUNT(*), NULL
  FROM (SELECT snapshot_date,
               LAG(snapshot_date)  OVER (ORDER BY snapshot_date) AS prev_d,
               LEAD(snapshot_date) OVER (ORDER BY snapshot_date) AS next_d
        FROM (SELECT DISTINCT snapshot_date FROM daily_snapshots) x) o
  JOIN daily_snapshots p ON p.snapshot_date = o.prev_d
  JOIN daily_snapshots n ON n.snapshot_date = o.next_d AND n.ticker = p.ticker
  WHERE NOT EXISTS (SELECT 1 FROM daily_snapshots m
                    WHERE m.snapshot_date = o.snapshot_date AND m.ticker = p.ticker)
  -- D: plain impossible states -------------------------------------------
  UNION ALL
  SELECT 'D1', 'risk snapshot: score != (1-avg_corr)/2*100, or only one of the pair is NULL',
         COUNT(*) FILTER (WHERE (diversification_score IS NULL) <> (avg_pairwise_corr IS NULL)
                             OR ABS(diversification_score - (1 - avg_pairwise_corr) / 2 * 100) > 0.2),
         COUNT(*)
  FROM portfolio_risk_snapshots
  UNION ALL
  SELECT 'D2', 'account snapshot: net_equity != gross+cash, or leverage != gross/net_equity',
         COUNT(*) FILTER (WHERE (net_equity IS NOT NULL AND ABS(net_equity - (gross_book + cash_balance)) > 0.01)
                             OR (leverage IS NOT NULL AND (net_equity IS NULL OR net_equity <= 0
                                 OR ABS(leverage - gross_book / net_equity) > 0.01))),
         COUNT(*)
  FROM account_daily_snapshots
  UNION ALL
  SELECT 'D3', 'EOD rows stamped with a different date than the ET day they were written',
         COUNT(*), NULL
  FROM (SELECT snapshot_date, created_at FROM portfolio_risk_snapshots
        UNION ALL SELECT snapshot_date, created_at FROM account_daily_snapshots) z
  WHERE z.snapshot_date <> (z.created_at AT TIME ZONE 'America/New_York')::date
  UNION ALL
  SELECT 'D4', 'market-day rows dated on a weekend or an NYSE closed day',
         COUNT(*), NULL
  FROM (SELECT rec_date AS d FROM recommendations
        UNION ALL SELECT signal_date FROM exit_signals
        UNION ALL SELECT score_date FROM score_history
        UNION ALL SELECT snapshot_date FROM daily_snapshots
        UNION ALL SELECT snapshot_date FROM portfolio_risk_snapshots
        UNION ALL SELECT snapshot_date FROM account_daily_snapshots
        UNION ALL SELECT fired_date FROM rec_events) z
  WHERE EXTRACT(ISODOW FROM z.d) IN (6, 7)
     OR z.d IN (DATE '2026-01-01', DATE '2026-01-19', DATE '2026-02-16', DATE '2026-04-03',
                DATE '2026-05-25', DATE '2026-06-19', DATE '2026-07-03', DATE '2026-09-07')
  UNION ALL
  SELECT 'D5', 'BUY-type rec below the BUY bar, no composite, or no usable price',
         COUNT(*) FILTER (WHERE composite_score IS NULL OR composite_score < 65
                             OR price_at_surface IS NULL OR price_at_surface <= 0),
         COUNT(*)
  FROM recommendations WHERE rec_type IN ('new_pick', 'add_winner', 'enter_now')
  UNION ALL
  SELECT 'D6', 'rec conviction=high with composite below STRONG_BUY (75)',
         COUNT(*) FILTER (WHERE composite_score < 75),
         COUNT(*)
  FROM recommendations WHERE conviction = 'high'
  UNION ALL
  SELECT 'D7', 'sized rec (v2+) above the 15% single-name ceiling of its own book value',
         COUNT(*) FILTER (WHERE rec_shares * price_at_surface / rec_portfolio_value * 100 > 15.05),
         COUNT(*)
  FROM recommendations
  WHERE rec_sizing_version >= 2 AND rec_shares > 0 AND price_at_surface > 0 AND rec_portfolio_value > 0
  UNION ALL
  SELECT 'D8', 'model_predictions: band excludes the point, or scored row with missing/wrong error',
         COUNT(*) FILTER (WHERE predicted_low > predicted_value OR predicted_high < predicted_value
                             OR (realized_value IS NOT NULL AND (abs_error IS NULL OR scored_at IS NULL
                                 OR ABS(abs_error - ABS(predicted_value - realized_value)) > 1e-6))),
         COUNT(*)
  FROM model_predictions
  UNION ALL
  SELECT 'D9', 'analyst_coverage: price targets out of order (low > avg > high) or <= 0',
         COUNT(*) FILTER (WHERE low_pt > avg_pt OR avg_pt > high_pt OR low_pt <= 0 OR avg_pt <= 0 OR high_pt <= 0),
         COUNT(*)
  FROM analyst_coverage
  UNION ALL
  SELECT 'D10', 'trades: SELL without realized_pnl, BUY with one, or dated in the future',
         COUNT(*) FILTER (WHERE (UPPER(action) = 'SELL' AND realized_pnl IS NULL)
                             OR (UPPER(action) = 'BUY' AND realized_pnl IS NOT NULL)
                             OR traded_at > now()),
         COUNT(*)
  FROM trades
  -- E: recompute / provenance (2026-10-19, app-trust item 2). A stored value
  -- that can be re-derived from its own inputs must still equal that
  -- derivation. Weights are COMPOSITE_WEIGHTS (constants.py): technical .25,
  -- business_quality .35, valuation .30, sentiment .10 (version 2, since
  -- 2026-07-09; test_impossible_states pins these numbers to constants.py).
  UNION ALL
  -- E1: first run 2026-10-19 (owner SQL) = 438 rows / 0 mismatches. An
  -- unmeasured pillar is stored null and enters the composite as 50. Equity
  -- rows only: an ETF composite is the technical/cost blend
  -- (ETF_COMPOSITE_WEIGHTS) and stores no bq/val.
  SELECT 'E1', 'score_history: composite != its weighted pillars (0.25t+0.35bq+0.30val+0.10s, unmeasured = 50)',
         COUNT(*) FILTER (WHERE ABS(composite - (0.25*COALESCE(t_score, 50) + 0.35*COALESCE(bq_score, 50)
                                                 + 0.30*COALESCE(val_score, 50) + 0.10*COALESCE(s_score, 50))) > 0.2),
         COUNT(*)
  FROM score_history
  WHERE NOT (bq_score IS NULL AND val_score IS NULL)
  UNION ALL
  -- E2: first run 2026-10-19 (owner SQL) = 1,273 rows / 0. Sentiment is the
  -- one pillar recommendations does not store, so back it out of the composite.
  SELECT 'E2', 'recommendations (weights v2): implied sentiment outside 0-100 (composite off by >~5 pts)',
         COUNT(*) FILTER (WHERE (composite_score - 0.25*t_score - 0.35*bq_score - 0.30*val_score) / 0.10 < -0.5
                             OR (composite_score - 0.25*t_score - 0.35*bq_score - 0.30*val_score) / 0.10 > 100.5),
         COUNT(*)
  FROM recommendations
  WHERE weights_version = 2 AND composite_score IS NOT NULL AND t_score IS NOT NULL
    AND bq_score IS NOT NULL AND val_score IS NOT NULL
  UNION ALL
  -- E3: NEVER RUN yet -- not baselined (ACCRUING in impossible_states.py until
  -- the owner's first count). Stored values: followed_signal is the text
  -- 'yes' / 'no' / 'discretionary' / NULL (app.py Trade Journal save);
  -- signal_seen is the free-text reconciled verdict at click, e.g.
  -- 'Strong Buy · Composite 81/100'. Detector candidate (a) from 2026-10-19:
  -- MSFT SELL 10-16 marked 'yes' against a 'Buy'.
  SELECT 'E3', 'trades: followed_signal = yes against the OPPOSITE signal (a SELL "following" a Buy, or a BUY "following" a Sell)',
         COUNT(*) FILTER (WHERE (UPPER(action) = 'SELL' AND LOWER(followed_signal) = 'yes'
                                 AND signal_seen ILIKE '%buy%' AND signal_seen NOT ILIKE '%sell%')
                             OR (UPPER(action) = 'BUY' AND LOWER(followed_signal) = 'yes'
                                 AND signal_seen ILIKE '%sell%' AND signal_seen NOT ILIKE '%buy%')),
         COUNT(*) FILTER (WHERE followed_signal IS NOT NULL)
  FROM trades
) q
ORDER BY (n_bad > 0) DESC, id;


-- ---------------------------------------------------------------------
-- Q1 — ALL DETAIL ROWS IN ONE RESULT SET (added after the first Q0 run,
-- 2026-10-14, which hit A3 A6 C1 C2 C3 C7 C8 D4 D5). Run alone.
-- A6 is omitted: its cause was found in code (cron_runner.py's
-- sentiment_history writer copies avg_sent raw, bypassing
-- util.sentiment_value_or_none). D4 is grouped per table+source and also
-- covers gate_suppressions, which Q0 did not.
-- ---------------------------------------------------------------------
SELECT chk, d, ticker, detail FROM (
  SELECT 'A3' AS chk, NULL::date AS d, pillar AS ticker,
         format('exact_50=%s  near_49_51_not_50=%s  total=%s',
                COUNT(*) FILTER (WHERE v = 50),
                COUNT(*) FILTER (WHERE v BETWEEN 49 AND 51 AND v <> 50),
                COUNT(*)) AS detail
  FROM (SELECT 't_score' AS pillar, t_score AS v FROM score_history
        UNION ALL SELECT 's_score',  s_score  FROM score_history
        UNION ALL SELECT 'bq_score', bq_score FROM score_history
        UNION ALL SELECT 'val_score', val_score FROM score_history) z
  WHERE v IS NOT NULL GROUP BY pillar
  UNION ALL
  SELECT 'C1', r.rec_date, r.ticker,
         format('rec %s composite=%s | exit %s composite=%s',
                r.rec_type, r.composite_score, e.signal_type, e.composite_score)
  FROM recommendations r JOIN exit_signals e ON e.ticker = r.ticker AND e.signal_date = r.rec_date
  WHERE r.rec_type IN ('new_pick', 'add_winner', 'enter_now')
  UNION ALL
  SELECT 'C2', g.rec_date, g.ticker,
         format('%s src=%s lane=%s | rec %s composite=%s | %s',
                g.gate_id, g.source, g.lane, r.rec_type, r.composite_score, LEFT(g.reason, 90))
  FROM gate_suppressions g JOIN recommendations r ON r.ticker = g.ticker AND r.rec_date = g.rec_date
  WHERE g.counterfactual IS NOT TRUE AND r.rec_type IN ('new_pick', 'add_winner', 'enter_now')
  UNION ALL
  SELECT 'C3', s.score_date, s.ticker,
         format('score_history=%s | exit %s composite=%s | bq_avail=%s val_avail=%s',
                s.composite, e.signal_type, e.composite_score, s.bq_available, s.val_available)
  FROM score_history s JOIN exit_signals e ON e.ticker = s.ticker AND e.signal_date = s.score_date
  WHERE ABS(s.composite - e.composite_score) > 5
  UNION ALL
  SELECT 'C7', a.snapshot_date, NULL,
         format('gross_book=%s  snapshots_sum=%s  gap=%s  n_tickers=%s',
                a.gross_book, ROUND(d.book, 2), ROUND(a.gross_book - d.book, 2), d.n)
  FROM account_daily_snapshots a
  JOIN (SELECT snapshot_date, SUM(shares * close_price) AS book, COUNT(*) AS n
        FROM daily_snapshots GROUP BY 1) d ON d.snapshot_date = a.snapshot_date
  WHERE ABS(a.gross_book - d.book) > 5
  UNION ALL
  SELECT 'C8', o.snapshot_date, p.ticker,
         format('present %s and %s, absent between | trades in window: %s', o.prev_d, o.next_d,
                COALESCE((SELECT STRING_AGG(t.action || ' ' || t.shares, ', ') FROM trades t
                          WHERE t.ticker = p.ticker
                            AND (t.traded_at AT TIME ZONE 'America/New_York')::date BETWEEN o.prev_d AND o.next_d),
                         'none'))
  FROM (SELECT snapshot_date,
               LAG(snapshot_date)  OVER (ORDER BY snapshot_date) AS prev_d,
               LEAD(snapshot_date) OVER (ORDER BY snapshot_date) AS next_d
        FROM (SELECT DISTINCT snapshot_date FROM daily_snapshots) x) o
  JOIN daily_snapshots p ON p.snapshot_date = o.prev_d
  JOIN daily_snapshots n ON n.snapshot_date = o.next_d AND n.ticker = p.ticker
  WHERE NOT EXISTS (SELECT 1 FROM daily_snapshots m WHERE m.snapshot_date = o.snapshot_date AND m.ticker = p.ticker)
  UNION ALL
  SELECT 'D4', MAX(d), tbl,
         format('src=%s rows=%s distinct_days=%s first=%s last=%s jul03_rows=%s',
                src, COUNT(*), COUNT(DISTINCT d), MIN(d), MAX(d),
                COUNT(*) FILTER (WHERE d = DATE '2026-07-03'))
  FROM (SELECT 'recommendations' AS tbl, rec_date AS d, rec_type AS src FROM recommendations
        UNION ALL SELECT 'exit_signals', signal_date, signal_type FROM exit_signals
        UNION ALL SELECT 'score_history', score_date, source FROM score_history
        UNION ALL SELECT 'daily_snapshots', snapshot_date, 'n/a' FROM daily_snapshots
        UNION ALL SELECT 'portfolio_risk_snapshots', snapshot_date, 'cron' FROM portfolio_risk_snapshots
        UNION ALL SELECT 'account_daily_snapshots', snapshot_date, 'cron' FROM account_daily_snapshots
        UNION ALL SELECT 'rec_events', fired_date, source FROM rec_events
        UNION ALL SELECT 'gate_suppressions', rec_date, source FROM gate_suppressions) z
  WHERE EXTRACT(ISODOW FROM d) IN (6, 7)
     OR d IN (DATE '2026-01-01', DATE '2026-01-19', DATE '2026-02-16', DATE '2026-04-03',
              DATE '2026-05-25', DATE '2026-06-19', DATE '2026-07-03', DATE '2026-09-07')
  GROUP BY tbl, src
  UNION ALL
  SELECT 'D5', rec_date, ticker,
         format('%s composite=%s price=%s conviction=%s verdict=%s',
                rec_type, composite_score, price_at_surface, conviction, verdict)
  FROM recommendations
  WHERE rec_type IN ('new_pick', 'add_winner', 'enter_now')
    AND (composite_score IS NULL OR composite_score < 65 OR price_at_surface IS NULL OR price_at_surface <= 0)
  UNION ALL
  SELECT 'E1', score_date, ticker,
         format('composite=%s implied=%s | t=%s bq=%s val=%s s=%s',
                composite,
                ROUND((0.25*COALESCE(t_score, 50) + 0.35*COALESCE(bq_score, 50)
                       + 0.30*COALESCE(val_score, 50) + 0.10*COALESCE(s_score, 50))::numeric, 2),
                t_score, bq_score, val_score, s_score)
  FROM score_history
  WHERE NOT (bq_score IS NULL AND val_score IS NULL)
    AND ABS(composite - (0.25*COALESCE(t_score, 50) + 0.35*COALESCE(bq_score, 50)
                         + 0.30*COALESCE(val_score, 50) + 0.10*COALESCE(s_score, 50))) > 0.2
  UNION ALL
  SELECT 'E2', rec_date, ticker,
         format('%s composite=%s implied_sentiment=%s | t=%s bq=%s val=%s',
                rec_type, composite_score,
                ROUND(((composite_score - 0.25*t_score - 0.35*bq_score - 0.30*val_score) / 0.10)::numeric, 1),
                t_score, bq_score, val_score)
  FROM recommendations
  WHERE weights_version = 2 AND composite_score IS NOT NULL AND t_score IS NOT NULL
    AND bq_score IS NOT NULL AND val_score IS NOT NULL
    AND ((composite_score - 0.25*t_score - 0.35*bq_score - 0.30*val_score) / 0.10 < -0.5
         OR (composite_score - 0.25*t_score - 0.35*bq_score - 0.30*val_score) / 0.10 > 100.5)
  UNION ALL
  SELECT 'E3', (traded_at AT TIME ZONE 'America/New_York')::date, ticker,
         format('%s followed_signal=%s | signal_seen=%s', UPPER(action), followed_signal, LEFT(signal_seen, 80))
  FROM trades
  WHERE (UPPER(action) = 'SELL' AND LOWER(followed_signal) = 'yes'
         AND signal_seen ILIKE '%buy%' AND signal_seen NOT ILIKE '%sell%')
     OR (UPPER(action) = 'BUY' AND LOWER(followed_signal) = 'yes'
         AND signal_seen ILIKE '%sell%' AND signal_seen NOT ILIKE '%buy%')
) q
ORDER BY chk, d DESC NULLS FIRST, ticker;


-- ---------------------------------------------------------------------
-- Q2 — FOLLOW-UP to Q1 (2026-10-14). Three questions, one result set.
--   T1  add_winner on a WATCH name (ON 09-21/22, MU 09-14): timing.
--       The Brief code makes WATCH-and-add impossible within ONE run
--       (daily_briefing.py ~1908 `continue`), so if the rec was surfaced
--       BEFORE the WATCH/G-20 rows that day, it is an intraday transition,
--       not a gate leak. NOTE G-20 counterfactual=TRUE is the BINDING row;
--       FALSE is the pre-pass "recorded, non-binding" row.
--   P50 every exact-50 pillar with that ticker's previous/next value. A
--       fabricated 50 shows as a one-day jump against both neighbours. A
--       real lattice 50 sits among nearby values. s_score is joined to that
--       day's sentiment_history.headline_count (written at EOD, so an
--       approximation of the morning's news state).
--   BK  per-ticker book on the three C7 gap days, to match the missing
--       $736 / $739 / $1,065 to a ticker. written_et ~17:3x = cron; any
--       other time = the app's own daily_snapshots writer.
-- ---------------------------------------------------------------------
SELECT chk, d, ticker, detail FROM (
  SELECT 'T1' AS chk, r.rec_date AS d, r.ticker,
         format('rec %s surfaced %s | exit %s surfaced %s | G-20 %s cf=%s at %s',
                r.rec_type, to_char(r.surfaced_at AT TIME ZONE 'America/New_York', 'HH24:MI'),
                e.signal_type, to_char(e.surfaced_at AT TIME ZONE 'America/New_York', 'HH24:MI'),
                g.source, g.counterfactual, to_char(g.suppressed_at AT TIME ZONE 'America/New_York', 'HH24:MI')) AS detail
  FROM recommendations r
  JOIN exit_signals e ON e.ticker = r.ticker AND e.signal_date = r.rec_date
  LEFT JOIN gate_suppressions g ON g.ticker = r.ticker AND g.rec_date = r.rec_date AND g.gate_id = 'G-20'
  WHERE r.rec_type = 'add_winner'
  UNION ALL
  SELECT 'P50', s.score_date, s.ticker,
         format('t=%s (prev %s, next %s) | s=%s (prev %s, next %s, headlines %s) | bq=%s (prev %s, next %s) | composite=%s',
                s.t_score, s.t_prev, s.t_next, s.s_score, s.s_prev, s.s_next, sh.headline_count,
                s.bq_score, s.bq_prev, s.bq_next, s.composite)
  FROM (SELECT ticker, score_date, composite, t_score, s_score, bq_score,
               LAG(t_score)  OVER w AS t_prev,  LEAD(t_score)  OVER w AS t_next,
               LAG(s_score)  OVER w AS s_prev,  LEAD(s_score)  OVER w AS s_next,
               LAG(bq_score) OVER w AS bq_prev, LEAD(bq_score) OVER w AS bq_next
        FROM score_history WINDOW w AS (PARTITION BY ticker ORDER BY score_date)) s
  LEFT JOIN sentiment_history sh ON sh.ticker = s.ticker AND sh.snap_date = s.score_date
  WHERE s.t_score = 50 OR s.s_score = 50 OR s.bq_score = 50
  UNION ALL
  SELECT 'BK', snapshot_date, ticker,
         format('value=%s  shares=%s  close=%s  written_et=%s',
                ROUND(shares * close_price, 2), shares, close_price,
                to_char(created_at AT TIME ZONE 'America/New_York', 'MM-DD HH24:MI'))
  FROM daily_snapshots
  WHERE snapshot_date IN (DATE '2026-09-22', DATE '2026-09-29', DATE '2026-09-30')
) q
ORDER BY chk, d DESC, ticker;


-- =====================================================================
-- DETAIL BLOCKS — run only those whose Q0 row shows n_bad > 0.
-- =====================================================================

-- A1 — READ IT AS: any row is a fabricated or impossible reading. Rows
-- before 2026-10-13 (F-299 removed the avg_corr=0.0 fallback) are the
-- known legacy defect; a row ON/AFTER that date is a live regression.
SELECT snapshot_date, diversification_score, avg_pairwise_corr, corr_coverage_n
FROM portfolio_risk_snapshots
WHERE diversification_score >= 50 OR avg_pairwise_corr <= 0
ORDER BY snapshot_date;

-- A2 — READ IT AS: the schema promises NULL here ("never the 50"). Any
-- row means score_history's builder leaked a fabricated pillar.
SELECT score_date, ticker, composite, bq_available, bq_score, val_available, val_score
FROM score_history
WHERE (bq_available IS FALSE AND bq_score IS NOT NULL)
   OR (val_available IS FALSE AND val_score IS NOT NULL)
ORDER BY score_date DESC LIMIT 100;

-- A3 — READ IT AS: compare n_exact_50 with its neighbours. A real score
-- distribution has no spike at exactly 50; if n_exact_50 is many times
-- n_49_51_not_50 / 2, that pillar is emitting a default, not a reading.
-- t_score has NO availability flag (D3 in the score-history plan), so
-- a t_score spike is the one this table cannot otherwise disclose.
SELECT pillar,
       COUNT(*) FILTER (WHERE v = 50)                          AS n_exact_50,
       COUNT(*) FILTER (WHERE v BETWEEN 49 AND 51 AND v <> 50) AS n_49_51_not_50,
       COUNT(*)                                                AS n_total
FROM (SELECT 't_score' AS pillar, t_score AS v FROM score_history
      UNION ALL SELECT 's_score',  s_score  FROM score_history
      UNION ALL SELECT 'bq_score', bq_score FROM score_history
      UNION ALL SELECT 'val_score', val_score FROM score_history) z
WHERE v IS NOT NULL
GROUP BY pillar ORDER BY pillar;

-- A4 — READ IT AS: a composite that cleared the BUY bar while carrying a
-- fabricated 50 pillar. Cross-check whether any of these became a rec.
SELECT s.score_date, s.ticker, s.composite, s.bq_available, s.val_available,
       r.rec_type, r.composite_score AS rec_composite
FROM score_history s
LEFT JOIN recommendations r ON r.ticker = s.ticker AND r.rec_date = s.score_date
WHERE s.composite >= 65 AND (s.bq_available IS FALSE OR s.val_available IS FALSE)
ORDER BY s.score_date DESC LIMIT 100;

-- A5 — READ IT AS: 0 is a legitimate but rare technical reading. Hits
-- clustered on ETF tickers or one rec_type = the fabricated-badge class.
SELECT rec_type, ticker, rec_date, composite_score, momentum_score
FROM recommendations WHERE momentum_score = 0
ORDER BY rec_date DESC LIMIT 100;

-- A6 — READ IT AS: "no headlines" stored as a neutral 0 instead of NULL.
-- Downstream sentiment-vs-move analysis would read it as measured-neutral.
SELECT snap_date, ticker, vader_compound, vader_score, headline_count, bullish_pct, source
FROM sentiment_history
WHERE vader_compound = 0 AND COALESCE(headline_count, 0) = 0
ORDER BY snap_date DESC LIMIT 100;

-- B1 — READ IT AS: since F-291/F-292 the EOD write NULLs the score on an
-- unverified reading. Any row here after 2026-10-09 is a regression. The
-- second query shows how much PRE-fix history is unflagged (context only).
SELECT snapshot_date, diversification_score, avg_pairwise_corr, corr_coverage_n
FROM portfolio_risk_snapshots
WHERE snapshot_date >= DATE '2026-10-09' AND diversification_score IS NOT NULL
  AND (corr_coverage_n IS NULL OR corr_coverage_n < 20);
SELECT COUNT(*) AS legacy_scored_on_thin_sample
FROM portfolio_risk_snapshots
WHERE snapshot_date < DATE '2026-10-09' AND diversification_score IS NOT NULL
  AND (corr_coverage_n IS NULL OR corr_coverage_n < 20);

-- B2 — READ IT AS: schema says net_equity is "NULL unless cash fresh".
-- A row here persisted leverage/cushion computed from a cash balance the
-- writer should have refused. cash_age_days > 7 is the stale case.
SELECT snapshot_date, gross_book, cash_balance, net_equity, leverage, cash_as_of,
       snapshot_date - (cash_as_of AT TIME ZONE 'America/New_York')::date AS cash_age_days
FROM account_daily_snapshots
WHERE net_equity IS NOT NULL
  AND (cash_as_of IS NULL OR snapshot_date - (cash_as_of AT TIME ZONE 'America/New_York')::date > 7)
ORDER BY snapshot_date;

-- B3 — READ IT AS: a weekly record asserting diversification without a
-- verified risk reading on or before that date. Pre-2026-10-09 rows are
-- the known pre-F-291 legacy; later ones are a regression.
SELECT t.thesis_date, t.claims->>'correlation_structure' AS claim,
       r.snapshot_date AS risk_row_date, r.diversification_score, r.corr_coverage_n
FROM portfolio_thesis t
LEFT JOIN LATERAL (SELECT snapshot_date, diversification_score, corr_coverage_n FROM portfolio_risk_snapshots
                   WHERE snapshot_date <= t.thesis_date ORDER BY snapshot_date DESC LIMIT 1) r ON TRUE
WHERE t.claims->>'correlation_structure' = 'diversified'
ORDER BY t.thesis_date;

-- C1 — READ IT AS: the engine said buy/add AND exit/trim/watch on the
-- same name the same day. F-39i covers the deterioration whiplash case;
-- hits AFTER its ship date, or with signal_type EXIT/TRIM, are bugs.
SELECT r.rec_date, r.ticker, r.rec_type, r.composite_score AS rec_composite,
       e.signal_type, e.composite_score AS exit_composite
FROM recommendations r JOIN exit_signals e ON e.ticker = r.ticker AND e.signal_date = r.rec_date
WHERE r.rec_type IN ('new_pick', 'add_winner', 'enter_now')
ORDER BY r.rec_date DESC;

-- C2 — READ IT AS: G-25/G-26 (and G-01/G-24) suppress add_winner — a
-- (gate, add_winner) pair here is a direct contradiction. Other pairs
-- need judgment: an app-side and cron-side run hours apart can legitimately
-- see different data. Look at the `source` and `lane` columns.
SELECT g.rec_date, g.ticker, g.gate_id, g.source AS gate_source, g.lane, g.reason,
       r.rec_type, r.composite_score
FROM gate_suppressions g JOIN recommendations r ON r.ticker = g.ticker AND r.rec_date = g.rec_date
WHERE g.counterfactual IS NOT TRUE AND r.rec_type IN ('new_pick', 'add_winner', 'enter_now')
ORDER BY g.gate_id, g.rec_date DESC;

-- C3 / C4 — READ IT AS: same ticker, same day, two composites. Small
-- gaps are time-of-day price moves. A gap > 5 means two scoring paths
-- (or a fabricated pillar on one side) — check bq/val_available.
SELECT 'exit_signals' AS other, s.score_date, s.ticker, s.composite, e.composite_score AS other_composite,
       s.bq_available, s.val_available
FROM score_history s JOIN exit_signals e ON e.ticker = s.ticker AND e.signal_date = s.score_date
WHERE ABS(s.composite - e.composite_score) > 5
UNION ALL
SELECT 'add_winner', s.score_date, s.ticker, s.composite, r.composite_score, s.bq_available, s.val_available
FROM score_history s JOIN recommendations r ON r.ticker = s.ticker AND r.rec_date = s.score_date
WHERE r.rec_type = 'add_winner' AND ABS(s.composite - r.composite_score) > 5
ORDER BY 2 DESC;

-- C5 / C6 — READ IT AS: both values come from the SAME EOD run's
-- port_risk / risk row (rec_events_capture.py:224, :554). Any mismatch
-- means one side was recomputed or written from a different run.
SELECT e.rec_type, e.fired_date, e.ticker, e.metric_before, e.corr_coverage_n AS ev_cov,
       p.portfolio_beta, p.avg_pairwise_corr, p.corr_coverage_n AS snap_cov
FROM rec_events e JOIN portfolio_risk_snapshots p ON p.snapshot_date = e.fired_date
WHERE e.source = 'cron' AND (
      (e.rec_type = 'beta_trim' AND ((e.metric_before IS NULL) <> (p.portfolio_beta IS NULL)
                                     OR ABS(e.metric_before - p.portfolio_beta) > 0.01))
   OR (e.rec_type = 'diversify_add' AND ((e.metric_before IS NULL) <> (p.avg_pairwise_corr IS NULL)
                                     OR ABS(e.metric_before - p.avg_pairwise_corr) > 0.001
                                     OR e.corr_coverage_n IS DISTINCT FROM p.corr_coverage_n)))
ORDER BY e.fired_date DESC;

-- C7 — READ IT AS: since 2026-10-12 gross_book uses raw shares while
-- daily_snapshots stays truncated, so a gap of < ~$1-2 is expected (the
-- 10-13 log measured it). A gap of > $5 means the two writers saw
-- DIFFERENT ticker sets that day — run C8 for the same date.
SELECT a.snapshot_date, a.gross_book, ROUND(d.book, 2) AS snapshot_book,
       ROUND(a.gross_book - d.book, 2) AS gap, d.n_tickers
FROM account_daily_snapshots a
JOIN (SELECT snapshot_date, SUM(shares * close_price) AS book, COUNT(*) AS n_tickers
      FROM daily_snapshots GROUP BY 1) d ON d.snapshot_date = a.snapshot_date
WHERE ABS(a.gross_book - d.book) > 5
ORDER BY a.snapshot_date;

-- C8 — READ IT AS: a holding on day N-1 and N+1 but absent on day N was
-- almost certainly a failed price load recorded as if the position were
-- gone (which corrupts that day's P&L baseline). Rule out a real
-- sell-then-rebuy with the trades join (trade_on_gap_day non-null).
SELECT o.snapshot_date AS gap_day, p.ticker, o.prev_d, o.next_d,
       (SELECT STRING_AGG(t.action || ' ' || t.shares, ', ') FROM trades t
        WHERE t.ticker = p.ticker
          AND (t.traded_at AT TIME ZONE 'America/New_York')::date BETWEEN o.prev_d AND o.next_d) AS trade_on_gap_day
FROM (SELECT snapshot_date,
             LAG(snapshot_date)  OVER (ORDER BY snapshot_date) AS prev_d,
             LEAD(snapshot_date) OVER (ORDER BY snapshot_date) AS next_d
      FROM (SELECT DISTINCT snapshot_date FROM daily_snapshots) x) o
JOIN daily_snapshots p ON p.snapshot_date = o.prev_d
JOIN daily_snapshots n ON n.snapshot_date = o.next_d AND n.ticker = p.ticker
WHERE NOT EXISTS (SELECT 1 FROM daily_snapshots m WHERE m.snapshot_date = o.snapshot_date AND m.ticker = p.ticker)
ORDER BY gap_day DESC;

-- D1 / D2 — READ IT AS: arithmetic identities the writer is supposed to
-- guarantee. Any row is a writer bug or a hand-edited row.
SELECT 'risk' AS tbl, snapshot_date, diversification_score AS a, avg_pairwise_corr AS b, NULL::numeric AS c
FROM portfolio_risk_snapshots
WHERE (diversification_score IS NULL) <> (avg_pairwise_corr IS NULL)
   OR ABS(diversification_score - (1 - avg_pairwise_corr) / 2 * 100) > 0.2
UNION ALL
SELECT 'account', snapshot_date, net_equity, gross_book + cash_balance, leverage
FROM account_daily_snapshots
WHERE (net_equity IS NOT NULL AND ABS(net_equity - (gross_book + cash_balance)) > 0.01)
   OR (leverage IS NOT NULL AND (net_equity IS NULL OR net_equity <= 0 OR ABS(leverage - gross_book / net_equity) > 0.01))
ORDER BY 2;

-- D3 — READ IT AS: the naive-UTC-date bug class. EOD runs 17:30 ET, so
-- created_at's ET date should equal snapshot_date. A row stamped the
-- NEXT day (UTC already rolled over) or a re-run on a later day that
-- re-dated old data both show up here. Note created_at is first-insert
-- time; an upsert does not refresh it.
SELECT 'risk' AS tbl, snapshot_date, created_at, (created_at AT TIME ZONE 'America/New_York') AS created_et
FROM portfolio_risk_snapshots WHERE snapshot_date <> (created_at AT TIME ZONE 'America/New_York')::date
UNION ALL
SELECT 'account', snapshot_date, created_at, (created_at AT TIME ZONE 'America/New_York')
FROM account_daily_snapshots WHERE snapshot_date <> (created_at AT TIME ZONE 'America/New_York')::date
ORDER BY 2;

-- D4 — READ IT AS: cron-sourced rows on a closed day are bugs. App-
-- sourced rows on a weekend (owner opened the app Saturday) are a
-- question, not necessarily a bug. 2026-07-03 is included on purpose:
-- constants.py records it as an early close, but NYSE's own calendar
-- lists it as a FULL closure — any row dated that day was written
-- because the app believed the market was open.
SELECT tbl, d, EXTRACT(ISODOW FROM d) AS isodow, src, COUNT(*) AS n
FROM (SELECT 'recommendations' AS tbl, rec_date AS d, 'n/a' AS src FROM recommendations
      UNION ALL SELECT 'exit_signals', signal_date, 'n/a' FROM exit_signals
      UNION ALL SELECT 'score_history', score_date, source FROM score_history
      UNION ALL SELECT 'daily_snapshots', snapshot_date, 'n/a' FROM daily_snapshots
      UNION ALL SELECT 'portfolio_risk_snapshots', snapshot_date, 'cron' FROM portfolio_risk_snapshots
      UNION ALL SELECT 'account_daily_snapshots', snapshot_date, 'cron' FROM account_daily_snapshots
      UNION ALL SELECT 'rec_events', fired_date, source FROM rec_events
      UNION ALL SELECT 'gate_suppressions', rec_date, source FROM gate_suppressions) z
WHERE EXTRACT(ISODOW FROM d) IN (6, 7)
   OR d IN (DATE '2026-01-01', DATE '2026-01-19', DATE '2026-02-16', DATE '2026-04-03',
            DATE '2026-05-25', DATE '2026-06-19', DATE '2026-07-03', DATE '2026-09-07')
GROUP BY tbl, d, src ORDER BY d DESC, tbl;

-- D5 / D6 / D7 — READ IT AS: a BUY-type call that its own stored
-- numbers say should not exist. D7: F-249 caps sizing at 15% of the
-- book; enter_now rows are additionally capped at 25% of net capital,
-- which this table cannot check (net capital is not stored on the row).
SELECT rec_date, ticker, rec_type, composite_score, conviction, price_at_surface,
       rec_shares, rec_portfolio_value, rec_sizing_version,
       ROUND(rec_shares * price_at_surface / NULLIF(rec_portfolio_value, 0) * 100, 2) AS pct_of_book
FROM recommendations
WHERE (rec_type IN ('new_pick', 'add_winner', 'enter_now')
       AND (composite_score IS NULL OR composite_score < 65 OR price_at_surface IS NULL OR price_at_surface <= 0))
   OR (conviction = 'high' AND composite_score < 75)
   OR (rec_sizing_version >= 2 AND rec_shares > 0 AND price_at_surface > 0 AND rec_portfolio_value > 0
       AND rec_shares * price_at_surface / rec_portfolio_value * 100 > 15.05)
ORDER BY rec_date DESC LIMIT 200;

-- D8 / D9 / D10 — READ IT AS: field-level impossibilities.
SELECT 'model_predictions' AS tbl, id::text AS row_id, model_name AS a, predicted_low::text AS b,
       predicted_value::text AS c, predicted_high::text AS d, realized_value::text AS e, abs_error::text AS f
FROM model_predictions
WHERE predicted_low > predicted_value OR predicted_high < predicted_value
   OR (realized_value IS NOT NULL AND (abs_error IS NULL OR scored_at IS NULL
       OR ABS(abs_error - ABS(predicted_value - realized_value)) > 1e-6))
UNION ALL
SELECT 'analyst_coverage', id::text, ticker, low_pt::text, avg_pt::text, high_pt::text, article_date::text, NULL
FROM analyst_coverage
WHERE low_pt > avg_pt OR avg_pt > high_pt OR low_pt <= 0 OR avg_pt <= 0 OR high_pt <= 0
UNION ALL
SELECT 'trades', id::text, ticker, action, shares::text, price::text, realized_pnl::text, traded_at::text
FROM trades
WHERE (UPPER(action) = 'SELL' AND realized_pnl IS NULL)
   OR (UPPER(action) = 'BUY' AND realized_pnl IS NOT NULL)
   OR traded_at > now()
ORDER BY 1, 2;

-- H1 — cron heartbeat vs the rows it claims to have produced. One row.
-- READ IT AS: eod status 'ok' but any *_row column false = a silent
-- write failure behind a green heartbeat (the F-235 failure class).
SELECT h.lane, h.status, h.last_run_at,
       (h.last_run_at AT TIME ZONE 'America/New_York')::date AS run_day_et,
       EXISTS (SELECT 1 FROM portfolio_risk_snapshots WHERE snapshot_date = (h.last_run_at AT TIME ZONE 'America/New_York')::date) AS risk_row,
       EXISTS (SELECT 1 FROM account_daily_snapshots  WHERE snapshot_date = (h.last_run_at AT TIME ZONE 'America/New_York')::date) AS account_row,
       EXISTS (SELECT 1 FROM daily_snapshots          WHERE snapshot_date = (h.last_run_at AT TIME ZONE 'America/New_York')::date) AS snapshot_rows,
       h.detail
FROM cron_heartbeat h WHERE h.lane = 'eod';
