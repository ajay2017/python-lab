-- =====================================================================
-- DRISHTA — Period report, chunk C0: read-only data audit (2026-10-20)
-- docs/plans/period-report.md. READ-ONLY: every statement is a SELECT.
-- The Supabase SQL editor returns only the LAST result set, so this is one
-- UNION ALL with (section, k1, k2, n, v) text columns. Paste the result back.
--
-- What it settles before the report's numbers are trusted:
--   T*  trades: total rows (PostgREST page cap), provenance by month x action
--       x trigger_type x imported, missing signal_seen / decision_context,
--       when decision_context starts, late-logged context, fractional fills
--   A*  account_daily_snapshots: rows with NULL cash since go-live
--   F*  account_flows: last entry by source (manual vs broker-synced)
--   I*  snaptrade_income_events: type x month x sign (settles the FEE sign)
--   S*  daily_snapshots: sessions captured since 2026-09-10
-- =====================================================================
SELECT * FROM (
  SELECT 'T1 trades total' AS section, NULL::text AS k1, NULL::text AS k2,
         COUNT(*)::text AS n, NULL::text AS v
  FROM trades
  UNION ALL
  SELECT 'T2 trades by month/action/trigger/imported',
         to_char((traded_at AT TIME ZONE 'America/New_York')::date, 'YYYY-MM') || ' ' || UPPER(action),
         COALESCE(trigger_type, '(null)') || CASE WHEN imported THEN ' · imported' ELSE '' END,
         COUNT(*)::text,
         'no signal_seen ' || COUNT(*) FILTER (WHERE signal_seen IS NULL OR btrim(signal_seen) = '')
           || ' · no decision_context ' || COUNT(*) FILTER (WHERE decision_context IS NULL)
  FROM (SELECT *, (broker_txn_id IS NOT NULL
                   OR notes ILIKE '%Robinhood import%' OR notes ILIKE '%RH text import%'
                   OR notes ILIKE '%RH screenshot%') AS imported
        FROM trades WHERE UPPER(action) IN ('BUY', 'SELL')) t
  GROUP BY 2, 3
  UNION ALL
  SELECT 'T3 decision_context first / late-logged', NULL, NULL,
         COUNT(*) FILTER (WHERE ((decision_context->>'captured_at')::timestamptz AT TIME ZONE 'America/New_York')::date
                                <> (traded_at AT TIME ZONE 'America/New_York')::date)::text,
         'first ' || MIN(traded_at)::text
  FROM trades WHERE decision_context IS NOT NULL
  UNION ALL
  SELECT 'T4 fractional fills', NULL, NULL,
         COUNT(*)::text, NULL
  FROM trades WHERE UPPER(action) IN ('BUY', 'SELL') AND shares <> trunc(shares)
  UNION ALL
  SELECT 'A1 account snapshots since 09-10', NULL, NULL,
         COUNT(*)::text,
         'null cash ' || COUNT(*) FILTER (WHERE cash_balance IS NULL)
           || ' · null net_equity ' || COUNT(*) FILTER (WHERE net_equity IS NULL)
  FROM account_daily_snapshots WHERE snapshot_date >= DATE '2026-09-10'
  UNION ALL
  SELECT 'F1 account_flows by type/source', flow_type,
         CASE WHEN snaptrade_txn_id IS NOT NULL THEN 'broker' ELSE 'manual' END,
         COUNT(*)::text, 'last ' || MAX(flow_date)::text
  FROM account_flows GROUP BY 2, 3
  UNION ALL
  SELECT 'I1 income events by type/month/sign', event_type || ' ' || to_char(event_date, 'YYYY-MM'),
         CASE WHEN amount < 0 THEN 'negative' WHEN amount > 0 THEN 'positive' ELSE 'zero' END,
         COUNT(*)::text, 'sum ' || ROUND(SUM(amount), 2)::text
  FROM snaptrade_income_events GROUP BY 2, 3
  UNION ALL
  SELECT 'S1 daily_snapshots sessions since 09-10', NULL, NULL,
         COUNT(DISTINCT snapshot_date)::text, NULL
  FROM daily_snapshots WHERE snapshot_date >= DATE '2026-09-10'
) q
ORDER BY section, k1, k2;
