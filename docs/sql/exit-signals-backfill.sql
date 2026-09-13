-- =====================================================================
-- DRISHTA — exit_signals price backfill (data-integrity finding D21/D23
-- follow-on). Plan: docs/plans/data-integrity.md
--
-- CONTEXT: 32 exit_signals rows (2026-07-18 -> 2026-08-04, before the
-- writer bug was fixed in commit bd48079 on 2026-08-05) have NULL
-- price_at_signal, dd_from_peak_pct, below_ma_count and rel_strength.
-- pnl_pct IS populated on every one of them, proving a price existed at
-- write time -- it just was not persisted into price_at_signal.
--
-- SCOPE DECISION: only price_at_signal is being backfilled.
-- protective_track_record.py (the sole consumer this unblocks) reads
-- ONLY price_at_signal -- verified by grep, it never touches
-- dd_from_peak_pct/below_ma_count/rel_strength. The one place
-- dd_from_peak_pct IS read (debrief_advisor.py, the WEEKLY debrief
-- narrative) is week-scoped and will never revisit rows this old by the
-- time you run this. Those three columns are being left NULL rather
-- than guessed at -- they need a full price-history recompute
-- (moving averages, SPY relative strength), not a single close price,
-- and this backfill does not attempt that.
--
-- HOW TO RUN: block by block, one at a time (the SQL editor only shows
-- the last result set). Block 1 is read-only diagnosis. Block 2 is the
-- actual UPDATE -- do not run it until you have looked at Block 1's
-- output and are comfortable with the match count and the specific
-- rows. Block 3 verifies afterward.
-- =====================================================================


-- ---------------------------------------------------------------------
-- Block 1 — DIAGNOSIS ONLY. Shows exactly which of the 32 unpriced rows
-- daily_snapshots can and cannot backfill, side by side. Nothing is
-- written by this block.
--
-- READ IT AS:
--   backfillable_close IS NOT NULL  -> Block 2 will fix this row.
--   backfillable_close IS NULL      -> daily_snapshots has no row for
--                                      this (ticker, date) -- the
--                                      position likely was not held (or
--                                      was already sold) that specific
--                                      day. These stay NULL; not fixable
--                                      without an external price-history
--                                      fetch, out of scope here.
-- ---------------------------------------------------------------------
SELECT
    es.id,
    es.ticker,
    es.signal_date,
    es.signal_type,
    es.pnl_pct,
    ds.close_price                                   AS backfillable_close,
    CASE WHEN ds.close_price IS NOT NULL THEN 'will be fixed'
         ELSE 'NOT in daily_snapshots -- stays NULL' END AS outcome
FROM exit_signals es
LEFT JOIN daily_snapshots ds
       ON ds.ticker = es.ticker
      AND ds.snapshot_date = es.signal_date
WHERE es.price_at_signal IS NULL
ORDER BY es.signal_date, es.ticker;


-- ---------------------------------------------------------------------
-- Block 1b — the same thing as one number, so you can decide whether
-- it's worth proceeding before looking at all 32 rows individually.
-- ---------------------------------------------------------------------
SELECT
    COUNT(*)                                                        AS total_unpriced,
    COUNT(ds.close_price)                                           AS backfillable_now,
    COUNT(*) - COUNT(ds.close_price)                                AS remains_null
FROM exit_signals es
LEFT JOIN daily_snapshots ds
       ON ds.ticker = es.ticker
      AND ds.snapshot_date = es.signal_date
WHERE es.price_at_signal IS NULL;


-- ---------------------------------------------------------------------
-- Block 2 — THE ACTUAL WRITE. Only run this after reviewing Block 1.
--
-- Sets price_at_signal to the EOD close daily_snapshots recorded for
-- that exact ticker on that exact signal_date. This is the correct
-- value for "price at signal" IF the signal fired at/after the close
-- computation (exit_advisor runs off the same EOD-ish data the premarket/
-- scan lanes use) -- it is a real historical close, not a guess, but it
-- is still a DAILY close standing in for "the price at the moment the
-- signal fired," same documented caveat as the analyst-price backfill
-- script this mirrors (scripts/backfill_analyst_prices.py).
--
-- Scoped tightly: only touches rows where price_at_signal IS currently
-- NULL and a matching daily_snapshots row exists. Cannot touch any
-- already-priced row (the WHERE clause structurally excludes them), and
-- cannot invent a price for a row daily_snapshots has no match for (the
-- subquery join means no match = no update, not a fabricated value).
-- ---------------------------------------------------------------------
UPDATE exit_signals es
SET price_at_signal = ds.close_price
FROM daily_snapshots ds
WHERE ds.ticker = es.ticker
  AND ds.snapshot_date = es.signal_date
  AND es.price_at_signal IS NULL;


