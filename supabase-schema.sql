-- Run once in the Supabase SQL editor for Hitim.
create table if not exists public.access_requests (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null unique,
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'blocked')),
  role text not null default 'user'
    check (role in ('user', 'admin')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.access_requests enable row level security;

-- The browser never reads this table directly. Only the FastAPI backend uses
-- the service-role key, which bypasses RLS after validating the Google token.
revoke all on table public.access_requests from anon, authenticated;
