import unittest

from profile_monitor.ui import button


class InlineButtonTests(unittest.TestCase):
    def test_regular_text_is_kept_without_custom_id(self):
        value = button("Неизвестная кнопка", "test").to_dict()
        self.assertEqual(value["text"], "Неизвестная кнопка")
        self.assertNotIn("icon_custom_emoji_id", value)

    def test_custom_icon_is_used_without_duplicate_unicode_icon(self):
        value = button("📡 Наблюдение", "test").to_dict()
        self.assertEqual(value["text"], "Наблюдение")
        self.assertEqual(value["icon_custom_emoji_id"], "6019295596173596341")

    def test_statistics_button_uses_custom_icon(self):
        value = button("📊 Статистика", "test").to_dict()
        self.assertEqual(value["text"], "Статистика")
        self.assertEqual(value["icon_custom_emoji_id"], "5931472654660800739")


if __name__ == "__main__":
    unittest.main()
