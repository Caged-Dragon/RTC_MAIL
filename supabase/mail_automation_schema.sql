-- Automatic e-mail: ready-made templates, a send queue and the events that fill it.

create table if not exists public.mail_templates (
  template_key text primary key check (template_key ~ '^[a-z][a-z0-9_]{2,60}$'),
  name text not null check (char_length(name) between 1 and 120),
  category text not null default 'general' check (category in ('orders','support','account','sales','billing','general')),
  mailbox_local_part text not null default 'noreply' check (mailbox_local_part ~ '^[a-z][a-z0-9._-]{1,40}$'),
  subject text not null check (char_length(subject) between 1 and 300),
  body_html text not null check (char_length(body_html) <= 100000),
  body_text text not null default '' check (char_length(body_text) <= 50000),
  variables jsonb not null default '[]'::jsonb,
  is_active boolean not null default true,
  is_system boolean not null default false,
  updated_by uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
drop trigger if exists mail_templates_set_updated_at on public.mail_templates;
create trigger mail_templates_set_updated_at before update on public.mail_templates for each row execute function public.set_generic_updated_at();
alter table public.mail_templates enable row level security;
create policy mail_templates_admin_select on public.mail_templates for select to authenticated using ((select private.is_admin()));
create policy mail_templates_admin_insert on public.mail_templates for insert to authenticated with check ((select private.is_admin()));
create policy mail_templates_admin_update on public.mail_templates for update to authenticated using ((select private.is_admin())) with check ((select private.is_admin()));
create policy mail_templates_admin_delete on public.mail_templates for delete to authenticated using ((select private.is_admin()) and not is_system);
create policy deny_anonymous_auth_sessions on public.mail_templates as restrictive for all to authenticated using ((select private.is_permanent_auth_user())) with check ((select private.is_permanent_auth_user()));
revoke all on public.mail_templates from anon;

create table if not exists public.mail_outbox (
  outbox_id bigint generated always as identity primary key,
  template_key text not null references public.mail_templates(template_key) on update cascade,
  to_email text not null check (to_email ~* '^[^@\s]+@[^@\s]+\.[^@\s]{2,}$'),
  to_name text,
  variables jsonb not null default '{}'::jsonb,
  related_type text,
  related_id text,
  status text not null default 'queued' check (status in ('queued','sending','sent','failed','skipped')),
  attempts integer not null default 0,
  next_attempt_at timestamptz not null default now(),
  locked_until timestamptz,
  last_error text,
  sent_message_id bigint,
  idempotency_key text unique,
  created_at timestamptz not null default now(),
  sent_at timestamptz
);
create index if not exists mail_outbox_due_idx on public.mail_outbox (next_attempt_at) where status in ('queued','sending');
create index if not exists mail_outbox_related_idx on public.mail_outbox (related_type, related_id);
create index if not exists mail_outbox_template_idx on public.mail_outbox (template_key);
alter table public.mail_outbox enable row level security;
create policy mail_outbox_admin_select on public.mail_outbox for select to authenticated using ((select private.is_admin()));
create policy deny_anonymous_auth_sessions on public.mail_outbox as restrictive for all to authenticated using ((select private.is_permanent_auth_user())) with check ((select private.is_permanent_auth_user()));
revoke all on public.mail_outbox from anon;
revoke insert, update, delete, truncate on public.mail_outbox from authenticated;

create table if not exists public.support_requests (
  id uuid primary key default gen_random_uuid(),
  reference text not null unique,
  name text not null, email text not null, phone text,
  subject text not null, message text not null,
  order_number bigint,
  status text not null default 'open' check (status in ('open','answered','closed')),
  created_at timestamptz not null default now()
);
create index if not exists support_requests_email_time_idx on public.support_requests (lower(email), created_at desc);
alter table public.support_requests enable row level security;
create policy support_requests_admin_select on public.support_requests for select to authenticated using ((select private.is_admin()));
create policy support_requests_admin_update on public.support_requests for update to authenticated using ((select private.is_admin())) with check ((select private.is_admin()));
create policy deny_anonymous_auth_sessions on public.support_requests as restrictive for all to authenticated using ((select private.is_permanent_auth_user())) with check ((select private.is_permanent_auth_user()));
revoke all on public.support_requests from anon;
revoke insert, delete, truncate on public.support_requests from authenticated;

create table if not exists private.mail_runtime_config (key text primary key, value text not null default '');
alter table private.mail_runtime_config enable row level security;
insert into private.mail_runtime_config (key, value) values ('outbox_url', 'https://mail.rtcrackers.com/api/outbox/run'), ('outbox_secret', '')
on conflict (key) do nothing;

-- Queue a mail. Never raises: a mail problem must never break an order or a sign-up.
create or replace function private.enqueue_mail(p_template text, p_to text, p_name text, p_vars jsonb, p_rel_type text, p_rel_id text, p_key text, p_delay interval default '0')
returns bigint language plpgsql security definer set search_path = public, private as $$
declare v_id bigint;
begin
  if p_to is null or p_to !~* '^[^@\s]+@[^@\s]+\.[^@\s]{2,}$' then return null; end if;
  if not exists (select 1 from public.mail_templates where template_key = p_template and is_active) then return null; end if;
  insert into public.mail_outbox (template_key, to_email, to_name, variables, related_type, related_id, idempotency_key, next_attempt_at)
  values (p_template, lower(trim(p_to)), nullif(trim(coalesce(p_name, '')), ''), coalesce(p_vars, '{}'::jsonb), p_rel_type, p_rel_id, p_key, now() + p_delay)
  on conflict (idempotency_key) do nothing returning outbox_id into v_id;
  return v_id;
exception when others then
  raise warning 'enqueue_mail failed: %', sqlerrm;
  return null;
end $$;
revoke all on function private.enqueue_mail(text, text, text, jsonb, text, text, text, interval) from public, anon, authenticated;

-- Administrators (and the service role) can queue any template to anyone from the apps.
create or replace function public.enqueue_mail(p_template text, p_to_email text, p_to_name text default null, p_variables jsonb default '{}'::jsonb,
  p_related_type text default null, p_related_id text default null, p_idempotency_key text default null)
returns bigint language plpgsql security definer set search_path = public, private as $$
begin
  if not (private.is_admin() or coalesce((select auth.role()), '') = 'service_role') then raise exception 'Not allowed'; end if;
  return private.enqueue_mail(p_template, p_to_email, p_to_name, p_variables, p_related_type, p_related_id, p_idempotency_key);
end $$;
revoke all on function public.enqueue_mail(text, text, text, jsonb, text, text, text) from public, anon;
grant execute on function public.enqueue_mail(text, text, text, jsonb, text, text, text) to authenticated, service_role;

-- Orders: confirmation when placed, update on every status change, review request 3 days after delivery.
create or replace function private.orders_enqueue_mail() returns trigger
language plpgsql security definer set search_path = public, private as $$
begin
  if tg_op = 'INSERT' then
    perform private.enqueue_mail('order_confirmation', new.customer_email, new.shipping_first_name,
      jsonb_build_object('order_id', new.id::text), 'order', new.id::text, 'order_confirmation:' || new.id);
  elsif new.status::text is distinct from old.status::text then
    perform private.enqueue_mail('order_status_update', new.customer_email, new.shipping_first_name,
      jsonb_build_object('order_id', new.id::text, 'status', new.status::text), 'order', new.id::text, 'order_status:' || new.id || ':' || new.status::text);
    if new.status::text = 'delivered' then
      perform private.enqueue_mail('order_review_request', new.customer_email, new.shipping_first_name,
        jsonb_build_object('order_id', new.id::text), 'order', new.id::text, 'order_review:' || new.id, interval '3 days');
    end if;
  end if;
  return null;
exception when others then
  raise warning 'orders_enqueue_mail failed: %', sqlerrm;
  return null;
end $$;
drop trigger if exists orders_enqueue_mail_ins on public.orders;
create trigger orders_enqueue_mail_ins after insert on public.orders for each row execute function private.orders_enqueue_mail();
drop trigger if exists orders_enqueue_mail_upd on public.orders;
create trigger orders_enqueue_mail_upd after update of status on public.orders for each row execute function private.orders_enqueue_mail();

-- Welcome mail for new customer accounts.
create or replace function private.users_enqueue_welcome() returns trigger
language plpgsql security definer set search_path = public, private as $$
begin
  perform private.enqueue_mail('welcome', new.email, new.first_name, jsonb_build_object('customer_name', coalesce(new.first_name, '')), 'user', new.id::text, 'welcome:' || new.id);
  return null;
exception when others then
  raise warning 'users_enqueue_welcome failed: %', sqlerrm;
  return null;
end $$;
drop trigger if exists users_enqueue_welcome_ins on public.users;
create trigger users_enqueue_welcome_ins after insert on public.users for each row when (new.is_customer is true) execute function private.users_enqueue_welcome();

-- Support form on the shop: saves the request, thanks the customer, alerts the support inbox.
create or replace function public.submit_support_request(p_name text, p_email text, p_phone text, p_subject text, p_message text, p_order_number bigint default null)
returns jsonb language plpgsql security definer set search_path = public, private as $$
declare v_email text := lower(trim(coalesce(p_email, ''))); v_name text := trim(coalesce(p_name, '')); v_subject text := trim(coalesce(p_subject, ''));
        v_message text := trim(coalesce(p_message, '')); v_phone text := nullif(right(regexp_replace(coalesce(p_phone, ''), '\D', '', 'g'), 10), '');
        v_id uuid := gen_random_uuid(); v_ref text; v_n integer;
begin
  if char_length(v_name) not between 1 and 80 then raise exception 'Please enter your name'; end if;
  if v_email !~* '^[^@\s]+@[^@\s]+\.[^@\s]{2,}$' or char_length(v_email) > 200 then raise exception 'Please enter a valid email address'; end if;
  if v_phone is not null and char_length(v_phone) <> 10 then raise exception 'Mobile number must be 10 digits'; end if;
  if char_length(v_subject) not between 3 and 150 then raise exception 'Subject must be 3 to 150 characters'; end if;
  if char_length(v_message) not between 10 and 4000 then raise exception 'Message must be 10 to 4000 characters'; end if;
  select count(*) into v_n from public.support_requests where lower(email) = v_email and created_at > now() - interval '1 hour';
  if v_n >= 3 then raise exception 'You have sent several messages already. Please wait a little before sending another.'; end if;
  select count(*) into v_n from public.support_requests where created_at > now() - interval '1 hour';
  if v_n >= 100 then raise exception 'We are receiving a very high volume of messages. Please try again shortly.'; end if;
  v_ref := 'SR-' || upper(substr(replace(v_id::text, '-', ''), 1, 8));
  insert into public.support_requests (id, reference, name, email, phone, subject, message, order_number) values (v_id, v_ref, v_name, v_email, v_phone, v_subject, v_message, p_order_number);
  perform private.enqueue_mail('support_auto_reply', v_email, v_name, jsonb_build_object('customer_name', v_name, 'subject', v_subject, 'reference', v_ref), 'support', v_id::text, 'support_auto:' || v_id);
  perform private.enqueue_mail('support_internal_alert', 'support@rtcrackers.com', 'Support', jsonb_build_object('customer_name', v_name, 'customer_email', v_email,
    'customer_phone', coalesce(v_phone, ''), 'subject', v_subject, 'message', v_message, 'reference', v_ref, 'reply_to', v_email), 'support', v_id::text, 'support_alert:' || v_id);
  return jsonb_build_object('reference', v_ref);
end $$;
revoke all on function public.submit_support_request(text, text, text, text, text, bigint) from public;
grant execute on function public.submit_support_request(text, text, text, text, text, bigint) to anon, authenticated;
