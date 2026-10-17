-- APPLIED 2026-10-12. The database wakes the mail server the moment a mail is queued (pg_net), and a one-minute safety net
-- (pg_cron) retries failures and sends delayed mail (e.g. review requests three days after delivery).
create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron;

create or replace function private.call_outbox_run() returns void
language plpgsql security definer set search_path = public, private, extensions as $$
declare v_url text; v_secret text;
begin
  select value into v_url from private.mail_runtime_config where key = 'outbox_url';
  select value into v_secret from private.mail_runtime_config where key = 'outbox_secret';
  if coalesce(v_url, '') = '' then return; end if;
  perform net.http_post(url := v_url, body := '{}'::jsonb,
    headers := jsonb_build_object('Content-Type', 'application/json') || case when coalesce(v_secret, '') <> '' then jsonb_build_object('X-Outbox-Secret', v_secret) else '{}'::jsonb end,
    timeout_milliseconds := 8000);
exception when others then
  raise warning 'call_outbox_run failed: %', sqlerrm;
end $$;
revoke all on function private.call_outbox_run() from public, anon, authenticated;

create or replace function private.outbox_after_insert() returns trigger
language plpgsql security definer set search_path = public, private as $$
begin
  if exists (select 1 from public.mail_outbox where status = 'queued' and next_attempt_at <= now()) then perform private.call_outbox_run(); end if;
  return null;
exception when others then return null;
end $$;
drop trigger if exists mail_outbox_wakeup on public.mail_outbox;
create trigger mail_outbox_wakeup after insert on public.mail_outbox for each statement execute function private.outbox_after_insert();

create or replace function private.outbox_sweep() returns void
language plpgsql security definer set search_path = public, private as $$
begin
  if exists (select 1 from public.mail_outbox where status in ('queued', 'sending') and next_attempt_at <= now() and attempts < 6) then perform private.call_outbox_run(); end if;
end $$;
revoke all on function private.outbox_sweep() from public, anon, authenticated;
select cron.unschedule(jobid) from cron.job where jobname = 'mail-outbox-sweep';
select cron.schedule('mail-outbox-sweep', '* * * * *', 'select private.outbox_sweep()');

create policy deny_all_client_access on private.mail_runtime_config for all to anon, authenticated using (false) with check (false);
