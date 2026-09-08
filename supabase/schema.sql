-- UFA MVP Race - Supabase schema.
-- Paste into the SQL editor and run, then load the rows
-- by running push_supabase.py locally.
--
-- Four tables back the dashboard. All are public
-- read-only: the page ships the anon key, so row-level
-- security is what keeps it read-only.


-- ============================== season_meta
-- One row per season: week structure, league-wide
-- counts, and the fitted MVP Score weights.
create table if not exists public.season_meta (
  season        integer primary key,
  weeks         jsonb   not null,
  reg_weeks     integer not null,
  playoff_from  integer not null,
  league_n      integer not null,
  games         integer not null,
  games_full    integer not null,
  fit           jsonb   not null
);


-- ============================== metrics
-- The five races, in the order they appear on the page.
create table if not exists public.metrics (
  k             text    primary key,
  sort_order    integer not null,
  label         text    not null,
  short         text    not null,
  descr         text    not null,
  ceil          numeric not null,
  weeks         jsonb   not null,
  season_scope  text    not null
);


-- ============================== players
-- Season totals for players who reach at least one
-- leaderboard. ranks and trend are keyed by metric.
create table if not exists public.players (
  id                text primary key,
  first_name        text    not null,
  last_name         text    not null,
  jersey            text    not null default '',
  team              text    not null,
  abbrev            text    not null,
  division          text    not null,
  record            text    not null,
  games_played      integer not null default 0,
  games_played_full integer not null default 0,
  goals             integer not null default 0,
  assists           integer not null default 0,
  hockey_assists    integer not null default 0,
  blocks            integer not null default 0,
  completions       integer not null default 0,
  throw_attempts    integer not null default 0,
  turnovers         integer not null default 0,
  yards_thrown      integer not null default 0,
  yards_received    integer not null default 0,
  plus_minus        integer not null default 0,
  total_scores      integer not null default 0,
  mvp_score         numeric not null default 0,
  ranks             jsonb   not null default '{}'::jsonb,
  trend             jsonb   not null default '{}'::jsonb,
  headshot          text
);

create index if not exists players_mvp_score_idx
  on public.players (mvp_score desc);


-- ============================== awards
create table if not exists public.awards (
  award       text    primary key,
  sort_order  integer not null,
  player_id   text    not null
              references public.players (id) on delete cascade,
  note        text    not null default ''
);


-- ============================== row-level security
-- Public read, no public write. The anon key in the
-- browser can only SELECT. The loader script writes with
-- the service_role key, which bypasses RLS.
alter table public.season_meta enable row level security;
alter table public.metrics     enable row level security;
alter table public.players     enable row level security;
alter table public.awards      enable row level security;

drop policy if exists "public read" on public.season_meta;
drop policy if exists "public read" on public.metrics;
drop policy if exists "public read" on public.players;
drop policy if exists "public read" on public.awards;

create policy "public read" on public.season_meta
  for select to anon, authenticated using (true);

create policy "public read" on public.metrics
  for select to anon, authenticated using (true);

create policy "public read" on public.players
  for select to anon, authenticated using (true);

create policy "public read" on public.awards
  for select to anon, authenticated using (true);
