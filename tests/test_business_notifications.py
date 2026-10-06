import unittest

from business_notifications import chat_identity


class ChatIdentityTests(unittest.TestCase):
    def test_chat_is_peer_even_when_author_is_business_account(self):
        html, plain = chat_identity({
            "sender_id": 100, "name": "Владелец аккаунта",
            "chat_id": 200, "chat_name": "Тестовый собеседник", "chat_username": "test_peer",
        })
        self.assertIn("https://t.me/test_peer", html)
        self.assertIn("Тестовый собеседник (@test_peer)", plain)
        self.assertNotIn("Владелец аккаунта", plain)

    def test_private_chat_without_username_has_id_link(self):
        html, plain = chat_identity({"chat_id": 200, "chat_name": "Друг"})
        self.assertIn("tg://user?id=200", html)
        self.assertEqual(plain, "Друг")

    def test_unsafe_chat_username_is_not_used_in_url(self):
        html, plain = chat_identity({
            "chat_id": 200, "chat_name": "<Друг>", "chat_username": "bad/name",
        })
        self.assertNotIn("https://t.me/", html)
        self.assertIn("&lt;Друг&gt;", html)
        self.assertEqual(plain, "<Друг>")
