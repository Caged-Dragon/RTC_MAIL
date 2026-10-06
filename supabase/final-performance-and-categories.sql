-- RT Crackers targeted performance indexes used by the production schema.
create index if not exists email_messages_mailbox_folder_created_idx
  on public.email_messages (mailbox_id,folder,created_at desc);
create index if not exists email_recipients_message_idx
  on public.email_recipients (email_message_id);
create index if not exists business_email_addresses_purpose_idx
  on public.business_email_addresses (email_purpose_id);
create index if not exists order_tracking_changed_by_idx
  on public.order_tracking (changed_by_user_id);
create index if not exists order_tracking_order_created_idx
  on public.order_tracking (order_id,created_at desc);
