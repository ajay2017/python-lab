-- =====================================================================
-- DRISHTA — Database Maintenance Framework · Phase 0 DDL confirmation
-- Plan: docs/plans/data-maintenance-framework.md   ·   Written 2026-09-28
--
-- READ-ONLY. Every statement is a SELECT against information_schema/
-- pg_catalog. Nothing writes, updates, deletes, or touches an app table.
-- Safe to run against production.
--
-- HOW TO RUN: the Supabase SQL editor returns only the LAST result set,
-- so run these ONE BLOCK AT A TIME and paste each result back.
--
-- PURPOSE: resolve two things the plan doc left explicitly blocked on
-- real DDL (a coding session has no Supabase credentials to check this
-- itself): (1) F3 — do the 6 tables db.py calls bare .upsert() on (no
-- explicit on_conflict) actually have a PK/unique constraint that makes
-- that upsert correct?; (2) the real account_flows uniqueness situation
-- that governs the F2a write-time-dedup-guard decision.
-- =====================================================================


-- ---------------------------------------------------------------------
-- Q1 — F3: PK/unique constraints on the 6 bare-`.upsert()` tables.
--
-- stock_analyzer/db.py calls `.upsert(...)` with NO explicit on_conflict
-- argument for debate_cache, structural_scan_cache, regime_scenario_cache,
-- catalyst_stress_cache, thesis_cluster_cache, and missed_opportunity_cache
-- — correctness of each upsert depends entirely on the table's own PK/
-- unique constraint matching the columns the Python code actually treats
-- as the natural key (e.g. debate_cache is written per ticker+debate_type
-- +debate_date; the others are one row per scan_date).
--
-- READ IT AS: for each table, `key_columns` should be the SAME set the
-- corresponding save_* function's docstring in db.py claims it upserts on
-- (see docs/plans/data-maintenance-framework.md's F3 finding for what
-- each one SHOULD be). A mismatch, or a bare `id`-only PK with no other
-- unique constraint, means the upsert can silently create duplicate rows
-- instead of updating the intended one — confirms F3 as a real (not just
-- latent) risk for that specific table.
-- ---------------------------------------------------------------------
SELECT
    tc.table_name,
    tc.constraint_type,
    tc.constraint_name,
    STRING_AGG(kcu.column_name, ', ' ORDER BY kcu.ordinal_position) AS key_columns
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
   AND tc.table_schema = kcu.table_schema
WHERE tc.table_schema = 'public'
  AND tc.table_name IN (
      'debate_cache', 'structural_scan_cache', 'regime_scenario_cache',
      'catalyst_stress_cache', 'thesis_cluster_cache', 'missed_opportunity_cache'
  )
  AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
GROUP BY tc.table_name, tc.constraint_type, tc.constraint_name
ORDER BY tc.table_name, tc.constraint_type;


-- ---------------------------------------------------------------------
-- Q2 — Any foreign-key constraints anywhere in the schema?
--
-- The codebase-wide research pass found no FK enforcement visible from
-- db.py alone (every cross-table link — ticker, rec_type, gate_id — is a
-- bare string with no DB-level reference). This confirms that positively
-- rather than assuming it, and scopes how far a referential-integrity
-- check (F5 in the plan) can ever go: if this returns zero rows, F5 stays
-- purely advisory/detective forever, by necessity, not by choice.
--
-- READ IT AS: an empty result confirms the "no FKs anywhere" premise the
-- whole design leans on for F5. Any row returned is a genuine surprise —
-- paste it back before Phase 1 scopes F5's checks.
-- ---------------------------------------------------------------------
SELECT
    tc.table_name,
    kcu.column_name,
    ccu.table_name  AS references_table,
    ccu.column_name AS references_column,
    tc.constraint_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
   AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage ccu
    ON tc.constraint_name = ccu.constraint_name
   AND tc.table_schema = ccu.table_schema
WHERE tc.table_schema = 'public'
  AND tc.constraint_type = 'FOREIGN KEY'
ORDER BY tc.table_name;


-- ---------------------------------------------------------------------
-- Q3 — The real account_flows uniqueness situation (governs Decision 1
-- in the plan doc: the add_account_flow write-time dedup guard).
--
-- db.py's own comment (~line 827-836) says a "partial unique index" on
-- account_flows only matches rows WHERE snaptrade_txn_id IS NOT NULL —
-- meaning a manually-entered flow (snaptrade_txn_id always NULL) is
-- invisible to it. This pulls the ACTUAL index definitions to confirm
-- that's really true, and to see the exact column set + WHERE clause so
-- Phase 1b's dedup key can be chosen to not collide with it.
--
-- READ IT AS: look for any index whose indexdef contains "WHERE" — that's
-- the partial index. If its WHERE clause excludes NULL snaptrade_txn_id
-- rows (as expected), the manual-entry gap is confirmed real, and the new
-- guard needs a DIFFERENT key (flow_date + flow_type + amount + note, or
-- whatever Decision 1 settles on) scoped specifically to snaptrade_txn_id
-- IS NULL rows, so it can't collide with the existing broker-sourced index.
-- ---------------------------------------------------------------------
SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'account_flows'
ORDER BY indexname;


-- ---------------------------------------------------------------------
-- Q4 — account_flows: how many manual (non-broker) rows exist today, and
-- is there any existing accidental duplicate among them?
--
-- Scopes the blast radius of Decision 1 concretely — if this table has
-- very few manual rows and zero existing duplicates, the write-time guard
-- is purely preventive (no cleanup needed alongside it); if duplicates
-- already exist, Phase 1b needs a one-time manual cleanup query too.
--
-- READ IT AS: `manual_rows` with snaptrade_txn_id IS NULL is the
-- at-risk population. `dup_groups` > 0 means real duplicates already
-- exist among them — note which (flow_date, flow_type, amount) combos,
-- since that's exactly the kind of key Decision 1 needs to define around.
-- ---------------------------------------------------------------------
SELECT
    COUNT(*) FILTER (WHERE snaptrade_txn_id IS NULL) AS manual_rows,
    COUNT(*) FILTER (WHERE snaptrade_txn_id IS NOT NULL) AS broker_rows,
    (SELECT COUNT(*) FROM (
        SELECT flow_date, flow_type, amount, COUNT(*) AS n
        FROM account_flows
        WHERE snaptrade_txn_id IS NULL
        GROUP BY flow_date, flow_type, amount
        HAVING COUNT(*) > 1
    ) dupes) AS dup_groups
FROM account_flows;
