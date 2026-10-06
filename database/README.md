# RT Crackers Database final notes

The production Supabase project is the source of truth for the four websites.
The final database contains the commerce tables, product_categories, theme_page_settings,
theme_component_settings, and the ten RT Crackers mailboxes.

The live project has already received the category/theme/mail/performance changes used by this codebase.
Do not run an old migration bundle over production blindly. Use the migration history or apply only the
missing additive statements after checking the current schema.

Key runtime sources:
- Products: public.products
- Categories: public.product_categories
- Company: public.company_profile
- Merchant settings: public.merchant_settings
- Offers/banners: public.offers / public.offer_banners
- Theme pages: public.theme_page_settings
- Theme components: public.theme_component_settings
- Mailboxes/messages: public.business_email_addresses / public.mailboxes / public.email_messages / public.email_recipients
- Admin authorization: public.admin_access + private.is_admin()
