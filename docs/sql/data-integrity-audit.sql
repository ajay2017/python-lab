-- =====================================================================
-- DRISHTA — Data Integrity Audit · Step 0 verification pack
-- Plan: docs/plans/data-integrity.md   ·   Written 2026-09-13
--
-- READ-ONLY. Every statement is a SELECT. Nothing writes, updates or
-- deletes. Safe to run against production.
--
-- HOW TO RUN: the Supabase SQL editor returns only the LAST result set,
-- so run these ONE BLOCK AT A TIME and paste each result back.
-- Every column name here was transcribed from docs/architecture.md §6.x.
--
-- If a query errors with "column ... does not exist", that is itself a
-- finding (an additive ALTER TABLE was never applied) — paste the error.
-- =====================================================================


-- ---------------------------------------------------------------------
-- Q1a — D3: how often does the app fall back to a STALE bundle?
--
-- bundle_cache is write-through on every SUCCESSFUL live fetch, so
-- fetched_at = "when this ticker last loaded cleanly". Rows aging past
-- BUNDLE_CACHE_MAX_AGE_DAYS = 5 are the ones that would be served stale
-- (and would inject the fabricated neutral-50 sentiment pillar).
--
-- READ IT AS: older_than_1d near 0 => the live path is healthy and D3 is
-- hygiene. A large older_than_2d/5d => D3 is the top item in the plan.
-- ---------------------------------------------------------------------
SELECT
    COUNT(*)                                                            AS tickers_cached,
    MIN(fetched_at)                                                     AS oldest_fetch,
    MAX(fetched_at)                                                     AS newest_fetch,
    COUNT(*) FILTER (WHERE fetched_at < now() - INTERVAL '1 day')       AS older_than_1d,
    COUNT(*) FILTER (WHERE fetched_at < now() - INTERVAL '2 days')      AS older_than_2d,
    COUNT(*) FILTER (WHERE fetched_at < now() - INTERVAL '5 days')      AS older_than_5d_would_expire
FROM bundle_cache;


-- ---------------------------------------------------------------------
-- Q1a-2 — D3, THE SHARP VERSION. Supersedes Q1a for ranking purposes.
--
-- Q1a is too coarse: bundle_cache holds every ticker ever loaded (scan
-- universe, discovery, watchlist, one-off Analysis lookups), so an old
-- fetched_at conflates "the live fetch FAILED" with "nobody REQUESTED
-- this ticker recently". Only the first is the D3 path.
--
-- Held tickers remove the ambiguity: 🏠 Home reloads all of them on every
-- render, so if you have opened the app today and a HELD ticker's bundle
-- is still stale, the live fetch genuinely failed for it.
--
-- READ IT AS: age_days < 1 on every row => the live path is healthy for
-- the book that actually gets scored, and D3 is latent/hygiene.
-- Any held row with age_days > 2 => that position was scored today on a
-- fabricated neutral sentiment pillar. NULL fetched_at => never cached.
-- ---------------------------------------------------------------------
SELECT
    h.ticker,
    h.shares,
    b.fetched_at,
    ROUND((EXTRACT(EPOCH FROM (now() - b.fetched_at)) / 86400.0)::numeric, 2) AS age_days,
    CASE
        WHEN b.fetched_at IS NULL                            THEN 'never cached'
        WHEN b.fetched_at > now() - INTERVAL '1 day'         THEN 'fresh'
        WHEN b.fetched_at > now() - INTERVAL '2 days'        THEN 'aging'
        WHEN b.fetched_at > now() - INTERVAL '5 days'        THEN 'STALE - served with fabricated sentiment'
        ELSE                                                      'EXPIRED - would fail to load'
    END                                                                      AS verdict
FROM holdings h
LEFT JOIN bundle_cache b ON b.ticker = h.ticker
ORDER BY b.fetched_at NULLS FIRST;


