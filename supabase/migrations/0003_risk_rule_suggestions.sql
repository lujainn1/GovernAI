-- Persist Risk Assessment Agent rule suggestions (see app/models.py
-- SuggestedRiskRule) so they survive a save/reload instead of only appearing in
-- the immediate API response. Run this once against your Supabase project after
-- 0001_init_schema.sql and 0002_agent_memory.sql.
--
-- These are proposals for a human governance reviewer. The agent never writes
-- to risk_rules itself; a reviewer who agrees adds the rule through the
-- existing POST /risk-rules endpoint.
--
-- Apply this BEFORE (or together with) deploying the matching backend code:
-- save_report now writes this column, so an unmigrated database rejects every
-- governance_reports upsert.

alter table public.governance_reports
  add column risk_suggested_new_rules jsonb not null default '[]'::jsonb;
