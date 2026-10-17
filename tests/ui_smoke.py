"""Browser smoke test of the mail front end against a mock API (needs: pip install playwright && playwright install chromium).
Run:  python tests/ui_smoke.py"""
import json, os, re, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright

PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public")
TEMPLATES = {
    "welcome": {"template_key": "welcome", "name": "Welcome to your new account", "category": "account", "mailbox_local_part": "account", "subject": "Welcome to {{company_name}}",
                "body_html": "<p>Hi {{customer_name}}</p>", "body_text": "Hi {{customer_name}}", "is_active": True, "is_system": True, "sent_count": 3,
                "variables": [{"name": "customer_name", "example": "Ravi"}], "sample": {"customer_name": "Ravi"}},
    "quotation": {"template_key": "quotation", "name": "Quotation / price offer", "category": "sales", "mailbox_local_part": "sales", "subject": "Your quotation",
                  "body_html": "<p>{{{message_html}}}</p>", "body_text": "{{message}}", "is_active": True, "is_system": True, "sent_count": 0,
                  "variables": [{"name": "customer_name", "example": "Ravi"}, {"name": "message", "example": "500 boxes"}], "sample": {}},
}
CALLS = []
OUTBOX = [{"outbox_id": 9, "template_key": "order_confirmation", "to_email": "a@b.com", "status": "failed", "attempts": 6, "last_error": "Resend 403: key restricted",
           "created_at": "2026-10-12T10:00:00Z", "sent_at": None, "related_type": "order", "related_id": "x", "next_attempt_at": "2026-10-12T10:00:00Z"}]
