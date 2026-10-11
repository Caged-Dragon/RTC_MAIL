"""Pure-Python tests for the mail-lock checker (no network, no database)."""
import unittest

from mailcore import compliance as c

ALLOWED = [r"^inbound-smtp\.[a-z0-9-]+\.amazonaws\.com\.?$"]
RESEND_MX = [{"data": "10 inbound-smtp.us-east-1.amazonaws.com."}]
GOOD_SPF = ["v=spf1 include:amazonses.com ~all"]
GOOD_DMARC = ["v=DMARC1; p=quarantine; rua=mailto:admin@rtcrackers.com"]
GOOD_DKIM = ["p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQ; v=DKIM1"]


def run(mx, spf=GOOD_SPF, dmarc=GOOD_DMARC, dkim=GOOD_DKIM):
    return c.evaluate("rtcrackers.com", c.parse_mx(mx), spf, dmarc, dkim, ALLOWED)


def status(report, cid):
    return next(x["status"] for x in report["checks"] if x["id"] == cid)


class ParseTests(unittest.TestCase):
    def test_parse_mx_sorts_and_normalises(self):
        out = c.parse_mx([{"data": "20 B.Example.COM."}, {"data": "10 a.example.com."}, {"data": "garbage"}])
        self.assertEqual(out, [(10, "a.example.com"), (20, "b.example.com")])

    def test_parse_txt_joins_chunks(self):
        self.assertEqual(c.parse_txt([{"data": '"v=spf1 include:" "amazonses.com ~all"'}]), ["v=spf1 include:amazonses.com ~all"])
        self.assertEqual(c.parse_txt([{"data": "plain"}]), ["plain"])
        self.assertEqual(c.parse_txt(None), [])


class MxTests(unittest.TestCase):
    def test_only_resend_inbound_is_ok(self):
        r = run(RESEND_MX)
        self.assertEqual(status(r, "mx"), "ok")
        self.assertEqual(r["status"], "ok")

    def test_gmail_mx_is_a_leak(self):
        r = run(RESEND_MX + [{"data": "1 aspmx.l.google.com."}])
        self.assertEqual(status(r, "mx"), "fail")
        self.assertEqual(r["status"], "fail")
        detail = next(x["detail"] for x in r["checks"] if x["id"] == "mx")
        self.assertIn("Google Workspace", detail)

    def test_forwarder_and_unknown_hosts_are_leaks(self):
        self.assertEqual(status(run([{"data": "10 mx1.improvmx.com."}]), "mx"), "fail")
        self.assertEqual(status(run([{"data": "10 mail.some-random-host.net."}]), "mx"), "fail")

    def test_lookalike_host_cannot_pass(self):
        # must match the whole host, not merely contain 'amazonaws.com'
        self.assertEqual(status(run([{"data": "10 inbound-smtp.evil.com.amazonaws.com.attacker.net."}]), "mx"), "fail")
        self.assertEqual(status(run([{"data": "10 evil.amazonaws.com."}]), "mx"), "fail")

    def test_no_mx_fails(self):
        self.assertEqual(status(run([]), "mx"), "fail")

    def test_other_region_of_resend_inbound_allowed(self):
        self.assertEqual(status(run([{"data": "10 inbound-smtp.eu-west-1.amazonaws.com"}]), "mx"), "ok")


class AuthRecordTests(unittest.TestCase):
    def test_missing_records_warn_not_fail(self):
        r = run(RESEND_MX, spf=[], dmarc=[], dkim=[])
        self.assertEqual({status(r, k) for k in ("spf", "dmarc", "dkim")}, {"warning"})
        self.assertEqual(r["status"], "warning")

    def test_dmarc_monitor_only_warns(self):
        self.assertEqual(status(run(RESEND_MX, dmarc=["v=DMARC1; p=none"]), "dmarc"), "warning")

    def test_spf_with_other_senders_warns(self):
        r = run(RESEND_MX, spf=["v=spf1 include:_spf.google.com include:amazonses.com ~all"])
        self.assertEqual(status(r, "spf"), "warning")

    def test_every_failing_check_explains_the_fix(self):
        for chk in run([{"data": "1 aspmx.l.google.com."}], spf=[], dmarc=[], dkim=[])["checks"]:
            if chk["status"] != "ok":
                self.assertTrue(chk["fix"], chk["id"])


if __name__ == "__main__":
    unittest.main()
