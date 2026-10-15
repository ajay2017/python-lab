-- =====================================================================
-- DRISHTA — Impossible-States CLEANUP (2026-10-14)
-- Companion to docs/sql/impossible-states-audit.sql. Owner-approved
-- deletion of rows that audit proved incorrect.
--
-- RUN ORDER — after BOTH fix commits are live on Railway, or new bad rows
-- keep arriving:
--   * write_guards commit  (weekend/holiday writes, snapshot sweep)
--   * sentiment + calendar commit (cron sentiment writer, 2026-07-03)
--
-- STEP 1  Run the preview. It returns every row that STEP 2 will delete
--         or modify. Download it as CSV — that file IS the backup (no
--         backup tables are created: a table made in the SQL editor
--         starts with RLS off, which this project never allows).
--         Expected counts from the 2026-10-14 audit, with today's rows
--         possibly adding a few:
--           recommendations   ~231 (155 buy_candidate, 53 new_pick,
--                                   21 enter_now, 2 add_winner)
--           gate_suppressions  ~64
--           exit_signals       ~15
--           daily_snapshots      8 (all 2026-07-03)
--           sentiment_history  ~170 (VADER 0 with no headlines)
--         If a count is far off, STOP and paste the preview back.
-- STEP 2  Run the BEGIN … COMMIT block as one script. Each statement
--         reports its own row count; they should match STEP 1.
--
-- The closed-day list is the 2026 NYSE full closures, INCLUDING
-- 2026-07-03 (nyse.com lists it as a full closure; constants.py wrongly
-- had it as an early close until the calendar fix). Every affected row
-- falls in 2026 (earliest 2026-06-19), so no other year is needed.
--
-- 2026-07-03 in OTHER tables: the audit's Q1 holiday check (which includes
-- 07-03) also covered account_daily_snapshots, portfolio_risk_snapshots,
-- score_history and rec_events -- only daily_snapshots had a 07-03 row
-- (those four tables began recording in Aug/Sep 2026).
--
-- EXPECT A SHIFT in Self Track Record afterwards: its SELL-side match looks
-- for an engine signal shortly before a sale, so a Monday sell that matched
-- only a weekend-dated exit signal moves from engine-called to self-
-- initiated. That is the correct reading -- the weekend signal described
-- no session -- but the Self-vs-Engine figures will move.
-- =====================================================================


-- ---------------------------------------------------------------------
-- STEP 1 — PREVIEW (read-only). Download the result as CSV before STEP 2.
-- ---------------------------------------------------------------------
WITH closed(d) AS (
  VALUES (DATE '2026-01-01'), (DATE '2026-01-19'), (DATE '2026-02-16'), (DATE '2026-04-03'),
         (DATE '2026-05-25'), (DATE '2026-06-19'), (DATE '2026-07-03'), (DATE '2026-09-07'),
         (DATE '2026-11-26'), (DATE '2026-12-25')
)
SELECT 'recommendations' AS tbl, 'delete' AS action, rec_date AS d, ticker,
       format('rec_type=%s composite=%s price=%s', rec_type, composite_score, price_at_surface) AS detail
FROM recommendations
WHERE EXTRACT(ISODOW FROM rec_date) IN (6, 7) OR rec_date IN (SELECT d FROM closed)
UNION ALL
SELECT 'gate_suppressions', 'delete', rec_date, ticker,
       format('gate=%s source=%s counterfactual=%s', gate_id, source, counterfactual)
FROM gate_suppressions
WHERE EXTRACT(ISODOW FROM rec_date) IN (6, 7) OR rec_date IN (SELECT d FROM closed)
UNION ALL
SELECT 'exit_signals', 'delete', signal_date, ticker,
       format('signal_type=%s composite=%s', signal_type, composite_score)
FROM exit_signals
WHERE EXTRACT(ISODOW FROM signal_date) IN (6, 7) OR signal_date IN (SELECT d FROM closed)
UNION ALL
SELECT 'daily_snapshots', 'delete', snapshot_date, ticker,
       format('shares=%s close=%s', shares, close_price)
FROM daily_snapshots
WHERE EXTRACT(ISODOW FROM snapshot_date) IN (6, 7) OR snapshot_date IN (SELECT d FROM closed)
UNION ALL
SELECT 'sentiment_history',
       CASE WHEN bullish_pct IS NULL AND bearish_pct IS NULL AND buzz_score IS NULL
                 AND company_score IS NULL THEN 'delete' ELSE 'null_vader' END,
       snap_date, ticker,
       format('vader=%s vader_score=%s headlines=%s bullish=%s bearish=%s buzz=%s company=%s',
              vader_compound, vader_score, headline_count, bullish_pct, bearish_pct,
              buzz_score, company_score)
FROM sentiment_history
WHERE vader_compound = 0 AND COALESCE(headline_count, 0) = 0
ORDER BY tbl, d, ticker;


