-- Remove the CSV import the four dashboard tables replace.
--
-- RUN THIS LAST. Only once schema.sql has run, the rows
-- are loaded, and the page is reading from Supabase.
--
-- To recover it, re-import the CSV committed in the repo
-- as ufa_2025_player_week.csv. It is the same 8,445 rows.

drop table if exists public."2025 Player Dataset";
