-- =====================================================================
-- DRISHTA — Database Maintenance Framework · thesis_reviews finding check
-- Written 2026-09-29, follow-up to the 2026-09-28 false-positive fix
--
-- READ-ONLY. A single SELECT. Nothing writes, updates, deletes, or
-- touches an app table. Safe to run against production.
--
-- PURPOSE: check_thesis_reviews_duplicates() still flags 24 tickers with
-- a duplicate (ticker, inputs_hash) after the 2026-09-28 fix excluded the
-- "earnings_" checkpoint marker — meaning NONE of these 24 groups carry
-- that marker; they're all genuine 16-hex-char content-hash matches from
-- the manual "Review Thesis" path. Two competing explanations, and
-- reasoning alone can't distinguish them (the same trap the earnings_
-- guess already fell into once):
--   (a) A real double-click / rerun bug: two rows written within
--       seconds of each other.
--   (b) Legitimate, deliberate re-evaluations on different days where the
--       underlying evidence (technical/fundamentals/news) genuinely
--       hadn't changed, so the hash collided honestly.
--
-- READ IT AS: look at `seconds_apart` for each duplicate pair. Values in
-- the single digits or low tens = (a), a real bug worth a fix. Values in
-- the thousands+ (hours/days/weeks) = (b), and the check needs a
-- temporal-proximity requirement added, not just a hash match, so
-- legitimate re-evaluations stop being called "duplicates".
-- ---------------------------------------------------------------------
SELECT
    ticker,
    inputs_hash,
    id,
    reviewed_at,
    created_at,
    LEAD(reviewed_at) OVER (PARTITION BY ticker, inputs_hash ORDER BY reviewed_at)
        AS next_reviewed_at,
    EXTRACT(EPOCH FROM (
        LEAD(reviewed_at) OVER (PARTITION BY ticker, inputs_hash ORDER BY reviewed_at)
        - reviewed_at
    )) AS seconds_apart
FROM thesis_reviews
WHERE (ticker, inputs_hash) IN (
    SELECT ticker, inputs_hash
    FROM thesis_reviews
    WHERE inputs_hash IS NOT NULL
      AND inputs_hash NOT LIKE 'earnings_%'
    GROUP BY ticker, inputs_hash
    HAVING COUNT(*) >= 2
)
ORDER BY ticker, inputs_hash, reviewed_at;
