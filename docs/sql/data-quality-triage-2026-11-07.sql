-- Weekly data-quality triage for the Saturday 2026-11-07 email (bug register
-- DQ-C7, DQ-TSLA, DQ-Orphan). READ-ONLY: every statement is a SELECT.
-- Run each block alone in the Supabase SQL editor and paste the results back.

-- Q1. C7 (16 rows vs baseline 3): is each gap a day the N1 repair rewrote
-- account gross_book while the per-ticker daily_snapshots rows stayed stale?
-- Expect: the 3 baseline rows (2026-09-22/29/30) with repaired_at NULL, and
-- the 13 new rows with repaired_at set. A NEW row with repaired_at NULL
-- is unexplained and is the one that matters.
SELECT a.snapshot_date,
       a.gross_book,
       ROUND(d.book, 2)               AS snapshots_sum,
       ROUND(a.gross_book - d.book, 2) AS gap,
       a.original_gross_book,
       a.repaired_at,
       d.n_tickers,
       (SELECT COUNT(*) FROM jsonb_object_keys(a.book_positions)) AS n_book_positions
FROM account_daily_snapshots a
JOIN (SELECT snapshot_date, SUM(shares * close_price) AS book, COUNT(*) AS n_tickers
      FROM daily_snapshots GROUP BY 1) d ON d.snapshot_date = a.snapshot_date
WHERE ABS(a.gross_book - d.book) > 5
ORDER BY a.snapshot_date;

-- Q2. For the C7 gap days, which tickers differ between the account row's
-- book_positions (C4+ rows only) and daily_snapshots? Rows before C4
-- (2026-11-02) have no book_positions and return nothing here.
SELECT a.snapshot_date, k.ticker,
       (a.book_positions -> k.ticker ->> 'shares')::numeric AS book_shares,
       s.shares                                              AS snapshot_shares
FROM account_daily_snapshots a
CROSS JOIN LATERAL jsonb_object_keys(a.book_positions) AS k(ticker)
LEFT JOIN daily_snapshots s ON s.snapshot_date = a.snapshot_date AND s.ticker = k.ticker
WHERE a.book_positions IS NOT NULL
  AND (s.shares IS NULL
       OR ABS(s.shares - (a.book_positions -> k.ticker ->> 'shares')::numeric) >= 1)
ORDER BY a.snapshot_date, k.ticker;

-- Q3. The TSLA duplicate thesis review: when were the two rows written?
-- Seconds apart = a double-click; same Sunday evening = cron + manual;
-- a week or more apart = identical inputs re-reviewed (a legitimate repeat).
SELECT id, ticker, reviewed_at AT TIME ZONE 'America/New_York' AS reviewed_et,
       status, inputs_hash, LEFT(summary, 80) AS summary_start
FROM thesis_reviews
WHERE ticker = 'TSLA'
  AND inputs_hash IN (SELECT inputs_hash FROM thesis_reviews
                      WHERE ticker = 'TSLA' AND inputs_hash NOT LIKE 'earnings_%'
                      GROUP BY inputs_hash HAVING COUNT(*) > 1)
ORDER BY reviewed_at;

-- Q4. The orphan cache rows: how many rows and how old.
SELECT 'bundle_cache' AS tbl, ticker, COUNT(*) AS n, MAX(fetched_at)::date AS last_touch
FROM bundle_cache WHERE ticker = 'INTL' GROUP BY ticker
UNION ALL
SELECT 'sentiment_llm_cache', ticker, COUNT(*), MAX(score_date)::date
FROM sentiment_llm_cache WHERE ticker = 'INTL' GROUP BY ticker
UNION ALL
SELECT 'debate_cache', ticker, COUNT(*), MAX(debate_date)::date
FROM debate_cache WHERE ticker = 'DELL' GROUP BY ticker;
