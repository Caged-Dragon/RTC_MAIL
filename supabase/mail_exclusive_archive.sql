-- HISTORICAL: applied to the prior Supabase project "RtCrackers Cart" (ypmiinmkyvzdpakbkers) on 2026-10-11.
-- It has NOT been verified or applied to the newly supplied project ybvsjuyznkdwvrnjnelr.
-- Do NOT run this project-specific archive/policy script on another project just to connect mail;
-- it changes constraints, triggers, RLS policies, and customer_addresses policies and requires a full review.
-- Purpose: company mail lives ONLY in this system.
--   1) mailboxes / business addresses can only be @rtcrackers.com
--   2) append-only archive of every message ever received or sent (survives "Delete forever")
--   3) one SELECT policy on customer_addresses (performance advisor)

alter table public.business_email_addresses
  add constraint business_email_addresses_company_domain_chk check (email_address ~* '@rtcrackers\.com$');
alter table public.mailboxes
  add constraint mailboxes_company_domain_chk check (email_address ~* '@rtcrackers\.com$');

create table if not exists public.mail_archive (
  archive_id bigint generated always as identity primary key,
  email_message_id bigint not null unique,
  mailbox_id bigint, mailbox_address text,
  direction text not null, status_at_archive text,
  message_id text, in_reply_to text, references_header text,
  from_address text not null, from_name text, subject text, text_body text, html_body text,
  sent_at timestamptz, received_at timestamptz, resend_email_id text,
  content_sha256 text not null,
  archived_at timestamptz not null default now()
);
create index if not exists mail_archive_mailbox_time_idx on public.mail_archive (mailbox_address, coalesce(received_at, sent_at, archived_at) desc);
create index if not exists mail_archive_from_idx on public.mail_archive (lower(from_address));

create table if not exists public.mail_archive_recipients (
  archive_recipient_id bigint generated always as identity primary key,
  email_message_id bigint not null,
  recipient_type text not null, email_address text not null, display_name text,
  unique (email_message_id, recipient_type, email_address)
);
create index if not exists mail_archive_recipients_msg_idx on public.mail_archive_recipients (email_message_id);

create or replace function private.mail_archive_block() returns trigger language plpgsql set search_path = '' as $$
begin raise exception 'The mail archive is append-only'; end $$;
create trigger mail_archive_no_change before update or delete on public.mail_archive for each row execute function private.mail_archive_block();
create trigger mail_archive_no_truncate before truncate on public.mail_archive for each statement execute function private.mail_archive_block();
create trigger mail_archive_rcpt_no_change before update or delete on public.mail_archive_recipients for each row execute function private.mail_archive_block();
create trigger mail_archive_rcpt_no_truncate before truncate on public.mail_archive_recipients for each statement execute function private.mail_archive_block();

create or replace function private.archive_email_message(p_id bigint) returns void
language plpgsql security definer set search_path = public, private, extensions as $$
declare m public.email_messages%rowtype; v_addr text;
begin
  select * into m from public.email_messages where email_message_id = p_id;
  if not found or m.status = 'draft' then return; end if;
  select email_address into v_addr from public.mailboxes where mailbox_id = m.mailbox_id;
  insert into public.mail_archive (email_message_id, mailbox_id, mailbox_address, direction, status_at_archive, message_id, in_reply_to,
      references_header, from_address, from_name, subject, text_body, html_body, sent_at, received_at, resend_email_id, content_sha256)
  values (m.email_message_id, m.mailbox_id, v_addr, m.direction::text, m.status::text, m.message_id, m.in_reply_to, m.references_header,
      m.from_address, m.from_name, m.subject, m.text_body, m.html_body, m.sent_at, m.received_at, m.resend_email_id,
      encode(extensions.digest(coalesce(m.from_address,'') || E'\n' || coalesce(m.subject,'') || E'\n' || coalesce(m.text_body,'') || E'\n' || coalesce(m.html_body,'') || E'\n' || coalesce(m.message_id,''), 'sha256'), 'hex'))
  on conflict (email_message_id) do nothing;
  insert into public.mail_archive_recipients (email_message_id, recipient_type, email_address, display_name)
  select r.email_message_id, r.recipient_type::text, r.email_address, r.display_name from public.email_recipients r where r.email_message_id = p_id
  on conflict do nothing;
end $$;
revoke all on function private.archive_email_message(bigint) from public, anon, authenticated;

create or replace function private.archive_on_message() returns trigger language plpgsql security definer set search_path = public, private as $$
begin perform private.archive_email_message(new.email_message_id); return null; end $$;
create trigger email_messages_archive_ins after insert on public.email_messages for each row when (new.status <> 'draft') execute function private.archive_on_message();
create trigger email_messages_archive_upd after update of status on public.email_messages for each row when (old.status = 'draft' and new.status <> 'draft') execute function private.archive_on_message();
create or replace function private.archive_on_recipient() returns trigger language plpgsql security definer set search_path = public, private as $$
begin perform private.archive_email_message(new.email_message_id); return null; end $$;
create trigger email_recipients_archive_ins after insert on public.email_recipients for each row execute function private.archive_on_recipient();

alter table public.mail_archive enable row level security;
alter table public.mail_archive_recipients enable row level security;
create policy mail_archive_admin_read on public.mail_archive for select to authenticated using ((select private.is_admin()));
create policy mail_archive_rcpt_admin_read on public.mail_archive_recipients for select to authenticated using ((select private.is_admin()));
create policy deny_anonymous_auth_sessions on public.mail_archive as restrictive for all to authenticated using ((select private.is_permanent_auth_user())) with check ((select private.is_permanent_auth_user()));
create policy deny_anonymous_auth_sessions on public.mail_archive_recipients as restrictive for all to authenticated using ((select private.is_permanent_auth_user())) with check ((select private.is_permanent_auth_user()));
revoke all on public.mail_archive, public.mail_archive_recipients from anon;
revoke insert, update, delete, truncate on public.mail_archive, public.mail_archive_recipients from authenticated;

select private.archive_email_message(email_message_id) from public.email_messages where status <> 'draft';

drop policy if exists customer_addresses_admin_read on public.customer_addresses;
drop policy if exists customer_addresses_owner_all on public.customer_addresses;
create policy customer_addresses_select on public.customer_addresses for select to authenticated
  using (user_id = (select u.id from public.users u where u.auth_user_id = (select auth.uid())) or (select private.is_admin()));
create policy customer_addresses_insert on public.customer_addresses for insert to authenticated
  with check (user_id = (select u.id from public.users u where u.auth_user_id = (select auth.uid())));
create policy customer_addresses_update on public.customer_addresses for update to authenticated
  using (user_id = (select u.id from public.users u where u.auth_user_id = (select auth.uid())))
  with check (user_id = (select u.id from public.users u where u.auth_user_id = (select auth.uid())));
create policy customer_addresses_delete on public.customer_addresses for delete to authenticated
  using (user_id = (select u.id from public.users u where u.auth_user_id = (select auth.uid())));