-- ---------------------------------------------------------------------
-- Q1b — D3: the same question for the fundamentals leg (HELD ONLY).
-- GROW_TODAY_MAX_FUND_AGE_DAYS = 2 (the trust threshold),
-- FUNDAMENTALS_CACHE_MAX_AGE_DAYS = 7 (the hard expiry).
--
-- SCHEMA NOTE (finding D17, 2026-09-13): docs/architecture.md §6.38
-- misdocuments this table -- it claims an `updated_at TIMESTAMPTZ` column
-- (does not exist in production), types `fetched_at` as TEXT (it is
-- TIMESTAMPTZ), and shows `financials` as nullable (it is NOT NULL). The
-- authoritative DDL is db.py's header, lines 283-287. An earlier version
-- of this query was written from §6.38 and failed with
-- 42703 column "updated_at" does not exist. Grade on fetched_at.
--
-- Write-through fires only when the LIVE .info leg returned at least
-- FUNDAMENTALS_GATE_MIN_METRICS = 1 core metric (bundle_loader.py:95-96).
-- So a fresh fetched_at means live fundamentals worked for that ticker;
-- a stale one means it has been sparse since, and the cache is carrying
-- the bq/val pillars.
-- ---------------------------------------------------------------------
SELECT
    h.ticker,
    h.shares,
    f.fetched_at,
    ROUND((EXTRACT(EPOCH FROM (now() - f.fetched_at)) / 86400.0)::numeric, 2) AS age_days,
    CASE
        WHEN f.fetched_at IS NULL                       THEN 'never cached'
        WHEN f.fetched_at > now() - INTERVAL '2 days'   THEN 'fresh - within trust threshold'
        WHEN f.fetched_at > now() - INTERVAL '7 days'   THEN 'AGED - composite untrustworthy per daily_briefing/trustworthy_composite'
        ELSE                                                 'EXPIRED - cache refused; bq/val pillars unavailable'
    END                                                                      AS verdict
FROM holdings h
LEFT JOIN fundamentals_cache f ON f.ticker = h.ticker
ORDER BY f.fetched_at NULLS FIRST;


-- ---------------------------------------------------------------------
-- Q1c — D3 DEPTH (not recency): fresh != complete.
--
-- Q1a-2/Q1b proved the cached data is CURRENT. This asks whether it is
-- COMPLETE, which is a different failure mode and the one the thresholds
-- are weakest on.
--
-- Two things measured at once:
--
-- (1) BQ pillar. FUNDAMENTALS_GATE_MIN_METRICS = 1, so a ticker passes as
--     "fundamentals available" on ONE of four CORE_BQ_KEYS
--     (fundamentals.py:14-17, counted as `is not None` at :21-24).
--     At <= 1 present, business_quality_score attaches a
--     "⚠ Data Quality: N/4 core BQ metrics unavailable ... may be
--     unreliable" signal (:244-249) that NO GATE READS.
--
-- (2) Valuation pillar renormalisation. valuation_score awards
--     forward_pe 25 pts (:44-46, requires pe > 0) and fcf_yield 20 pts
--     (:64-66); PT-upside 25 and consensus 30 come from analyst coverage,
--     not from `financials`. max_points is summed over AVAILABLE metrics
--     only (:111-112). So:
--       both present  -> analyst share = 55/100 of the pillar
--                        = 16.5% of the composite (0.30 weight)
--       both absent   -> analyst share = 55/55
--                        = 30% of the composite, silently doubled
--     This is the open "Analyst weight audit Phase B" item in CLAUDE.md,
--     measured against the real book for the first time.
--
-- READ IT AS: worst rows sort first. bq_present_of_4 <= 1 => that
-- holding's BQ pillar is near-fabricated but still passes the gate.
-- forward_pe AND fcf_yield both NULL => that holding's composite is 30%
-- sell-side opinion, not the documented 16.5%.
-- ---------------------------------------------------------------------
SELECT
    h.ticker,
    (  CASE WHEN f.financials ->> 'revenue_growth'  IS NOT NULL THEN 1 ELSE 0 END
     + CASE WHEN f.financials ->> 'earnings_growth' IS NOT NULL THEN 1 ELSE 0 END
     + CASE WHEN f.financials ->> 'profit_margins'  IS NOT NULL THEN 1 ELSE 0 END
     + CASE WHEN f.financials ->> 'debt_to_equity'  IS NOT NULL THEN 1 ELSE 0 END
    )                                               AS bq_present_of_4,
    f.financials ->> 'forward_pe'                   AS forward_pe,
    f.financials ->> 'fcf_yield'                    AS fcf_yield,
    CASE
        WHEN f.financials IS NULL                          THEN 'no cache row'
        WHEN f.financials ->> 'forward_pe' IS NULL
         AND f.financials ->> 'fcf_yield'  IS NULL         THEN 'ANALYST WEIGHT DOUBLED to ~30% of composite'
        WHEN f.financials ->> 'forward_pe' IS NULL
          OR f.financials ->> 'fcf_yield'  IS NULL         THEN 'partial - analyst weight elevated'
        ELSE                                                    'normal - analyst weight ~16.5%'
    END                                             AS valuation_basis
