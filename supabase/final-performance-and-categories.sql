-- RT Crackers final additive database migration.
-- Category management + targeted performance indexes.
create table if not exists public.product_categories (
  category_id uuid primary key default gen_random_uuid(),
  category_key text not null unique,
  category_name text not null,
  slug text not null unique,
  description text,
  details text,
  image_url text,
  alt_text text,
  display_order integer not null default 0,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.products add column if not exists category_id uuid references public.product_categories(category_id);
insert into public.product_categories (category_key,category_name,slug,display_order)
select lower(regexp_replace(category,'[^a-zA-Z0-9]+','-','g')), min(category), lower(regexp_replace(min(category),'[^a-zA-Z0-9]+','-','g')), min(s_no)
from public.products where category is not null and btrim(category)<>'' group by lower(regexp_replace(category,'[^a-zA-Z0-9]+','-','g'))
on conflict(category_key) do update set category_name=excluded.category_name;
update public.products p set category_id=c.category_id from public.product_categories c where p.category_id is null and lower(regexp_replace(coalesce(p.category,''),'[^a-zA-Z0-9]+','-','g'))=c.category_key;
create index if not exists products_active_sno_idx on public.products (s_no,product_code) where is_active=true;
create index if not exists products_category_active_sno_idx on public.products (category_id,s_no) where is_active=true;
create index if not exists product_categories_active_order_idx on public.product_categories (display_order,category_id) where is_active=true;
create index if not exists email_messages_mailbox_folder_created_idx on public.email_messages (mailbox_id,folder,created_at desc);
create index if not exists email_recipients_message_idx on public.email_recipients (email_message_id);
create index if not exists business_email_addresses_purpose_idx on public.business_email_addresses (email_purpose_id);
create index if not exists order_tracking_changed_by_idx on public.order_tracking (changed_by_user_id);
create index if not exists order_tracking_order_created_idx on public.order_tracking (order_id,created_at desc);
create index if not exists orders_status_created_idx on public.orders (status,created_at desc);
create index if not exists offer_banners_active_order_idx on public.offer_banners (display_order,starts_at,ends_at) where is_active=true;
create index if not exists offers_active_window_idx on public.offers (starts_at,ends_at,display_order) where is_active=true;
alter table public.product_categories enable row level security;
revoke all on public.product_categories from anon,authenticated;
grant select on public.product_categories to anon,authenticated;
grant select,insert,update,delete on public.product_categories to authenticated;
drop policy if exists product_categories_public_read on public.product_categories;
create policy product_categories_public_read on public.product_categories for select to anon using (is_active=true);
drop policy if exists product_categories_admin_write on public.product_categories;
create policy product_categories_admin_write on public.product_categories for all to authenticated using ((select private.is_admin())) with check ((select private.is_admin()));
create or replace function public.sync_product_category_name() returns trigger language plpgsql set search_path=public as $$ begin update public.products set category=new.category_name,updated_at=now() where category_id=new.category_id; new.updated_at=now(); return new; end $$;
drop trigger if exists trg_sync_product_category_name on public.product_categories;
create trigger trg_sync_product_category_name before update of category_name on public.product_categories for each row execute function public.sync_product_category_name();
analyze public.products; analyze public.product_categories; analyze public.orders; analyze public.order_items; analyze public.order_tracking; analyze public.email_messages; analyze public.email_recipients;
