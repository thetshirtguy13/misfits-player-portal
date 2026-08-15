# Misfits Player Portal — Supabase Connected

This version replaces the old browser-only `localStorage` prototype with your Supabase backend.

## What is connected
- Supabase email/password authentication
- Parent/guardian accounts with linked youth player profiles
- Coach/admin role-aware screens
- Shared PostgreSQL player stats
- Shared workout completion logs
- Private Supabase Storage uploads for player photos/videos
- GameChanger CSV import into `player_stats`
- Password reset flow
- Email verification through Supabase Auth

## IMPORTANT — run the migration first
In Supabase → SQL Editor, run:

`supabase_security_and_app_tables.sql`

You should see:

`MISFITS FINAL APP MIGRATION SUCCESS`

This migration also fixes two security items before real player use:
1. Browser users cannot promote themselves to coach/admin by editing their `profiles.role`.
2. `player-media` Storage access is tightened from “any authenticated user” to only users who can access the player UUID in the file path.

## First launch
Open `index.html`. The first screen asks for your Supabase **publishable key** (`sb_publishable_...`).

The project URL is prefilled as:

`https://skwjtkgrudjutqdvwiad.supabase.co`

Do **not** use the secret/service-role key in the browser.

## Coach/admin approval
New accounts default to parent-level access for safety. A user can request Coach during signup, but an admin must approve/promote the account.

Run in Supabase SQL Editor when appropriate:

```sql
update public.profiles
set role = 'coach'
where id = (select id from auth.users where email = 'APPROVED-COACH@example.com');
```

For the organization owner/admin:

```sql
update public.profiles
set role = 'admin'
where id = (select id from auth.users where email = 'YOUR-ADMIN-EMAIL@example.com');
```

## Before testing email links
Deploy the app to a public URL first, then set Supabase:
Authentication → URL Configuration → Site URL / Redirect URLs

to that deployed address. `localhost:3000` should not remain the production Site URL.

## GameChanger
This build supports GameChanger CSV imports only. It does not request or store GameChanger credentials.
