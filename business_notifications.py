"""Safe chat labels for Business edit and delete notifications."""

import re
from html import escape


def chat_identity(record):
    """Return an HTML label and plain label for the conversation, not its author."""
    chat_id = record.get("chat_id")
    name = str(record.get("chat_name") or "").strip()
    username = str(record.get("chat_username") or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
        username = ""
    if not name:
        name = "@" + username if username else str(chat_id or "неизвестен")
    label = name if not username or name.lower().lstrip("@") == username.lower() else f"{name} (@{username})"
    if username:
        return f'<a href="https://t.me/{username}">{escape(label)}</a>', label
    if isinstance(chat_id, int) and chat_id > 0:
        return f'<a href="tg://user?id={chat_id}">{escape(label)}</a>', label
    return escape(label), label
