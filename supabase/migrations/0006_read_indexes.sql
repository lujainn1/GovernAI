-- Indexes for the read paths behind the dashboard pages.
-- Run this once against your Supabase project after 0004_pipeline_runs.sql.
--
-- The reports list, the metrics/health windows and the step-by-step runs list
-- all order or filter by created_at; without an index Postgres sorts the whole
-- table on every page load. Safe to re-run.

create index if not exists governance_reports_created_at_idx
  on public.governance_reports (created_at);

create index if not exists pipeline_runs_created_at_idx
  on public.pipeline_runs (created_at desc);
