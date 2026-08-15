-- MISFITS PLAYER PORTAL - FINAL APP MIGRATION
-- Run this once in Supabase SQL Editor BEFORE using the Supabase-connected app.
-- It adds workout logs/import history, prevents self-promotion of account roles,
-- and tightens the player-media bucket policies to player-level access.

-- 1) Prevent browser users from changing their own role column.
revoke update on public.profiles from authenticated;
grant update (first_name, last_name, display_name, phone, avatar_url, updated_at)
on public.profiles to authenticated;

-- 2) Simple workout completion log used by the player portal.
create table if not exists public.workout_logs (
  id uuid primary key default gen_random_uuid(),
  player_id uuid not null references public.players(id) on delete cascade,
  workout_code text not null,
  workout_title text not null,
  category text not null,
  minutes integer not null default 0,
  notes text,
  completed_by uuid references public.profiles(id) on delete set null,
  completed_on date not null default current_date,
  created_at timestamptz not null default now()
);

alter table public.workout_logs enable row level security;
grant select, insert, update, delete on public.workout_logs to authenticated;

create index if not exists idx_workout_logs_player
on public.workout_logs(player_id);

drop policy if exists "workout logs read" on public.workout_logs;
create policy "workout logs read"
on public.workout_logs
for select to authenticated
using (public.can_access_player(player_id));

drop policy if exists "workout logs create" on public.workout_logs;
create policy "workout logs create"
on public.workout_logs
for insert to authenticated
with check (
  public.can_access_player(player_id)
  and completed_by = auth.uid()
);

-- 3) GameChanger import history.
create table if not exists public.gamechanger_imports (
  id uuid primary key default gen_random_uuid(),
  uploaded_by uuid not null references public.profiles(id) on delete cascade,
  file_name text not null,
  category text not null,
  rows_imported integer not null default 0,
  imported_at timestamptz not null default now()
);

alter table public.gamechanger_imports enable row level security;
grant select, insert on public.gamechanger_imports to authenticated;

create index if not exists idx_gamechanger_imports_user
on public.gamechanger_imports(uploaded_by);

drop policy if exists "gc imports read" on public.gamechanger_imports;
create policy "gc imports read"
on public.gamechanger_imports
for select to authenticated
using (uploaded_by = auth.uid() or public.is_admin());

drop policy if exists "gc imports create" on public.gamechanger_imports;
create policy "gc imports create"
on public.gamechanger_imports
for insert to authenticated
with check (
  uploaded_by = auth.uid()
  and public.is_coach_or_admin()
);

-- 4) Optional role request table for coach approval.
create table if not exists public.role_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  requested_role text not null check (requested_role in ('coach')),
  status text not null default 'pending' check (status in ('pending','approved','declined')),
  requested_at timestamptz not null default now(),
  reviewed_at timestamptz,
  unique(user_id, requested_role)
);

alter table public.role_requests enable row level security;
grant select, insert on public.role_requests to authenticated;

drop policy if exists "role request own read" on public.role_requests;
create policy "role request own read"
on public.role_requests
for select to authenticated
using (user_id = auth.uid() or public.is_admin());

drop policy if exists "role request own create" on public.role_requests;
create policy "role request own create"
on public.role_requests
for insert to authenticated
with check (user_id = auth.uid() and requested_role = 'coach');

-- 5) Tighten Storage access. Earlier broad policies are removed by name prefix.
do $$
declare p record;
begin
  for p in
    select policyname
    from pg_policies
    where schemaname = 'storage'
      and tablename = 'objects'
      and (policyname ilike 'Player media read%' or policyname ilike 'Player media upload%')
  loop
    execute format('drop policy if exists %I on storage.objects', p.policyname);
  end loop;
end $$;

-- Files must be stored as: <player_uuid>/<filename>
create policy "player media read by player access"
on storage.objects
for select to authenticated
using (
  bucket_id = 'player-media'
  and array_length(storage.foldername(name), 1) >= 1
  and (storage.foldername(name))[1] ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
  and public.can_access_player(((storage.foldername(name))[1])::uuid)
);

create policy "player media upload by player access"
on storage.objects
for insert to authenticated
with check (
  bucket_id = 'player-media'
  and array_length(storage.foldername(name), 1) >= 1
  and (storage.foldername(name))[1] ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
  and public.can_access_player(((storage.foldername(name))[1])::uuid)
);

-- 6) Helpful index for stats queries.
create index if not exists idx_player_stats_player_date
on public.player_stats(player_id, game_date desc);

select 'MISFITS FINAL APP MIGRATION SUCCESS' as status;

-- ADMIN/COACH PROMOTION (run manually in SQL Editor when appropriate):
-- update public.profiles
-- set role = 'admin'
-- where id = (select id from auth.users where email = 'YOUR-ADMIN-EMAIL@example.com');
--
-- update public.profiles
-- set role = 'coach'
-- where id = (select id from auth.users where email = 'APPROVED-COACH@example.com');
