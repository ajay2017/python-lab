-- =====================================================================
-- DRISHTA — LLM decision roles in App Settings: chunk C0 DDL
-- Written 2026-11-01 (docs/plans/llm-model-routing.md section 17.4).
-- Apply BEFORE chunk C3 (score-cache provenance) or C2's eval writer ships:
-- PostgREST rejects a whole select / upsert that names a missing column.
-- Idempotent: safe to re-run.
-- =====================================================================

-- 1. Score cache provenance: which model (and effort) produced each row.
--    NULL on existing rows = the pre-switch model (Haiku 4.5), so they stay hits.
ALTER TABLE sentiment_llm_cache ADD COLUMN IF NOT EXISTS model        text;
ALTER TABLE sentiment_llm_cache ADD COLUMN IF NOT EXISTS effort       text;
ALTER TABLE sentiment_llm_cache ADD COLUMN IF NOT EXISTS served_model text;

-- 2. Stored eval results. App Settings enables Save for a decision role only
--    when a passing row exists for that exact model + effort.
CREATE TABLE IF NOT EXISTS llm_role_evals (
    id                bigserial PRIMARY KEY,
    role              text        NOT NULL,
    model             text        NOT NULL,
    effort            text,
    capabilities_hash text        NOT NULL,
    criteria_version  int         NOT NULL,
    baseline          text        NOT NULL,
    sample_n          int         NOT NULL,
    metrics           jsonb       NOT NULL,
    passed            boolean     NOT NULL,
    failures          jsonb,
    ran_at            timestamptz NOT NULL DEFAULT now(),
    ran_by            text        NOT NULL,
    code_sha          text
);
CREATE INDEX IF NOT EXISTS llm_role_evals_lookup
    ON llm_role_evals (role, model, ran_at DESC);

ALTER TABLE llm_role_evals ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all (service role)" ON public.llm_role_evals;
CREATE POLICY "Allow all (service role)" ON public.llm_role_evals
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 3. U5: which model extracted each trade's Pre-Mortem trigger (the R-Multiple
--    declared stop). NULL on existing rows = Haiku 4.5; db.load_trades backfills None.
ALTER TABLE trades ADD COLUMN IF NOT EXISTS premortem_trigger_model text;

-- Verify (the SQL editor shows only the last result, so this is one row).
-- Expect: cache_cols 3, evals_rls true, evals_policies 1, can_insert true,
--         trades_col 1, eval_rows 0.
SELECT
  (SELECT count(*) FROM information_schema.columns
     WHERE table_name = 'sentiment_llm_cache'
       AND column_name IN ('model', 'effort', 'served_model'))           AS cache_cols,
  (SELECT relrowsecurity FROM pg_class WHERE relname = 'llm_role_evals') AS evals_rls,
  (SELECT count(*) FROM pg_policies WHERE tablename = 'llm_role_evals')  AS evals_policies,
  has_table_privilege('service_role', 'public.llm_role_evals', 'INSERT') AS can_insert,
  (SELECT count(*) FROM information_schema.columns
     WHERE table_name = 'trades' AND column_name = 'premortem_trigger_model') AS trades_col,
  (SELECT count(*) FROM llm_role_evals)                                   AS eval_rows;
