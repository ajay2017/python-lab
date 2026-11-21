-- READ-ONLY readout of broker_connection_status (#34c C1, docs/plans/broker-connection-alarm-34c.md).
-- Run each block in the Supabase SQL editor after a week or two of broker runs; paste the
-- results back so the alarm's K (SNAPTRADE_REFRESH_MAX_MISSED_SESSIONS) is set from evidence.
-- SELECT only: nothing here writes. All times shown in America/New_York.

-- 1. The last 20 runs.
SELECT id,
       checked_at AT TIME ZONE 'America/New_York'                AS checked_et,
       n_accounts, n_connections, any_linked_disabled, n_disabled, any_disabled, any_holdings_unavailable,
       holdings_refreshed_oldest AT TIME ZONE 'America/New_York' AS refreshed_oldest_et,
       holdings_refreshed_newest AT TIME ZONE 'America/New_York' AS refreshed_newest_et,
       holdings_stamp_missing, txn_refreshed_utc_date,
       positions_read_ok, snapshot_saved, positions_sig, freshness_modes, error
FROM broker_connection_status
ORDER BY checked_at DESC
LIMIT 20;

-- 2. Per ET date: the earliest and latest "oldest holdings refresh" seen by that day's runs
--    (does SnapTrade skip nights, weekends, holidays?).
SELECT (checked_at AT TIME ZONE 'America/New_York')::date                      AS et_date,
       count(*)                                                                 AS runs,
       min(holdings_refreshed_oldest AT TIME ZONE 'America/New_York')           AS earliest_refresh_et,
       max(holdings_refreshed_oldest AT TIME ZONE 'America/New_York')           AS latest_refresh_et
FROM broker_connection_status
GROUP BY 1
ORDER BY 1 DESC;

-- 3. Distinct positions signatures per ET day (does a call fetch live? 1 = positions did not change that day).
SELECT (checked_at AT TIME ZONE 'America/New_York')::date AS et_date,
       count(*)                                           AS runs,
       count(DISTINCT positions_sig)                      AS distinct_positions_sig
FROM broker_connection_status
GROUP BY 1
ORDER BY 1 DESC;

-- 4. Runs that were not clean: no snapshot saved (false = partial read or write failure; NULL = not
--    attempted), a disabled connection of OURS (any_disabled alone can be an old authorization), or
--    unavailable holdings.
SELECT id,
       checked_at AT TIME ZONE 'America/New_York' AS checked_et,
       snapshot_saved, positions_read_ok, any_linked_disabled, n_disabled, any_disabled, disabled_date,
       any_holdings_unavailable, accounts_ok, connections_ok, error
FROM broker_connection_status
WHERE snapshot_saved = false OR any_linked_disabled = true OR any_holdings_unavailable = true
ORDER BY checked_at DESC;