FROM holdings h
LEFT JOIN fundamentals_cache f ON f.ticker = h.ticker
ORDER BY bq_present_of_4 ASC, h.ticker;


-- ---------------------------------------------------------------------
-- Q2a — D8: is save_recommendations' "last-resort floor" silently
-- dropping score columns? Those rows are later read AS EVIDENCE by the
-- scorecard / track-record features.
--
-- READ IT AS: null_composite > 0 on new_pick/add_winner/enter_now rows
-- is the defect. buy_candidate rows legitimately carry less.
-- ---------------------------------------------------------------------
SELECT
    rec_type,
    COUNT(*)                                                AS rows,
    COUNT(*) FILTER (WHERE composite_score  IS NULL)        AS null_composite,
    COUNT(*) FILTER (WHERE t_score          IS NULL)        AS null_t,
    COUNT(*) FILTER (WHERE bq_score         IS NULL)        AS null_bq,
    COUNT(*) FILTER (WHERE val_score        IS NULL)        AS null_val,
    COUNT(*) FILTER (WHERE price_at_surface IS NULL)        AS null_price_at_surface,
    MIN(rec_date)                                           AS first_date,
    MAX(rec_date)                                           AS last_date
FROM recommendations
GROUP BY rec_type
ORDER BY rows DESC;


-- ---------------------------------------------------------------------
-- Q2b — D8 context: WHICH writer wins the daily race?
-- architecture.md §6.12 claims the interactive session usually wins
-- (measured 2026-08-23). This re-checks that it still holds, in ET.
-- Cron lanes fire at fixed hours; interactive writes cluster around
-- when you actually open the app.
-- ---------------------------------------------------------------------
SELECT
    EXTRACT(HOUR FROM surfaced_at AT TIME ZONE 'America/New_York')::int  AS et_hour,
    COUNT(*)                                                            AS rows
FROM recommendations
GROUP BY et_hour
ORDER BY et_hour;


-- ---------------------------------------------------------------------
-- Q3 — D9: analyst_coverage has NO dedup key (append-only insert).
-- A re-run double-counts, which inflates the Research Scorecard's n --
-- the number behind the CNBC Pro renewal decision.
--
-- READ IT AS: excess_rows is how many rows are duplicates. If it is 0,
-- D9 is latent (no re-run has happened yet) and drops to P3.
-- ---------------------------------------------------------------------
SELECT
    (SELECT COUNT(*) FROM analyst_coverage)                             AS total_rows,
    (SELECT COUNT(*) FROM (
        SELECT DISTINCT ticker, article_date, source FROM analyst_coverage
     ) d)                                                               AS distinct_article_keys,
    (SELECT COUNT(*) FROM analyst_coverage)
      - (SELECT COUNT(*) FROM (
            SELECT DISTINCT ticker, article_date, source FROM analyst_coverage
         ) d2)                                                          AS excess_rows;


