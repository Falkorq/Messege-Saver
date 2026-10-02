import unittest

from profile_monitor.style import icon, truncate_html


class CaptionTruncationTests(unittest.TestCase):
    def test_does_not_cut_custom_emoji_tag(self):
        source = (icon("profiles") + " пользователь\n") * 100
        result = truncate_html(source, 80)
        self.assertLessEqual(result.count("пользователь") * len("пользователь") + 80, 1000)
        self.assertEqual(result.count("<tg-emoji"), result.count("</tg-emoji>"))
        self.assertTrue(result.endswith("</tg-emoji>") or "…" in result)

    def test_closes_nested_tags_after_truncation(self):
        result = truncate_html("<b>" + "x" * 2000 + "</b>", 20)
        self.assertEqual(result, "<b>" + "x" * 19 + "…</b>")

    def test_short_html_is_unchanged(self):
        source = f"{icon('watch')} <b>Наблюдение</b>"
        self.assertEqual(truncate_html(source), source)


if __name__ == "__main__":
    unittest.main()
