-- Ready-made templates. The wrapper (logo bar, footer with your contact details) is added automatically when sending,
-- so each body below holds only the message itself. {{x}} = text (escaped), {{{x}}} = trusted HTML block, {{#if x}}...{{/if}} = only when x has a value.
insert into public.mail_templates (template_key, name, category, mailbox_local_part, subject, body_html, body_text, variables, is_system) values
('order_confirmation', 'Order confirmation', 'orders', 'orders',
 'Order #{{order_number}} confirmed | {{company_name}}',
 $tpl$<h2 style="margin:0 0 12px">Thank you, {{customer_name}}!</h2>
<p>We have received your order <b>#{{order_number}}</b>. Our team will confirm it shortly.</p>
{{{items_html}}}
<p style="text-align:right;font-size:16px"><b>Total: {{order_total}}</b><br><span style="color:#6b6560">Payment: {{payment_method}}</span></p>
<p><b>Delivery address</b><br>{{shipping_address}}</p>
{{#if tracking_url}}<p style="margin:20px 0"><a href="{{tracking_url}}" style="background:#c1121f;color:#ffffff;padding:11px 20px;border-radius:8px;text-decoration:none;font-weight:bold">Track your order</a></p>{{/if}}
<p>Questions? Just reply to this email or call {{company_phone}}.</p>$tpl$,
 E'Thank you, {{customer_name}}!\n\nWe have received your order #{{order_number}}.\n\n{{items_text}}\nTotal: {{order_total}} ({{payment_method}})\n\nDelivery address:\n{{shipping_address}}\n\n{{#if tracking_url}}Track your order: {{tracking_url}}\n\n{{/if}}Questions? Reply to this email or call {{company_phone}}.',
 '[{"name":"customer_name","example":"Ravi"},{"name":"order_number","example":"1042"},{"name":"order_total","example":"₹12,400.00"},{"name":"payment_method","example":"Cash on Delivery"},{"name":"shipping_address","example":"12 Main Road, Chennai, Tamil Nadu 600001"},{"name":"items_html","example":"(order lines table, built automatically)","raw":true},{"name":"items_text","example":"2 × Flower Pots Deluxe"},{"name":"tracking_url","example":"https://cart.rtcrackers.com/track-order?n=1042&t=…"}]', true),

('order_status_update', 'Order status update', 'orders', 'orders',
 'Order #{{order_number}}: {{status_label}}',
 $tpl$<h2 style="margin:0 0 12px">Hello {{customer_name}},</h2>
<p>Your order <b>#{{order_number}}</b> is now <b>{{status_label}}</b>.</p>
<p>{{status_message}}</p>
{{#if lr_number}}<p><b>Lorry receipt (LR) number:</b> {{lr_number}}{{#if transporter_name}}<br><b>Transport:</b> {{transporter_name}}{{/if}}</p>{{/if}}
{{#if tracking_url}}<p style="margin:20px 0"><a href="{{tracking_url}}" style="background:#c1121f;color:#ffffff;padding:11px 20px;border-radius:8px;text-decoration:none;font-weight:bold">See full status</a></p>{{/if}}
<p>Need help? Reply to this email or call {{company_phone}}.</p>$tpl$,
 E'Hello {{customer_name}},\n\nYour order #{{order_number}} is now {{status_label}}.\n\n{{status_message}}\n\n{{#if lr_number}}LR number: {{lr_number}}\n{{/if}}{{#if tracking_url}}Full status: {{tracking_url}}\n\n{{/if}}Need help? Reply to this email or call {{company_phone}}.',
 '[{"name":"customer_name","example":"Ravi"},{"name":"order_number","example":"1042"},{"name":"status_label","example":"Dispatched"},{"name":"status_message","example":"Your parcel has left our godown."},{"name":"lr_number","example":"LR-88231"},{"name":"transporter_name","example":"ARC Parcels"},{"name":"tracking_url","example":"https://cart.rtcrackers.com/track-order?n=1042&t=…"}]', true),

('order_review_request', 'Review request (3 days after delivery)', 'orders', 'orders',
 'How was your order #{{order_number}}?',
 $tpl$<h2 style="margin:0 0 12px">Hello {{customer_name}},</h2>
<p>We hope your order <b>#{{order_number}}</b> arrived safely and you enjoyed it. Would you take a minute to tell us how we did?</p>
{{#if review_url}}<p style="margin:20px 0"><a href="{{review_url}}" style="background:#c1121f;color:#ffffff;padding:11px 20px;border-radius:8px;text-decoration:none;font-weight:bold">Write a review</a></p>{{/if}}
<p>Thank you for choosing {{company_name}}.</p>$tpl$,
 E'Hello {{customer_name}},\n\nWe hope order #{{order_number}} arrived safely. Would you tell us how we did?\n{{#if review_url}}\nWrite a review: {{review_url}}\n{{/if}}\nThank you for choosing {{company_name}}.',
 '[{"name":"customer_name","example":"Ravi"},{"name":"order_number","example":"1042"},{"name":"review_url","example":"https://cart.rtcrackers.com/reviews"}]', true),

('support_auto_reply', 'Support: we received your message', 'support', 'support',
 'We received your message: {{subject}}',
 $tpl$<h2 style="margin:0 0 12px">Hello {{customer_name}},</h2>
<p>Thank you for contacting {{company_name}}. We have received your message and a member of our team will reply as soon as possible, usually within one working day.</p>
<p><b>Subject:</b> {{subject}}{{#if reference}}<br><b>Your reference:</b> {{reference}}{{/if}}</p>
<p>For anything urgent please call {{company_phone}}.</p>$tpl$,
 E'Hello {{customer_name}},\n\nThank you for contacting {{company_name}}. We have received your message and will reply as soon as possible, usually within one working day.\n\nSubject: {{subject}}\n{{#if reference}}Your reference: {{reference}}\n{{/if}}\nFor anything urgent please call {{company_phone}}.',
 '[{"name":"customer_name","example":"Ravi"},{"name":"subject","example":"Question about delivery"},{"name":"reference","example":"SR-1A2B3C4D"}]', true),

('support_internal_alert', 'Support: new request (internal alert)', 'support', 'noreply',
 '[Support] {{subject}} from {{customer_name}}',
 $tpl$<h2 style="margin:0 0 12px">New support request</h2>
<p><b>From:</b> {{customer_name}} &lt;{{customer_email}}&gt;{{#if customer_phone}}<br><b>Phone:</b> {{customer_phone}}{{/if}}<br><b>Reference:</b> {{reference}}</p>
<p><b>{{subject}}</b></p>
<p style="background:#f6f4f2;padding:12px;border-radius:8px">{{{message_html}}}</p>
<p style="color:#6b6560">Reply to this email to answer the customer directly.</p>$tpl$,
 E'New support request {{reference}}\nFrom: {{customer_name}} <{{customer_email}}> {{customer_phone}}\n\n{{subject}}\n\n{{message}}\n\nReply to this email to answer the customer.',
 '[{"name":"customer_name","example":"Ravi"},{"name":"customer_email","example":"ravi@example.com"},{"name":"customer_phone","example":"9876543210"},{"name":"subject","example":"Question about delivery"},{"name":"message","example":"When will my order arrive?"},{"name":"reference","example":"SR-1A2B3C4D"}]', true),

('welcome', 'Welcome to your new account', 'account', 'account',
 'Welcome to {{company_name}}',
 $tpl$<h2 style="margin:0 0 12px">Welcome, {{customer_name}}!</h2>
<p>Your {{company_name}} account is ready. You can now order online, save your details and follow every order in one place.</p>
<p style="margin:20px 0"><a href="{{shop_url}}" style="background:#c1121f;color:#ffffff;padding:11px 20px;border-radius:8px;text-decoration:none;font-weight:bold">Start shopping</a></p>
<p>Need help? Reply to this email or call {{company_phone}}.</p>$tpl$,
 E'Welcome, {{customer_name}}!\n\nYour {{company_name}} account is ready. Start shopping: {{shop_url}}\n\nNeed help? Reply to this email or call {{company_phone}}.',
 '[{"name":"customer_name","example":"Ravi"},{"name":"shop_url","example":"https://cart.rtcrackers.com"}]', true),

('quotation', 'Quotation / price offer', 'sales', 'sales',
 'Your quotation from {{company_name}}',
 $tpl$<h2 style="margin:0 0 12px">Hello {{customer_name}},</h2>
<p>Thank you for your enquiry. Here is your quotation:</p>
<p style="background:#f6f4f2;padding:14px;border-radius:8px">{{{message_html}}}</p>
{{#if valid_until}}<p><b>Valid until:</b> {{valid_until}}</p>{{/if}}
<p>To confirm, reply to this email or call {{company_phone}}.</p>$tpl$,
 E'Hello {{customer_name}},\n\nThank you for your enquiry. Here is your quotation:\n\n{{message}}\n\n{{#if valid_until}}Valid until: {{valid_until}}\n\n{{/if}}To confirm, reply to this email or call {{company_phone}}.',
 '[{"name":"customer_name","example":"Ravi"},{"name":"message","example":"500 boxes Flower Pots Deluxe at ₹450 = ₹2,25,000"},{"name":"valid_until","example":"31 October 2026"}]', true),

('payment_reminder', 'Payment reminder', 'billing', 'billing',
 'Payment reminder for order #{{order_number}}',
 $tpl$<h2 style="margin:0 0 12px">Hello {{customer_name}},</h2>
<p>This is a friendly reminder about the payment for order <b>#{{order_number}}</b>{{#if amount_due}}: <b>{{amount_due}}</b> is due{{/if}}.</p>
{{#if message}}<p>{{{message_html}}}</p>{{/if}}
<p>If you have already paid, please ignore this message or reply with the payment details. Thank you!</p>$tpl$,
 E'Hello {{customer_name}},\n\nA friendly reminder about the payment for order #{{order_number}}{{#if amount_due}}: {{amount_due}} is due{{/if}}.\n\n{{#if message}}{{message}}\n\n{{/if}}If you have already paid, please ignore this message or reply with the payment details. Thank you!',
 '[{"name":"customer_name","example":"Ravi"},{"name":"order_number","example":"1042"},{"name":"amount_due","example":"₹12,400.00"},{"name":"message","example":"Please pay by UPI to rtcrackers@upi"}]', true),

('general_notice', 'General notice (any message)', 'general', 'info',
 '{{heading}}',
 $tpl$<h2 style="margin:0 0 12px">{{heading}}</h2>
<p>Hello {{customer_name}},</p>
<p>{{{message_html}}}</p>
<p>Warm regards,<br>{{company_name}}</p>$tpl$,
 E'{{heading}}\n\nHello {{customer_name}},\n\n{{message}}\n\nWarm regards,\n{{company_name}}',
 '[{"name":"heading","example":"Festival season timings"},{"name":"customer_name","example":"Ravi"},{"name":"message","example":"Our godown will be open 8am to 8pm until Diwali."}]', true)
on conflict (template_key) do nothing;