-- ---------------------------------------------------------------------
-- Q3b — D9: are the Q3 "excess rows" TRUE duplicates, or false
-- positives from too loose a key?
--
-- (ticker, article_date, source) would also group two GENUINELY DIFFERENT
-- articles about the same ticker published the same day by the same
-- outlet. That is a plausible false positive, so test it rather than
-- assume: hash raw_text, which is the original pasted article.
--
-- READ IT AS:
--   distinct_raw_text = 1        -> TRUE duplicate, the same article
--                                   stored twice. D9 confirmed.
--   distinct_raw_text = copies   -> genuinely distinct articles. My key
--                                   was too loose; D9 is a false alarm.
--   saved_apart of seconds       -> a double-submit.
--   saved_apart of days/weeks    -> a deliberate re-paste, or a lane re-run.
-- ---------------------------------------------------------------------
SELECT
    ticker,
    article_date,
    source,
    COUNT(*)                                            AS copies,
    COUNT(DISTINCT md5(COALESCE(raw_text, '')))         AS distinct_raw_text,
    COUNT(DISTINCT consensus_rating)                    AS distinct_consensus,
    COUNT(DISTINCT avg_pt)                              AS distinct_avg_pt,
    MIN(created_at)                                     AS first_saved,
    MAX(created_at)                                     AS last_saved,
    MAX(created_at) - MIN(created_at)                   AS saved_apart
FROM analyst_coverage
GROUP BY ticker, article_date, source
HAVING COUNT(*) > 1
ORDER BY ticker;


-- ---------------------------------------------------------------------
-- Q4 — D7: is exit_signals' coalesce-on-write re-persisting STALE
-- values as if current? The tell is a ticker whose measured values never
-- change across many dates while its price DOES.
--
-- READ IT AS: distinct_value_sets = 1 with rows >= 5 AND
-- distinct_prices > 1  =>  frozen metrics against a moving price.
-- That is the defect. If distinct_value_sets tracks rows, it is healthy.
-- ---------------------------------------------------------------------
SELECT
    ticker,
    signal_type,
    COUNT(*)                                                            AS rows,
    COUNT(DISTINCT (dd_from_peak_pct, pnl_pct, below_ma_count,
                    rel_strength, composite_score))                     AS distinct_value_sets,
    COUNT(DISTINCT price_at_signal)                                     AS distinct_prices,
    MIN(signal_date)                                                    AS first_date,
    MAX(signal_date)                                                    AS last_date
FROM exit_signals
GROUP BY ticker, signal_type
HAVING COUNT(*) >= 3
ORDER BY rows DESC
LIMIT 40;


-- ---------------------------------------------------------------------
-- Q5 — D11: is the price cross-check actually catching anything?
-- There is no prev_ok column, so the strict prev-close leg is derived
-- against DATA_XCHECK_PREVCLOSE_TOL_PCT = 0.5 and the loose live leg
-- against DATA_XCHECK_LIVE_TOL_PCT = 3.0.
--
-- READ IT AS: prev_leg_breaches > 0 means real cross-source
-- disagreement has occurred -- and since the check is display-only,
-- nothing was withheld when it did. That promotes D11.
-- ---------------------------------------------------------------------
SELECT
    COUNT(*)                                                                    AS total_checks,
    COUNT(DISTINCT check_date)                                                  AS days_covered,
    MIN(check_date)                                                             AS first_day,
    MAX(check_date)                                                             AS last_day,
    COUNT(*) FILTER (WHERE ok IS FALSE)                                         AS failed_overall,
    COUNT(*) FILTER (WHERE prev_gap_pct IS NOT NULL
                       AND ABS(prev_gap_pct) > 0.5)                             AS prev_leg_breaches,
    COUNT(*) FILTER (WHERE live_gap_pct IS NOT NULL
                       AND ABS(live_gap_pct) > 3.0)                             AS live_leg_breaches
FROM price_xcheck_history;


-- ---------------------------------------------------------------------
-- Q5b — D11: which tickers disagree most often?
-- ---------------------------------------------------------------------
SELECT
    ticker,
    COUNT(*)                                                    AS checks,
    COUNT(*) FILTER (WHERE ok IS FALSE)                         AS failed,
    ROUND(MAX(ABS(prev_gap_pct))::numeric, 3)                   AS worst_prev_gap_pct,
    ROUND(MAX(ABS(live_gap_pct))::numeric, 3)                   AS worst_live_gap_pct,
    MAX(check_date)                                             AS last_checked
FROM price_xcheck_history
GROUP BY ticker
HAVING COUNT(*) FILTER (WHERE ok IS FALSE) > 0
ORDER BY failed DESC, worst_prev_gap_pct DESC
LIMIT 30;


