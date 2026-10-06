# RT Crackers production database audit — 2026-10-07

## Result
The live Supabase project was audited and targeted performance/security improvements were applied.

### Data structure verified
- 127 active products.
- 18 active product categories; all 127 products are linked to `public.product_categories` through `products.category_id`.
- 1 active company profile row.
- 10 active RT Crackers business mailboxes.
- 22 active theme page records.
- 143 active theme component records.
- No orders or email messages currently stored; those tables are ready for production traffic.

### Performance changes applied
- Added indexes covering previously unindexed foreign keys: mail purpose, mail message mailbox, mail recipient message, and order tracking actor.
- Added product/category lookup indexes used by active catalogue and category rendering.
- Added mail message mailbox/date and recipient/message indexes.
- Optimized RLS policies by using separate operation policies and cached `select auth.uid()` / `select private.is_admin()` patterns where appropriate.
- Combined authenticated read policies so admin and customer checks do not require overlapping permissive SELECT policies.
- Added `admin_access.auth_user_id` and an index, then changed administrator lookup to use the authenticated UUID.
- Ran `ANALYZE` on the main production tables after schema/index changes.

### Remaining advisor notice
Supabase's performance advisor now reports only `unused_index` informational notices. The project is very new and has little/no traffic, so these are usage observations, not proof that the indexes should be removed. Dropping them now could make later RLS/catalog/order/mail queries slower. Revisit after real production traffic has accumulated.

### Security notes
The public catalog/profile/theme reads intentionally allow the `anon` role, because the customer and portfolio sites must work before sign-in. Sensitive mail/admin/order data is protected by authenticated/admin policies.

Supabase still reports a warning that leaked-password protection is disabled. Enable that in Supabase Authentication password security settings before production authentication is considered fully hardened:
https://supabase.com/docs/guides/auth/password-security#password-strength-and-leaked-password-protection

Supabase RLS guidance recommends indexing policy filter columns and wrapping fixed auth helper calls with `select`; the current RLS design follows those patterns:
https://supabase.com/docs/guides/database/postgres/row-level-security

### Category management model
`public.product_categories` is now the source of truth for category name, slug, description, details, image URL, alt text, order and active state. A trigger keeps the legacy `products.category` text synchronized when an admin renames a category, preserving compatibility with existing product/order snapshots.
