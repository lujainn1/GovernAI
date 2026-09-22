-- Long-term memory for the governance agents (see app/memory.py). Run this
-- once against your Supabase project after 0001_init_schema.sql.
--
-- One row per use case: a readable summary of how the case was governed and
-- an embedding of the case's situation, used to recall similar past cases as
-- precedent when a new use case is submitted. Until this is applied the agents
-- simply run without memory (the backend logs a warning and carries on).
--
-- The embedding is a jsonb array of floats rather than a pgvector column: the
-- backend does the similarity search itself, which is plenty for a governance
-- team's case volume. Move to `vector(1536)` + an RPC function if it ever
-- outgrows that.

create table public.agent_memory (
  use_case_id uuid primary key references public.use_cases (id) on delete cascade,
  content text not null,
  embedding jsonb not null,
  embedding_model text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger set_agent_memory_updated_at
  before update on public.agent_memory
  for each row execute function public.set_updated_at();

-- Same access model as the other tables: the backend writes with the service
-- role key (bypasses RLS); authenticated users get read-only access.
alter table public.agent_memory enable row level security;

create policy "Authenticated users can read agent memory"
  on public.agent_memory for select to authenticated
  using (true);
