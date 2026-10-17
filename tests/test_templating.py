import unittest

from mailcore import templating as t


class RenderTests(unittest.TestCase):
    def test_values_are_escaped_in_html_but_not_in_text(self):
        v = {"name": "<b>Ravi & Co</b>"}
        self.assertEqual(t.render("Hi {{name}}", v, html_mode=True), "Hi &lt;b&gt;Ravi &amp; Co&lt;/b&gt;")
        self.assertEqual(t.render("Hi {{name}}", v, html_mode=False), "Hi <b>Ravi & Co</b>")

    def test_triple_braces_only_raw_for_allowed_names(self):
        v = {"items_html": "<table>ok</table>", "message": "<script>x</script>"}
        out = t.render("{{{items_html}}} {{{message}}}", v, html_mode=True, raw_allowed={"items_html"})
        self.assertIn("<table>ok</table>", out)
        self.assertNotIn("<script>", out)          # a customer-controlled value can never inject HTML
        self.assertIn("&lt;script&gt;", out)

    def test_unknown_and_none_render_empty(self):
        self.assertEqual(t.render("a{{nope}}b{{x}}c", {"x": None}, html_mode=True), "abc")

    def test_conditionals_and_nesting(self):
        tpl = "A{{#if lr}}[LR {{lr}}{{#if tr}} via {{tr}}{{/if}}]{{/if}}B"
        self.assertEqual(t.render(tpl, {}, html_mode=False), "AB")
        self.assertEqual(t.render(tpl, {"lr": "88"}, html_mode=False), "A[LR 88]B")
        self.assertEqual(t.render(tpl, {"lr": "88", "tr": "ARC"}, html_mode=False), "A[LR 88 via ARC]B")
        self.assertEqual(t.render(tpl, {"lr": "  ", "tr": "ARC"}, html_mode=False), "AB")   # blank counts as empty

    def test_template_syntax_inside_values_is_not_executed(self):
        out = t.render("{{a}}", {"a": "{{#if x}}boom{{/if}} {{b}}", "b": "SECRET"}, html_mode=False)
        self.assertEqual(out, "{{#if x}}boom{{/if}} {{b}}")   # single pass: values are never re-evaluated

    def test_subject_is_single_line(self):
        self.assertEqual(t.render_subject("Order {{n}}\r\nBcc: evil@x.com", {"n": "7"}), "Order 7 Bcc: evil@x.com")
        self.assertEqual(t.render_subject("{{x}}", {}), "(no subject)")


class ValidateTests(unittest.TestCase):
    def test_accepts_normal_template(self):
        self.assertEqual(t.validate("Hi {{a}}", "<p>{{a}}</p>{{#if b}}x{{/if}}", "Hi"), [])

    def test_rejects_dangerous_or_broken(self):
        self.assertTrue(t.validate("s", "<script>alert(1)</script>", ""))
        self.assertTrue(t.validate("s", '<a href="javascript:alert(1)">x</a>', ""))
        self.assertTrue(t.validate("s", '<img src=x onerror="alert(1)">', ""))
        self.assertTrue(t.validate("s", "<form action=x></form>", ""))
        self.assertTrue(t.validate("s", "{{#if a}}no end", ""))
        self.assertTrue(t.validate("", "body", ""))
        self.assertTrue(t.validate("s", "  ", ""))

    def test_used_names(self):
        self.assertEqual(t.used_names("{{a}} {{{b}}} {{#if c}}{{d}}{{/if}}"), {"a", "b", "c", "d"})


class FormatTests(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(t.format_inr(12400), "₹12,400.00")
        self.assertEqual(t.format_inr(225000), "₹2,25,000.00")
        self.assertEqual(t.format_inr(12345678.5), "₹1,23,45,678.50")
        self.assertEqual(t.format_inr(999), "₹999.00")
        self.assertEqual(t.format_inr(None), "")

    def test_nl2br_escapes(self):
        self.assertEqual(t.nl2br("a<b>\nc"), "a&lt;b&gt;<br>c")

    def test_items_blocks_escape_names(self):
        items = [{"name": "Rocket <x>", "quantity": 2, "line_total": 1000}]
        self.assertIn("Rocket &lt;x&gt;", t.items_html(items))
        self.assertIn("2 × Rocket <x>", t.items_text(items))

    def test_layout_escapes_company_details(self):
        out = t.wrap_layout("<p>hi</p>", company="A<b>", phone="1", email="a@b.com", site_url="https://x.y")
        self.assertIn("A&lt;b&gt;", out)
        self.assertIn("<p>hi</p>", out)

    def test_every_status_has_label_and_message(self):
        self.assertEqual(set(t.STATUS_LABELS), set(t.STATUS_MESSAGES))


if __name__ == "__main__":
    unittest.main()
