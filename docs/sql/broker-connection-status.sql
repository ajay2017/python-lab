-- broker_connection_status -- SnapTrade connection / refresh health, one row per broker run
-- (#34c C1, docs/plans/broker-connection-alarm-34c.md, section 2).
-- Append-only. The `broker` cron (cron_runner._run_broker) inserts one row per run once
-- SnapTrade is configured: after the positions read and the snapshot save, and on the
-- list_accounts-failed, no-accounts-linked and all-reads-failed exits. Capture only:
-- nothing reads it to decide anything yet (C2 / C3 do); the owner's readout is
-- docs/sql/readonly-broker-connection-status-readout.sql.
-- Columns:
--   run_started_at / checked_at   the lane's start and the moment the row was built (a
--                                  snapshot saved in the same run has captured_at <= checked_at)
--   accounts_ok / connections_ok  false = that SnapTrade call failed (the derived columns
--                                  below are then NULL = "couldn't check", never "fine")
--   any_disabled                   true when ANY authorization SnapTrade lists is disabled (True / "true")
--   n_disabled                     how many authorizations are disabled
--   any_linked_disabled            true when a disabled authorization is the one one of OUR accounts is
--                                  attached to (an old disabled authorization left by a reconnect does not
--                                  count); NULL = connections or accounts couldn't be read (or no account
--                                  carries an authorization reference), never false in those cases;
--                                  false also when no accounts are linked (n_accounts = 0)
--   any_holdings_unavailable       true only when an account says holdings_unavailable is true;
--                                  SnapTrade returns None for healthy accounts, which counts as available
--   holdings_refreshed_oldest/newest  min / max of SnapTrade's own holdings last_successful_sync (UTC instants)
--   holdings_stamp_missing         accounts whose stamp was missing or date-only
--   txn_refreshed_utc_date         the OLDEST transactions last_successful_sync, a DATE-ONLY UTC date
--   freshness_modes                the raw data_freshness_mode value of each connection
--   positions_read_ok              false = some account's positions read failed (partial read)
--   snapshot_saved                 NULL = not attempted (accounts call failed / no accounts linked);
--                                  false + positions_read_ok false = partial read, no snapshot attempted;
--                                  false + positions_read_ok true = every read worked but the snapshot write failed
--   positions_sig                  16-char hash of the broker positions map this run built
--   error                          scrubbed (no credentials / ids), at most 160 chars
-- The OWNER applies this in the Supabase SQL editor, BEFORE the C1 push deploys (otherwise
-- System Trust shows the store as missing). Safe to re-run.

CREATE TABLE IF NOT EXISTS broker_connection_status (
    id                          BIGSERIAL   PRIMARY KEY,
    run_started_at              TIMESTAMPTZ NOT NULL,
    checked_at                  TIMESTAMPTZ NOT NULL,
    accounts_ok                 BOOLEAN     NOT NULL,
    connections_ok              BOOLEAN     NOT NULL,
    n_accounts                  INTEGER,
    n_connections               INTEGER,
    any_disabled                BOOLEAN,
    n_disabled                  INTEGER,
    any_linked_disabled         BOOLEAN,
    disabled_date               TEXT,
    any_holdings_unavailable    BOOLEAN,
    holdings_refreshed_oldest   TIMESTAMPTZ,
    holdings_refreshed_newest   TIMESTAMPTZ,
    holdings_stamp_missing      INTEGER,
    txn_refreshed_utc_date      DATE,
    freshness_modes             JSONB,
    positions_read_ok           BOOLEAN,
    snapshot_saved              BOOLEAN,
    positions_sig               TEXT,
    error                       TEXT
);

-- Safe whether or not an earlier version of this file was already applied:
ALTER TABLE broker_connection_status ADD COLUMN IF NOT EXISTS n_disabled INTEGER;
ALTER TABLE broker_connection_status ADD COLUMN IF NOT EXISTS any_linked_disabled BOOLEAN;

CREATE INDEX IF NOT EXISTS broker_connection_status_checked_at_idx
    ON broker_connection_status (checked_at DESC);

ALTER TABLE broker_connection_status ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all (service role)" ON public.broker_connection_status;
CREATE POLICY "Allow all (service role)" ON public.broker_connection_status
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Verify (expect: relrowsecurity = true, 1 policy row, service_role_can_insert = true,
-- 0 rows until the first broker run after the deploy):
SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'broker_connection_status';
SELECT policyname, roles, cmd FROM pg_policies WHERE tablename = 'broker_connection_status';
SELECT has_table_privilege('service_role', 'public.broker_connection_status', 'INSERT') AS service_role_can_insert;
SELECT COUNT(*) AS status_rows FROM broker_connection_status;
