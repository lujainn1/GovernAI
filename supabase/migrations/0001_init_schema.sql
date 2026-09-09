-- GovernAI core schema: use cases, policies, risk rules, governance reports,
-- audit log, and user profiles. Run this once against your Supabase project
-- (SQL Editor, or `supabase db push`) before running scripts/seed_supabase.py.
--
-- Design notes:
--   * The FastAPI backend talks to these tables with the service role key
--     (see app/db.py), which bypasses Row Level Security entirely. Every
--     route in app/api.py already requires a valid Supabase session via
--     app.auth.get_current_user, so authorization is enforced at the API
--     layer. RLS below only governs *direct* table access (e.g. if a client
--     ever queries Supabase directly with a user's session) and defaults to
--     read-only for authenticated users.
--   * `policies` and `risk_rules` cover several overlapping frameworks
--     (internal POL-*, SDAIA AI Ethics, PDPL, cross-border transfer, GenAI).
--     Only the fields every framework shares are real columns; the rest
--     (principle, module, lifecycle_phase, evidence_required, ...) live in
--     a `metadata` jsonb column and are merged back to the top level by
--     app/tools/policy_repository.py and app/tools/risk_rules.py.

create extension if not exists pgcrypto;

-- =========================================================
-- profiles (one row per Supabase auth user)
-- =========================================================

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text,
  full_name text,
  role text not null default 'submitter' check (role in ('admin', 'reviewer', 'submitter')),
  created_at timestamptz not null default now()
);

-- Auto-create a profile row whenever a new user signs up.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- =========================================================
-- use_cases (the intake form for an AI system / agent)
-- =========================================================

create table public.use_cases (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  description text not null,
  owner text not null,
  data_classification text,
  deployment_context text,
  autonomy_level text,
  documentation text,
  created_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index use_cases_created_by_idx on public.use_cases (created_by);

-- =========================================================
-- policies (organizational AI governance policy repository)
-- =========================================================

create table public.policies (
  id text primary key,
  title text not null,
  category text not null,
  description text not null,
  status text not null default 'active',
  coverage int not null default 100,
  min_risk_level text not null default 'low'
    check (min_risk_level in ('low', 'medium', 'high', 'critical')),
  requires text[] not null default '{}',
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create index policies_category_idx on public.policies (category);

-- =========================================================
-- risk_rules (the risk-scoring rule set)
-- =========================================================

create table public.risk_rules (
  id text primary key,
  title text,
  category text not null,
  domain text,
  severity text,
  description text,
  condition text,
  action text,
  treatment text,
  weight int,
  trigger_keywords text[] not null default '{}',
  source_refs text[] not null default '{}',
  metadata jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create index risk_rules_category_idx on public.risk_rules (category);

-- =========================================================
-- governance_reports (one row per use-case assessment)
-- =========================================================

create table public.governance_reports (
  use_case_id uuid primary key references public.use_cases (id) on delete cascade,
  risk_level text not null check (risk_level in ('low', 'medium', 'high', 'critical')),
  risk_score int not null check (risk_score between 0 and 100),
  risk_factors text[] not null default '{}',
  risk_rationale text not null,
  compliance_status text not null
    check (compliance_status in ('compliant', 'partially_compliant', 'non_compliant')),
  violated_policies text[] not null default '{}',
  satisfied_policies text[] not null default '{}',
  compliance_rationale text not null,
  decision text not null check (decision in ('approve', 'require_human_approval', 'block')),
  decision_conditions text[] not null default '{}',
  decision_rationale text not null,
  status text not null check (
    status in (
      'completed', 'pending_human_approval', 'blocked',
      'approved_by_human', 'rejected_by_human'
    )
  ),
  human_approval jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- =========================================================
-- audit_log (append-only event trail per use case)
-- =========================================================

create table public.audit_log (
  id uuid primary key default gen_random_uuid(),
  use_case_id uuid not null references public.use_cases (id) on delete cascade,
  stage text not null,
  actor text not null,
  data jsonb not null default '{}',
  created_at timestamptz not null default now()
);

create index audit_log_use_case_id_idx on public.audit_log (use_case_id);
create index audit_log_created_at_idx on public.audit_log (created_at);

-- =========================================================
-- updated_at maintenance
-- =========================================================

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger set_use_cases_updated_at
  before update on public.use_cases
  for each row execute function public.set_updated_at();

create trigger set_governance_reports_updated_at
  before update on public.governance_reports
  for each row execute function public.set_updated_at();

-- =========================================================
-- Row Level Security (read-only for authenticated users; all writes go
-- through the backend, which uses the service role key and bypasses RLS)
-- =========================================================

alter table public.profiles enable row level security;
alter table public.use_cases enable row level security;
alter table public.policies enable row level security;
alter table public.risk_rules enable row level security;
alter table public.governance_reports enable row level security;
alter table public.audit_log enable row level security;

create policy "Users can read their own profile"
  on public.profiles for select to authenticated
  using (auth.uid() = id);

create policy "Authenticated users can read use cases"
  on public.use_cases for select to authenticated
  using (true);

create policy "Authenticated users can read policies"
  on public.policies for select to authenticated
  using (true);

create policy "Authenticated users can read risk rules"
  on public.risk_rules for select to authenticated
  using (true);

create policy "Authenticated users can read governance reports"
  on public.governance_reports for select to authenticated
  using (true);

create policy "Authenticated users can read audit log"
  on public.audit_log for select to authenticated
  using (true);
