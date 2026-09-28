-- Metadata table for Supabase. One-off script: paste into Supabase
-- SQL Editor -> New query -> Run (project ref zpyceqkkcgaoiqlbkcjf).
-- Requires 0001_init_schema.sql (use_cases, set_updated_at) to be applied.
--
-- One table, three kinds of row, told apart by `kind`:
--   document       metadata of a file uploaded for a use case
--                  (DocumentProcessingResult minus extracted_text)
--   rag_source     metadata of one indexed SDAIA knowledge-base chunk
--                  (app/rag/document_loader.py + chunker.py); no use case
--   rag_retrieval  metadata of one evidence chunk retrieved for a use case
--                  (app/rag/retriever.py), incl. query, rank, distance score
-- Metadata only: no document or chunk text is stored here.
-- page_count also holds the RAG loader's `total_pages`.

create table public.metadata (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('document', 'rag_source', 'rag_retrieval')),
  use_case_id uuid references public.use_cases (id) on delete cascade,

  -- shared by all kinds
  file_name text not null,
  file_type text,
  document_type text,
  page_count int check (page_count is null or page_count >= 0),

  -- document: Document Processing Agent output
  detected_language text
    check (detected_language is null or detected_language in ('arabic', 'english', 'mixed', 'unknown')),
  word_count int check (word_count is null or word_count >= 0),
  ocr_used boolean,
  warnings text[] not null default '{}',
  file_size_bytes bigint check (file_size_bytes is null or file_size_bytes >= 0),

  -- rag_source / rag_retrieval: source catalog + chunk metadata
  source text,
  title text,
  authority text,
  domain text,
  audience text,
  year int,
  access_level text,
  page int check (page is null or page >= 1),
  chunk_index int check (chunk_index is null or chunk_index >= 1),
  embedding_model text,

  -- rag_retrieval only
  retrieval_query text,
  retrieval_rank int check (retrieval_rank is null or retrieval_rank >= 1),
  distance_score double precision,

  extra jsonb not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- corpus rows belong to no use case; every other row belongs to one
  constraint metadata_use_case_scope check ((kind = 'rag_source') = (use_case_id is null)),
  -- the rag_source unique key below needs both parts (nulls never collide)
  constraint metadata_rag_source_position
    check (kind <> 'rag_source' or (page is not null and chunk_index is not null))
);

create index metadata_use_case_id_idx on public.metadata (use_case_id);
create index metadata_kind_idx on public.metadata (kind);
create index metadata_file_name_idx on public.metadata (file_name);
create index metadata_domain_idx on public.metadata (domain) where domain is not null;

-- Partial index: PostgREST upsert can't target it, so re-index the corpus by
-- deleting kind = 'rag_source' rows and inserting again.
create unique index metadata_rag_source_key
  on public.metadata (file_name, page, chunk_index)
  where kind = 'rag_source';

create trigger set_metadata_updated_at
  before update on public.metadata
  for each row execute function public.set_updated_at();

-- Backend writes with the service role key (bypasses RLS); authenticated
-- users get read-only access, same as the other tables.
alter table public.metadata enable row level security;

create policy "Authenticated users can read metadata"
  on public.metadata for select to authenticated
  using (true);

-- Make the new table visible to the REST API right away.
notify pgrst, 'reload schema';
