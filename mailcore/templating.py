"""Template rendering for automatic e-mail. Standard library only, so it is easy to test.

Syntax (deliberately tiny and safe: no code can run inside a template):
    {{name}}              value, HTML-escaped in the HTML part
    {{{name}}}            trusted HTML block (only for names in `raw_allowed`, otherwise escaped like {{name}})
    {{#if name}}...{{/if}} shown only when `name` has a value (may be nested)
Unknown names render as nothing."""
import html
import re

_IF = re.compile(r"\{\{#if\s+([a-z_][a-z0-9_]*)\s*\}\}((?:(?!\{\{#if\b).)*?)\{\{/if\}\}", re.S)
_RAW = re.compile(r"\{\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}\}")
_VAR = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_ANY = re.compile(r"\{\{\{?\s*#?(?:if\s+)?([a-z_][a-z0-9_]*)")

# Things an administrator must never be able to put in a template body.
_FORBIDDEN = re.compile(r"<\s*(script|iframe|object|embed|form|link|meta|base)\b|javascript:|\bon[a-z]+\s*=", re.I)

STATUS_LABELS = {
    "pending": "Received", "confirmed": "Confirmed", "processing": "Being prepared", "packed": "Packed",
    "shipped": "Dispatched", "out_for_delivery": "Out for delivery", "delivered": "Delivered", "cancelled": "Cancelled",
    "returned": "Returned", "QUOTATION_SENT": "Quotation sent", "PAYMENT_VERIFIED": "Payment verified",
    "PACKED_AT_GODOWN": "Packed at our godown", "DISPATCHED_LORRY": "Dispatched by lorry",
}
STATUS_MESSAGES = {
    "pending": "We have received your order and will confirm it shortly.",
    "confirmed": "Your order is confirmed. We will start preparing it now.",
    "processing": "We are preparing your order.",
    "packed": "Your order has been packed and is ready for dispatch.",
    "shipped": "Your order has left our godown and is on its way.",
    "out_for_delivery": "Your order is out for delivery. Please keep your phone nearby.",
    "delivered": "Your order has been delivered. Thank you for shopping with us!",
    "cancelled": "Your order has been cancelled. If you did not ask for this, please contact us right away.",
    "returned": "Your order has been returned to us. We will contact you about the next steps.",
    "QUOTATION_SENT": "We have sent you a quotation. Please check it and let us know.",
    "PAYMENT_VERIFIED": "We have verified your payment. Thank you!",
    "PACKED_AT_GODOWN": "Your order is packed at our godown and waiting for transport.",
    "DISPATCHED_LORRY": "Your order has been handed to the transport lorry.",
}


def _has(value) -> bool:
    return value is not None and str(value).strip() != ""


def render(template: str, variables: dict, *, html_mode: bool, raw_allowed: frozenset | set = frozenset()) -> str:
    out = template or ""
    for _ in range(12):  # innermost {{#if}} first, so nesting works
        replaced = _IF.sub(lambda m: m.group(2) if _has(variables.get(m.group(1))) else "", out)
        if replaced == out:
            break
        out = replaced

    def esc(v) -> str:
        s = "" if v is None else str(v)
        return html.escape(s, quote=True) if html_mode else s

    def raw(m):
        v = variables.get(m.group(1))
        if m.group(1) in raw_allowed:
            return "" if v is None else str(v)
        return esc(v)

    out = _RAW.sub(raw, out)
    return _VAR.sub(lambda m: esc(variables.get(m.group(1))), out)


def render_subject(template: str, variables: dict) -> str:
    """Plain text, one line, no header injection."""
    return re.sub(r"\s+", " ", render(template, variables, html_mode=False)).strip()[:300] or "(no subject)"


def used_names(*templates: str) -> set[str]:
    names = set()
    for t in templates:
        names.update(m.group(1) for m in _ANY.finditer(t or ""))
    return names


def validate(subject: str, body_html: str, body_text: str) -> list[str]:
    """Problems that must stop a template from being saved (empty list = fine)."""
    problems = []
    if not (subject or "").strip():
        problems.append("The subject cannot be empty.")
    if not (body_html or "").strip():
        problems.append("The message cannot be empty.")
    for label, text in (("subject", subject), ("message", body_html), ("plain-text version", body_text)):
        if text and text.count("{{#if") != text.count("{{/if}}"):
            problems.append(f"The {label} has an {{{{#if}}}} without a matching {{{{/if}}}}.")
    if _FORBIDDEN.search(body_html or ""):
        problems.append("Scripts, forms, embedded frames and event handlers are not allowed in e-mail templates.")
    return problems


def nl2br(text) -> str:
    return html.escape("" if text is None else str(text), quote=True).replace("\r\n", "\n").replace("\n", "<br>")


def format_inr(value) -> str:
    """12400 -> ₹12,400.00 with Indian digit grouping (₹2,25,000.00)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    sign = "-" if n < 0 else ""
    whole, frac = f"{abs(n):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    if head:
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return f"{sign}₹{whole}.{frac}"


def items_html(items: list[dict]) -> str:
    rows = "".join(
        f'<tr><td style="padding:8px 6px;border-bottom:1px solid #e4dfda">{html.escape(str(i.get("name", "")))}</td>'
        f'<td style="padding:8px 6px;border-bottom:1px solid #e4dfda;text-align:center">× {html.escape(str(i.get("quantity", "")))}</td>'
        f'<td style="padding:8px 6px;border-bottom:1px solid #e4dfda;text-align:right">{html.escape(format_inr(i.get("line_total")))}</td></tr>'
        for i in items
    )
    return f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;margin:14px 0;font-size:14px">{rows}</table>'


def items_text(items: list[dict]) -> str:
    return "\n".join(f'{i.get("quantity")} × {i.get("name")}  {format_inr(i.get("line_total"))}' for i in items) + "\n"


def wrap_layout(inner_html: str, *, company: str, phone: str, email: str, site_url: str) -> str:
    """The branded frame around every automatic mail. Inner HTML is already escaped/trusted by render()."""
    c, p, e, u = (html.escape(x or "", quote=True) for x in (company, phone, email, site_url))
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;padding:0;background:#f6f4f2;font-family:Arial,Helvetica,sans-serif;color:#1d1b1a">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f6f4f2"><tr><td align="center" style="padding:20px 10px">'
        '<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden">'
        f'<tr><td style="background:#c1121f;color:#ffffff;padding:18px 24px;font-size:20px;font-weight:bold">{c}</td></tr>'
        f'<tr><td style="padding:24px;font-size:15px;line-height:1.55">{inner_html}</td></tr>'
        f'<tr><td style="padding:16px 24px;background:#faf8f6;color:#6b6560;font-size:12px;line-height:1.5">{c}'
        + (f' · {p}' if p else "") + (f' · <a href="mailto:{e}" style="color:#6b6560">{e}</a>' if e else "")
        + (f' · <a href="{u}" style="color:#6b6560">{u}</a>' if u else "")
        + '<br>This message was sent automatically. You can reply to it and our team will see your reply.</td></tr>'
        '</table></td></tr></table></body></html>'
    )