CFG = {"domain": "rtcrackers.com", "user": "admin@rtcrackers.com", "is_admin": True, "resend_configured": True,
       "mailboxes": [{"id": m, "label": m.title(), "address": f"{m}@rtcrackers.com", "can_send": True} for m in ("admin", "sales", "support", "orders", "noreply", "info", "account", "billing")]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def out(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n = int(self.headers.get("Content-Length") or 0); return json.loads(self.rfile.read(n) or b"{}") if n else {}
    def route(self, m):
        p = self.path.split("?")[0]; b = self.body() if m in ("POST", "PUT") else None
        if not p.startswith("/api/"):
            f = os.path.join(PUBLIC, "index.html" if p == "/" else p.lstrip("/"))
            if not os.path.isfile(f): return self.send_error(404)
            data = open(f, "rb").read(); self.send_response(200)
            self.send_header("Content-Type", {"js": "text/javascript", "css": "text/css", "html": "text/html"}.get(f.rsplit(".", 1)[-1], "application/octet-stream")); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        p = p[4:]; CALLS.append((m, p, b))
        if p == "/config": return self.out(CFG)
        if p in ("/counts", "/counts/drafts"): return self.out({})
        if p == "/messages": return self.out([])
        if p == "/sync": return self.out({"new": 0})
        if p == "/templates" and m == "GET": return self.out(list(TEMPLATES.values()))
        if p == "/templates" and m == "POST":
            if b["key"] in TEMPLATES: return self.out({"detail": "A template with this key already exists."}, 422)
            TEMPLATES[b["key"]] = dict(b, template_key=b["key"], is_system=False, sent_count=0, sample={}); return self.out(TEMPLATES[b["key"]], 201)
        mt = re.match(r"^/templates/(\w+)(/preview|/send)?$", p)
        if mt:
            t = TEMPLATES.get(mt.group(1))
            if not t: return self.out({"detail": "Template not found"}, 404)
            if mt.group(2) == "/preview": return self.out({"subject": b.get("subject") or t["subject"], "html": "<html><body><h1>PREVIEW " + (b.get("body_html") or t["body_html"]) + "</h1></body></html>", "text": "x", "missing": []})
            if mt.group(2) == "/send": return self.out({"outbox_id": 1, "outcome": "sent", "status": "sent", "last_error": None, "sent_message_id": 5})
            if m == "PUT": t.update(b); return self.out(t)
            if m == "DELETE": TEMPLATES.pop(mt.group(1)); return self.out({})
            return self.out(t)
        if p == "/outbox" and m == "GET": return self.out(OUTBOX)
        if p == "/outbox/run": return self.out({"sent": 2, "retry": 0, "failed": 0, "remaining": 0})
        if re.match(r"^/outbox/\d+/retry$", p): OUTBOX[0]["status"] = "sent"; return self.out({"sent": 1, "retry": 0, "failed": 0, "remaining": 0})
        if p == "/compliance": return self.out({"domain": "rtcrackers.com", "status": "ok", "checks": [{"id": "mx", "title": "Inbound mail route", "status": "ok", "detail": "fine", "fix": ""}]})
        if p == "/receiving-test" and m == "POST": return self.out({"token": "abcdef0123", "to": "admin@rtcrackers.com"})
        if p == "/receiving-test": return self.out({"status": "key_problem", "message": "Your Resend API key is not allowed to read received mail."})
        self.out({"detail": "no route " + p}, 404)
    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")
    def do_PUT(self): self.route("PUT")
    def do_DELETE(self): self.route("DELETE")


srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
results = []


def case(fn):
    CALLS.clear()
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(); ctx = br.new_context(viewport={"width": 1300, "height": 900})
            ctx.add_init_script("try { localStorage.setItem(%s, JSON.stringify({access_token:'t', refresh_token:'r', email:'admin@rtcrackers.com'})) } catch (e) {}  /* the sandboxed preview frame has no storage, by design */" % json.dumps(os.environ.get("TOK", "rtmail.session")))
            errors = []; pg = ctx.new_page(); pg.set_default_timeout(6000)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(BASE); pg.wait_for_selector("#autoBtn:not([hidden])")
            fn(pg); assert not errors, errors; br.close()
        results.append((fn.__name__, True, ""))
    except Exception as e:
        results.append((fn.__name__, False, str(e).splitlines()[0][:300]))


@case
def admin_sees_both_panels_buttons(pg):
    assert pg.is_visible("#lockBtn") and pg.is_visible("#autoBtn")

@case
def templates_list_edit_preview_save(pg):
    pg.click("#autoBtn"); pg.wait_for_selector("text=Welcome to your new account")
    pg.click("button[data-k=welcome]"); pg.wait_for_selector("text=Message (HTML)")
    pg.fill(".au-form input[maxlength='300']", "Welcome, friend"); pg.click("text=Preview")
    pg.wait_for_selector("iframe.au-preview:not([hidden])"); assert "PREVIEW" in pg.get_attribute("iframe.au-preview", "srcdoc")
    assert pg.get_attribute("iframe.au-preview", "sandbox") == ""          # preview cannot run scripts
    pg.click("button.primary:has-text('Save')"); pg.wait_for_function("document.querySelector('.au-list')")
    put = [c for c in CALLS if c[0] == "PUT"][0]; assert put[2]["subject"] == "Welcome, friend" and put[2]["mailbox_local_part"] == "account"

@case
def new_template_validation_error_shown(pg):
    pg.click("#autoBtn"); pg.wait_for_selector("text=+ New template"); pg.click("text=+ New template"); pg.wait_for_selector("text=Key (letters")
    pg.fill(".au-form input[placeholder='e.g. festival_offer']", "welcome"); pg.fill(".au-form input[maxlength='120']", "Dup")
    pg.fill(".au-form input[maxlength='300']", "S"); pg.fill(".au-form textarea >> nth=0", "<p>x</p>"); pg.click("button.primary:has-text('Save')")
    pg.wait_for_selector("text=already exists")

@case
def send_to_anyone_form(pg):
    pg.click("#autoBtn"); pg.click(".lk-tabs button:has-text('Send to anyone')"); pg.wait_for_selector("text=Template")
    pg.select_option("select >> nth=0", "quotation"); pg.wait_for_selector("textarea")
    pg.fill("input[type=email]", "buyer@example.com"); pg.fill("textarea", "500 boxes at 450"); pg.click("button:has-text('Send now')")
    pg.wait_for_selector("text=Sent. A copy is in the Sent folder.")
    send = [c for c in CALLS if c[1] == "/templates/quotation/send"][0][2]
    assert send["to"] == "buyer@example.com" and send["variables"]["message"] == "500 boxes at 450"

@case
def outbox_shows_failure_and_retry(pg):
    pg.click("#autoBtn"); pg.click(".lk-tabs button:has-text('Sent automatically')"); pg.wait_for_selector("text=Resend 403: key restricted")
    pg.click("button:has-text('Retry')"); pg.wait_for_function("document.querySelector('.au-pill.sent')")

@case
def receiving_test_reports_key_problem(pg):
    pg.click("#lockBtn"); pg.wait_for_selector("text=Receiving test"); pg.click("button:has-text('Run receiving test')")
    pg.wait_for_selector("text=not allowed to read received mail", timeout=15000)

for n, ok, msg in results: print(("PASS " if ok else "FAIL ") + n + ("" if ok else "  " + msg))
bad = [r for r in results if not r[1]]; print(f"\n{len(results) - len(bad)}/{len(results)} passed"); sys.exit(1 if bad else 0)
