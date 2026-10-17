"""Automatic e-mail: the built-in templates render cleanly, and the auto-reply rules are safe. No database needed."""
import re
import unittest
from pathlib import Path

from mailcore import automation, templating

SEED = (Path(__file__).resolve().parent.parent / "supabase" / "mail_templates_seed.sql").read_text()
ROW = re.compile(
    r"\('(?P<key>\w+)', '(?P<name>[^']*)', '(?P<cat>\w+)', '(?P<box>\w+)',\s*'(?P<subject>[^']*)',\s*\$tpl\$(?P<html>.*?)\$tpl\$,\s*"
    r"E'(?P<text>(?:[^'\\]|\\.)*)',\s*'(?P<vars>\[.*?\])', true\)", re.S)


def seed_templates():
    import json
    out = {}
    for m in ROW.finditer(SEED):
        out[m["key"]] = {"template_key": m["key"], "name": m["name"], "category": m["cat"], "mailbox_local_part": m["box"], "subject": m["subject"],
                         "body_html": m["html"], "body_text": m["text"].replace("\\n", "\n"),
                         "variables": json.loads(m["vars"])}
    return out


class SeedTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = seed_templates()
        automation.company_vars = lambda: {"company_name": "RT Crackers", "company_phone": "+91 99999 00000", "company_email": "support@rtcrackers.com",
                                           "site_url": "https://cart.rtcrackers.com", "shop_url": "https://cart.rtcrackers.com", "year": "2026"}

    def test_all_nine_templates_found(self):
        self.assertEqual(set(self.t), {"order_confirmation", "order_status_update", "order_review_request", "support_auto_reply",
                                       "support_internal_alert", "welcome", "quotation", "payment_reminder", "general_notice"})

    def test_every_template_is_valid_and_renders_without_leftovers(self):
        for key, tpl in self.t.items():
            with self.subTest(key):
                self.assertEqual(templating.validate(tpl["subject"], tpl["body_html"], tpl["body_text"]), [])
                p = automation.preview(tpl)
                for part in ("subject", "html", "text"):
                    self.assertNotIn("{{", p[part], f"{key}: unresolved placeholder in {part}")
                self.assertTrue(p["subject"].strip() and p["text"].strip())
                self.assertIn("RT Crackers", p["html"])                       # branded wrapper applied
                self.assertEqual(p["missing"], [], f"{key} uses variables that no example provides")

    def test_customer_data_cannot_inject_html(self):
        evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
        for key in ("support_internal_alert", "general_notice", "quotation", "order_confirmation"):
            with self.subTest(key):
                v = {**automation.sample_variables(self.t[key]), "message": evil, "customer_name": evil, "subject": evil, "heading": evil}
                html = automation.preview(self.t[key], v)["html"]
                self.assertNotIn("<script>", html)
                self.assertNotIn("<img src=x", html)

    def test_status_update_uses_the_status_that_triggered_it(self):
        v = {**automation.sample_variables(self.t["order_status_update"]), "status": "shipped"}
        v.pop("status_label", None); v.pop("status_message", None)
        p = automation.preview(self.t["order_status_update"], v)
        self.assertIn("Dispatched", p["subject"])
        self.assertIn("left our godown", p["text"])

    def test_optional_blocks_disappear_when_empty(self):
        v = {**automation.sample_variables(self.t["order_status_update"]), "lr_number": "", "transporter_name": "", "tracking_url": ""}
        p = automation.preview(self.t["order_status_update"], v)
        self.assertNotIn("Lorry receipt", p["html"])
        self.assertNotIn("See full status", p["html"])


class AutoReplyRuleTests(unittest.TestCase):
    def setUp(self):
        self.queued = []
        automation.enqueue = lambda *a, **k: (self.queued.append((a, k)) or 1)

    def reply(self, local="support", sender="Ravi <ravi@gmail.com>", subject="Delivery question", headers=None):
        from email.utils import parseaddr
        name, addr = parseaddr(sender)
        return automation.maybe_auto_reply({"local_part": local}, {"subject": subject, "headers": headers or {}}, addr, name, 77)

    def test_new_customer_mail_to_support_is_acknowledged_once_per_day(self):
        self.assertTrue(self.reply())
        (args, _), = self.queued
        self.assertEqual(args[0], "support_auto_reply")
        self.assertEqual(args[1], "ravi@gmail.com")
        self.assertRegex(args[6], r"^inbound_auto:ravi@gmail.com:\d{4}-\d\d-\d\d$")   # same key all day -> one reply per sender per day

    def test_only_configured_mailboxes_reply(self):
        self.assertFalse(self.reply(local="sales"))
        self.assertFalse(self.reply(local="admin"))

    def test_no_reply_loops(self):
        for sender in ("noreply@shop.com", "no-reply@x.com", "MAILER-DAEMON@mx.com", "postmaster@x.com", "support@rtcrackers.com", "anyone@rtcrackers.com", "bounce@x.com"):
            with self.subTest(sender):
                self.assertFalse(self.reply(sender=sender))
        for subject in ("Automatic reply: hello", "Out of office until Monday", "Undeliverable: your message", "Delivery Status Notification (Failure)"):
            with self.subTest(subject):
                self.assertFalse(self.reply(subject=subject))
        for headers in ({"Auto-Submitted": "auto-replied"}, {"Precedence": "bulk"}, {"List-Unsubscribe": "<mailto:x@y.com>"}, {"X-Auto-Response-Suppress": "All"}):
            with self.subTest(headers):
                self.assertFalse(self.reply(headers=headers))
        self.assertEqual(self.queued, [])

    def test_header_list_format_is_understood(self):
        self.assertFalse(self.reply(headers=[{"name": "Auto-Submitted", "value": "auto-generated"}]))


class CategoryAndKeyTests(unittest.TestCase):
    def test_key_rules(self):
        self.assertTrue(automation.KEY_RE.match("order_confirmation"))
        for bad in ("Order", "1abc", "a", "has space", "x" * 70, "a-b"):
            self.assertFalse(automation.KEY_RE.match(bad), bad)

    def test_backoff_schedule_matches_attempt_limit(self):
        self.assertEqual(len(automation.BACKOFF_SECONDS), automation.MAX_ATTEMPTS - 1)


if __name__ == "__main__":
    unittest.main()