-- ---------------------------------------------------------------------
-- Block 3 — VERIFY. Re-run the Block 1b count; total_unpriced should
-- have dropped by exactly however many Block 1 reported as
-- backfillable_now, and none of the "remains_null" rows should have
-- moved (they still have no daily_snapshots match).
-- ---------------------------------------------------------------------
SELECT
    COUNT(*)                                    AS still_unpriced,
    MIN(signal_date)                            AS oldest_still_unpriced,
    MAX(signal_date)                            AS newest_still_unpriced
FROM exit_signals
WHERE price_at_signal IS NULL;


-- =====================================================================
-- Block 4 — the 5 tickers left genuinely BLOCKED after Block 2 (AMD,
-- FSLR, ISRG, NOW, TEAM: confirmed via a separate query to have ZERO
-- priced EXIT/TRIM row anywhere in the table, so protective_track_record.py
-- can never grade them without this).
--
-- NEW FINDING while preparing this (D25, docs/plans/data-integrity.md):
-- FSLR/ISRG/NOW also carry rows dated 2026-07-18 (SATURDAY) and
-- 2026-07-19 (SUNDAY) -- the market was not open either day, so there
-- is no real close price for them. Any number written for those 6 rows
-- would be a DIFFERENT day's price mislabeled as this weekend's
-- price_at_signal -- the exact fabrication this whole effort exists to
-- prevent. They are NOT included below and stay NULL.
--
-- Mechanism CONFIRMED, not guessed: the commit that first shipped this
-- capture (f86147d) was itself made Saturday 2026-07-18 12:09 ET, and
-- its code already used _today_et() correctly (no naive-UTC bug). The
-- real cause is that signal_date records the CALENDAR DAY THE SESSION
-- RAN, not the trading day the underlying price data reflects -- the
-- owner tested the brand-new feature that first weekend, and there is
-- no weekday guard on the interactive write path today. Still live and
-- reachable, not a historical-only artifact; tracked as its own item
-- (P3 -- corrupts one historical-analysis column only, no live gate).
--
-- The 8 rows below all fall on confirmed real NYSE trading days. Prices
-- were fetched via THIS repo's own multi-source failover
-- (providers.orchestrator.get_historical_close -- yfinance -> FMP, the
-- same chain scripts/backfill_analyst_prices.py uses), and the exact
-- date-to-close mapping was visually confirmed against a wider dated
-- window before writing (yfinance's `end` is EXCLUSIVE, so a same-day
-- start=end window returns nothing -- not "no data", a request bug in
-- the first attempt, corrected here). Two rows can share one fetched
-- price when they share a (ticker, date) -- an EXIT and a WATCH row
-- logged the same day have the same real close, not two different
-- values invented independently.
--
--   id=147  FSLR  2026-07-24 (WATCH)  -> 202.82
--   id=151  FSLR  2026-07-24 (EXIT)   -> 202.82
--   id=28   ISRG  2026-07-20 (EXIT)   -> 353.17
--   id=79   NOW   2026-07-22 (WATCH)  -> 95.46
--   id=95   NOW   2026-07-22 (EXIT)   -> 95.46
--   id=108  TEAM  2026-07-23 (WATCH)  -> 80.15
--   id=120  TEAM  2026-07-23 (EXIT)   -> 80.15
--   id=160  AMD   2026-07-28 (EXIT)   -> 454.62
--
-- The WHERE clause is scoped to these exact ids AND price_at_signal IS
-- NULL, so this cannot touch any other row or overwrite an already-
-- priced one even if run twice.
-- =====================================================================
UPDATE exit_signals
SET price_at_signal = CASE id
    WHEN 147 THEN 202.82
    WHEN 151 THEN 202.82
    WHEN 28  THEN 353.17
    WHEN 79  THEN 95.46
    WHEN 95  THEN 95.46
    WHEN 108 THEN 80.15
    WHEN 120 THEN 80.15
    WHEN 160 THEN 454.62
END
WHERE id IN (147, 151, 28, 79, 95, 108, 120, 160)
  AND price_at_signal IS NULL;


-- ---------------------------------------------------------------------
-- Block 5 — final verify. RUN 2026-09-13, 11 rows remained, all
-- accounted for (corrects this comment's earlier "exactly 6" claim,
-- which undercounted):
--   - 6 genuinely un-fixable: FSLR/ISRG/NOW's 07-18 (Sat) and 07-19
--     (Sun) rows -- no real close exists for either day.
--   - 5 that were already known not to matter: LLY (both rows) and MU
--     and TSLA's single rows are each superseded by a LATER priced
--     EXIT/TRIM for the same ticker elsewhere in the table (confirmed
--     by a separate per-ticker check before Block 4 ran), so they were
--     never blocking protective_track_record.py's grading regardless of
--     this backfill; PLTR's remaining row is WATCH-type, which that
--     function drops entirely (line 90), so its price was never going
--     to matter either.
-- Net: 32 unpriced -> 21 fixed (13 via Block 2, 8 via Block 4), 11
-- remain, none of them blocking anything.
-- ---------------------------------------------------------------------
SELECT id, ticker, signal_date, signal_type
FROM exit_signals
WHERE price_at_signal IS NULL
ORDER BY ticker, signal_date;