-- ---------------------------------------------------------------------
-- STEP 1b — PREVIEW COUNTS (read-only), to compare with STEP 2's output.
-- ---------------------------------------------------------------------
WITH closed(d) AS (
  VALUES (DATE '2026-01-01'), (DATE '2026-01-19'), (DATE '2026-02-16'), (DATE '2026-04-03'),
         (DATE '2026-05-25'), (DATE '2026-06-19'), (DATE '2026-07-03'), (DATE '2026-09-07'),
         (DATE '2026-11-26'), (DATE '2026-12-25')
)
SELECT 'recommendations' AS tbl, COUNT(*) AS n FROM recommendations
  WHERE EXTRACT(ISODOW FROM rec_date) IN (6, 7) OR rec_date IN (SELECT d FROM closed)
UNION ALL SELECT 'gate_suppressions', COUNT(*) FROM gate_suppressions
  WHERE EXTRACT(ISODOW FROM rec_date) IN (6, 7) OR rec_date IN (SELECT d FROM closed)
UNION ALL SELECT 'exit_signals', COUNT(*) FROM exit_signals
  WHERE EXTRACT(ISODOW FROM signal_date) IN (6, 7) OR signal_date IN (SELECT d FROM closed)
UNION ALL SELECT 'daily_snapshots', COUNT(*) FROM daily_snapshots
  WHERE EXTRACT(ISODOW FROM snapshot_date) IN (6, 7) OR snapshot_date IN (SELECT d FROM closed)
UNION ALL SELECT 'sentiment_history (delete)', COUNT(*) FROM sentiment_history
  WHERE vader_compound = 0 AND COALESCE(headline_count, 0) = 0
    AND bullish_pct IS NULL AND bearish_pct IS NULL AND buzz_score IS NULL AND company_score IS NULL
UNION ALL SELECT 'sentiment_history (null vader)', COUNT(*) FROM sentiment_history
  WHERE vader_compound = 0 AND COALESCE(headline_count, 0) = 0
    AND NOT (bullish_pct IS NULL AND bearish_pct IS NULL AND buzz_score IS NULL AND company_score IS NULL);


-- ---------------------------------------------------------------------
-- STEP 2 — DELETE / FIX. Run as one script, only after STEP 1's CSV is
-- saved. Wrapped in a transaction: any error rolls back all of it.
--
-- sentiment_history: a row whose ONLY reading was the fabricated VADER
-- neutral (no Finnhub field at all) is deleted -- db.save_sentiment_snapshot
-- requires vader_compound or bullish_pct, so it could not exist without it.
-- A row carrying ANY Finnhub field keeps it; only the fabricated VADER
-- fields are set to NULL -- the value the fixed writer now records.
-- ---------------------------------------------------------------------
BEGIN;

CREATE TEMP TABLE _closed(d date) ON COMMIT DROP;
INSERT INTO _closed VALUES
  ('2026-01-01'), ('2026-01-19'), ('2026-02-16'), ('2026-04-03'), ('2026-05-25'),
  ('2026-06-19'), ('2026-07-03'), ('2026-09-07'), ('2026-11-26'), ('2026-12-25');

DELETE FROM recommendations
WHERE EXTRACT(ISODOW FROM rec_date) IN (6, 7) OR rec_date IN (SELECT d FROM _closed);

DELETE FROM gate_suppressions
WHERE EXTRACT(ISODOW FROM rec_date) IN (6, 7) OR rec_date IN (SELECT d FROM _closed);

DELETE FROM exit_signals
WHERE EXTRACT(ISODOW FROM signal_date) IN (6, 7) OR signal_date IN (SELECT d FROM _closed);

DELETE FROM daily_snapshots
WHERE EXTRACT(ISODOW FROM snapshot_date) IN (6, 7) OR snapshot_date IN (SELECT d FROM _closed);

-- Order matters: UPDATE first (rows keeping a real Finnhub reading), then
-- DELETE the rows with no reading left. After the UPDATE, those kept rows
-- no longer match vader_compound = 0, so the DELETE cannot touch them.
UPDATE sentiment_history SET vader_compound = NULL, vader_score = NULL
WHERE vader_compound = 0 AND COALESCE(headline_count, 0) = 0
  AND NOT (bullish_pct IS NULL AND bearish_pct IS NULL AND buzz_score IS NULL AND company_score IS NULL);

DELETE FROM sentiment_history
WHERE vader_compound = 0 AND COALESCE(headline_count, 0) = 0
  AND bullish_pct IS NULL AND bearish_pct IS NULL AND buzz_score IS NULL AND company_score IS NULL;

COMMIT;


-- ---------------------------------------------------------------------
-- STEP 3 — VERIFY. Re-run STEP 1b: every count should now be 0.
-- Then re-run Q0 in impossible-states-audit.sql: A6 and D4 should read
-- n_bad = 0.
-- ---------------------------------------------------------------------
