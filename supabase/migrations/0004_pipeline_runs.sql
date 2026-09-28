-- Step-by-step (human-in-the-loop) pipeline runs (see app/pipeline_runs.py).
-- Run this once against your Supabase project after 0001_init_schema.sql.
--
-- A normal run executes every agent in one request. A step-by-step run pauses
-- after each agent so a person can approve or reject its output before the
-- next agent starts; this table holds that paused state between requests.
--
-- One row per use case. `steps` is the ordered list of agent outputs with the
-- human verdict on each (a PipelineStep in app/models.py); it is read and
-- written whole, so it lives in one jsonb column rather than its own table.
-- `memory_context` is the precedent recalled from agent_memory when the run
-- started, kept so every later agent sees the same precedent the first one did.
--
-- The final GovernanceReport is not stored here: it is written to
-- governance_reports once the last step is approved, exactly as a normal run does.

create table public.pipeline_runs (
  use_case_id uuid primary key references public.use_cases (id) on delete cascade,
  status text not null check (status in ('awaiting_step_approval', 'completed', 'rejected')),
  memory_context text not null default '',
  steps jsonb not null default '[]',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index pipeline_runs_status_idx on public.pipeline_runs (status);

create trigger set_pipeline_runs_updated_at
  before update on public.pipeline_runs
  for each row execute function public.set_updated_at();

-- Same access model as the other tables: the backend writes with the service
-- role key (bypasses RLS); authenticated users get read-only access.
alter table public.pipeline_runs enable row level security;

create policy "Authenticated users can read pipeline runs"
  on public.pipeline_runs for select to authenticated
  using (true);