-- ---------------------------------------------------------------------
-- Q6a — D10: account_flows duplicates inflate Net Contributed Capital.
-- db.py:827-836 records that pre-fix NULL-txn duplicates were never
-- collapsed. This sizes the damage. (Display-only per §6.8 -- a wrong
-- number you read, not a wrong decision.)
-- ---------------------------------------------------------------------
SELECT
    flow_date,
    flow_type,
    amount,
    COUNT(*)            AS copies,
    MIN(created_at)     AS first_saved,
    MAX(created_at)     AS last_saved
FROM account_flows
GROUP BY flow_date, flow_type, amount
HAVING COUNT(*) > 1
ORDER BY flow_date DESC;


-- ---------------------------------------------------------------------
-- Q6b — D10: the NCC figure as currently computed, so we can see how
-- much of it is duplicate. Compare against the same sum over DISTINCT
-- (flow_date, flow_type, amount).
-- ---------------------------------------------------------------------
WITH raw AS (
    SELECT
        COALESCE(SUM(amount) FILTER (WHERE flow_type = 'baseline'),   0) AS baseline,
        COALESCE(SUM(amount) FILTER (WHERE flow_type = 'deposit'),    0) AS deposits,
        COALESCE(SUM(amount) FILTER (WHERE flow_type = 'withdrawal'), 0) AS withdrawals
    FROM account_flows
),
deduped AS (
    SELECT
        COALESCE(SUM(amount) FILTER (WHERE flow_type = 'baseline'),   0) AS baseline,
        COALESCE(SUM(amount) FILTER (WHERE flow_type = 'deposit'),    0) AS deposits,
        COALESCE(SUM(amount) FILTER (WHERE flow_type = 'withdrawal'), 0) AS withdrawals
    FROM (SELECT DISTINCT flow_date, flow_type, amount FROM account_flows) d
)
SELECT
    raw.baseline + raw.deposits - raw.withdrawals            AS ncc_as_computed_today,
    deduped.baseline + deduped.deposits - deduped.withdrawals AS ncc_if_deduped,
    (raw.baseline + raw.deposits - raw.withdrawals)
      - (deduped.baseline + deduped.deposits - deduped.withdrawals) AS overstatement
FROM raw, deduped;


-- ---------------------------------------------------------------------
-- Q7 — D14: orphan rows (no foreign keys exist anywhere in the schema).
--
-- READ IT CAREFULLY -- not all of these are defects:
--   * manual_stops  -> a real orphan IS a defect (a stop on a ticker you
--                      do not own; the sweep sits in a bare except:pass).
--   * exit_signals  -> a real orphan is suspicious (signals on an
--                      unowned name).
--   * recommendations / analyst_coverage -> orphans are EXPECTED and
--                      NORMAL. These legitimately cover names you have
--                      never held. Reported only for scale, not as bugs.
-- ---------------------------------------------------------------------
WITH known AS (
    SELECT ticker FROM holdings
    UNION
    SELECT ticker FROM trades
)
SELECT 'manual_stops'     AS source_table, COUNT(DISTINCT ticker) AS orphan_tickers, 'DEFECT if > 0'   AS interpretation
  FROM manual_stops     WHERE ticker NOT IN (SELECT ticker FROM known)
UNION ALL
SELECT 'exit_signals',     COUNT(DISTINCT ticker), 'suspicious if > 0'
  FROM exit_signals     WHERE ticker NOT IN (SELECT ticker FROM known)
UNION ALL
SELECT 'recommendations',  COUNT(DISTINCT ticker), 'EXPECTED - not a bug'
  FROM recommendations  WHERE ticker NOT IN (SELECT ticker FROM known)
UNION ALL
SELECT 'analyst_coverage', COUNT(DISTINCT ticker), 'EXPECTED - not a bug'
  FROM analyst_coverage WHERE ticker NOT IN (SELECT ticker FROM known);


