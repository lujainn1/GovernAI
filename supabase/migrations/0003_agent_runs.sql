-- GovernAI: agent observability
--
-- One row per agent invocation (Risk Assessment, Policy Compliance,
-- Decision): its timing, token usage, estimated cost, ordered steps, tool
-- calls, and error if it failed. `audit_log_id` links each run to the audit
-- entry for the same pipeline stage (a `<stage>_failed` entry when the agent
-- errored), and `request_id` links it to the HTTP request that caused it.
--
-- Privacy: prompts, submitted documentation, and full tool outputs are NOT
-- stored. `tool_calls` holds only truncated previews. `error_message` is
-- truncated by the application and, when the model's reply could not be
-- parsed, may include a short fragment (about 120 chars) of that reply.

create table public.agent_runs (
  id uuid primary key default gen_random_uuid(),
  use_case_id uuid not null references public.use_cases (id) on delete cascade,
  audit_log_id uuid references public.audit_log (id) on delete set null,
  request_id text,
  agent text not null,
  stage text not null,
  model text,
  status text not null check (status in ('success', 'error')),
  error_type text,
  error_message text,
  started_at timestamptz not null,
  latency_ms numeric(12, 1) not null default 0,
  iterations int not null default 0,
  tool_call_count int not null default 0,
  tool_error_count int not null default 0,
  prompt_tokens int not null default 0,
  completion_tokens int not null default 0,
  total_tokens int not null default 0,
  estimated_cost_usd numeric(12, 6) not null default 0,
  steps jsonb not null default '[]',
  tool_calls jsonb not null default '[]',
  created_at timestamptz not null default now()
);

create index agent_runs_use_case_id_idx on public.agent_runs (use_case_id);
create index agent_runs_started_at_idx on public.agent_runs (started_at desc);
create index agent_runs_agent_idx on public.agent_runs (agent);
create index agent_runs_request_id_idx on public.agent_runs (request_id);

-- Read-only for authenticated users; writes go through the backend's service role.
alter table public.agent_runs enable row level security;

create policy "Authenticated users can read agent runs"
  on public.agent_runs for select to authenticated
  using (true);
