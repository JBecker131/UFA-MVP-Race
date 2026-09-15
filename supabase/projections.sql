-- The one table missing from this project: public.projections.
--
-- season_meta, metrics, players and awards are already loaded; this is the block
-- of schema.sql that has not been applied yet. Paste it into the Supabase SQL
-- editor and run it, then load the rows with:
--
--     python push_supabase.py
--
-- It is the same definition as in schema.sql, split out so you do not have to
-- re-run the whole file. Safe to run twice.
--
-- Why it blocks more than it looks like it should: the Modal API's /compare and
-- /players read this table, and the dashboard's Pick five panel calls /compare.
-- Without it the live model path returns 503 and the page silently falls back to
-- the projections embedded at build time.


-- ============================== projections
-- One row per player who cleared the workload cut in
-- the last completed season, scored by the two random
-- forests in pipeline_def.py. from_season/to_season and
-- min_points repeat on every row: the table is read in
-- one request and the page needs them before it can
-- label anything.
create table if not exists public.projections (
  id              text primary key,
  first_name      text    not null,
  last_name       text    not null,
  abbrev          text    not null,
  team            text    not null,
  division        text    not null,
  record          text    not null default '',
  points_played   integer not null default 0,
  prev_score      numeric not null default 0,
  projected_score numeric not null default 0,
  chance          numeric not null default 0,
  projected_rank  integer not null,
  from_season     integer not null,
  to_season       integer not null,
  min_points      integer not null
);

create index if not exists projections_rank_idx
  on public.projections (projected_rank asc);


-- ============================== row-level security
-- Public read, no public write - the page ships the anon
-- key, so RLS is what keeps it read-only. The loader
-- script writes with the service_role key, which bypasses
-- RLS, and the Modal API reads with the anon key.
alter table public.projections enable row level security;

drop policy if exists "public read" on public.projections;

create policy "public read" on public.projections
  for select to anon, authenticated using (true);