-- ---------------------------------------------------------------------
-- Q8a — D15 / hygiene: the DDL declares trades.price NOT NULL CHECK > 0,
-- so this SHOULD return zeros. A non-zero result means legacy rows
-- predate the constraint -- which would make D15 genuinely reachable.
-- ---------------------------------------------------------------------
SELECT
    COUNT(*)                                        AS total_trades,
    COUNT(*) FILTER (WHERE price  IS NULL)          AS null_price,
    COUNT(*) FILTER (WHERE shares IS NULL)          AS null_shares,
    COUNT(*) FILTER (WHERE price  <= 0)             AS nonpositive_price,
    COUNT(*) FILTER (WHERE shares <= 0)             AS nonpositive_shares,
    COUNT(*) FILTER (WHERE traded_at IS NULL)       AS null_traded_at
FROM trades;


-- ---------------------------------------------------------------------
-- Q8b — context for Q8c: which action values actually exist?
-- recalculate_from_trades treats SPLIT rows as OVERWRITING rather than
-- accumulating, so any ticker with a SPLIT row cannot be checked by the
-- naive net-shares math in Q8c.
-- ---------------------------------------------------------------------
SELECT action, COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers
FROM trades
GROUP BY action
ORDER BY rows DESC;


-- ---------------------------------------------------------------------
-- Q8c — D5 / ledger divergence: does `holdings` agree with a naive
-- replay of `trades`? holdings is mutated INCREMENTALLY and only
-- reconciled when you open the Trade Journal, so drift is possible.
--
-- CAVEAT: tickers carrying a SPLIT row are excluded, because the naive
-- BUY-minus-SELL sum is not valid for them (see Q8b). A ticker in
-- holdings with NO trades at all, or a fractional diff, is the D5
-- signature.
-- ---------------------------------------------------------------------
WITH split_tickers AS (
    SELECT DISTINCT ticker FROM trades WHERE action = 'SPLIT'
),
net AS (
    SELECT ticker,
           SUM(CASE WHEN action = 'BUY'  THEN  shares
                    WHEN action = 'SELL' THEN -shares
                    ELSE 0 END) AS net_shares
    FROM trades
    WHERE ticker NOT IN (SELECT ticker FROM split_tickers)
    GROUP BY ticker
)
SELECT
    COALESCE(h.ticker, n.ticker)                                        AS ticker,
    h.shares                                                            AS holdings_shares,
    n.net_shares                                                        AS trades_net_shares,
    ROUND(COALESCE(h.shares, 0) - COALESCE(n.net_shares, 0), 4)         AS diff
FROM holdings h
FULL OUTER JOIN net n ON n.ticker = h.ticker
WHERE COALESCE(h.ticker, n.ticker) NOT IN (SELECT ticker FROM split_tickers)
  AND ROUND(COALESCE(h.shares, 0) - COALESCE(n.net_shares, 0), 4) <> 0
ORDER BY ABS(COALESCE(h.shares, 0) - COALESCE(n.net_shares, 0)) DESC;


-- ---------------------------------------------------------------------
-- Q9 — context: daily_snapshots coverage (the EOD baseline behind
-- Tier-B day P&L and the E2 alpha-attribution countdown).
-- ---------------------------------------------------------------------
SELECT
    COUNT(DISTINCT snapshot_date)   AS days_captured,
    MIN(snapshot_date)              AS first_day,
    MAX(snapshot_date)              AS last_day,
    COUNT(*)                        AS total_rows
FROM daily_snapshots;


-- ---------------------------------------------------------------------
-- Q9b — context: the actual GAPS. Any gap_days > 3 spanning a normal
-- trading week means the EOD lane missed multiple sessions.
-- ---------------------------------------------------------------------
WITH days AS (
    SELECT DISTINCT snapshot_date FROM daily_snapshots
),
gaps AS (
    SELECT
        snapshot_date,
        LAG(snapshot_date) OVER (ORDER BY snapshot_date) AS prev_date,
        snapshot_date - LAG(snapshot_date) OVER (ORDER BY snapshot_date) AS gap_days
    FROM days
)
SELECT prev_date, snapshot_date, gap_days
FROM gaps
WHERE gap_days > 3
ORDER BY gap_days DESC, snapshot_date DESC
LIMIT 30;
