import re
import time
import logging
from datetime import datetime, timedelta, timezone
from html import escape

from profile_monitor.style import icon


log = logging.getLogger(__name__)


DURATION_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
MEDIA_NAMES = {
    "photo": "Фото", "video": "Видео", "voice": "Голосовые", "video_note": "Видеосообщения",
    "document": "Файлы", "audio": "Аудио", "sticker": "Стикеры", "animation": "GIF",
    "location": "Геопозиции", "venue": "Места", "poll": "Опросы", "contact": "Контакты",
}
MEDIA_ICONS = {
    "photo": "photos", "video": "video", "voice": "voice",
    "video_note": "video_note", "document": "document", "audio": "audio",
    "sticker": "sticker", "animation": "animation", "location": "location",
    "venue": "venue", "poll": "poll", "contact": "contact",
}


MAX_DURATION = 10 * 365 * 86400  # 10 лет; сверх этого INTEGER SQLite переполняется


def parse_duration(value, default="1h"):
    text = (value or default).strip().lower()
    if text in {"0", "forever", "навсегда", "∞"}:
        return 0
    matches = list(DURATION_RE.finditer(text))
    if not matches or "".join(match.group(0).replace(" ", "") for match in matches) != text.replace(" ", ""):
        raise ValueError("Неверный срок. Примеры: 30m, 2h, 1d, 1w или навсегда")
    seconds = sum(int(match.group(1)) * UNITS[match.group(2).lower()] for match in matches)
    if seconds <= 0:
        raise ValueError("Срок должен быть больше нуля")
    if seconds > MAX_DURATION:
        raise ValueError("Слишком большой срок: максимум 10 лет (или навсегда)")
    return seconds


def format_until(until_at):
    moscow = timezone(timedelta(hours=3))
    return ("навсегда" if not until_at else
            "до " + datetime.fromtimestamp(until_at, moscow).strftime("%d.%m.%Y %H:%M МСК"))


class BusinessToolsService:
    def __init__(self, bot, repository, enqueue_notification, account_labels):
        self.bot = bot
        self.repo = repository
        self.enqueue = enqueue_notification
        self.account_labels = account_labels

    async def handle_business_message(self, msg, record, account_id, owner_id):
        text = (record.get("text") or "").strip()
        own = record.get("sender_id") == account_id
        if own and text.startswith("!"):
            handled = await self._handle_chat_command(msg, record, account_id, owner_id, text)
            if handled:
                return "command"
        self.repo.record_message(
            msg.business_connection_id, account_id, owner_id, msg.message_id, record
        )
        mute = self.repo.is_muted(account_id, msg.chat.id) if not own else None
        if mute:
            previous_error = mute["last_error"]
            try:
                await self._delete(msg.business_connection_id, msg.chat.id, msg.message_id, account_id)
            except Exception as exc:
                current_error = str(exc)[:300]
                if previous_error != current_error:
                    self.enqueue(
                        owner_id,
                        f"{icon('warning')} Не удалось удалить сообщение от "
                        f"{escape(self.chat_name(mute))}: {escape(current_error)}",
                    )
                raise
            return "muted"
        return None

    async def _handle_chat_command(self, msg, record, account_id, owner_id, text):
        command, *rest = text.split(maxsplit=1)
        command = command.lower()
        if command not in {"!mute", "!unmute", "!mutestatus", "!stats"}:
            return False
        row = self.repo.stat(owner_id, account_id, msg.chat.id)
        chat_row = row[0] if row else {
            "owner_id": owner_id, "account_id": account_id, "chat_id": msg.chat.id,
            "display_name": record.get("chat_name"), "username": record.get("chat_username"),
        }
        try:
            if command == "!mute":
                seconds = parse_duration(rest[0] if rest else None)
                until = int(time.time()) + seconds if seconds else 0
                self.repo.mute(owner_id, chat_row, until)
                response = f"{icon('disabled')} {escape(self.chat_name(chat_row))} в муте {format_until(until)}"
            elif command == "!unmute":
                removed = self.repo.unmute(owner_id, account_id, msg.chat.id)
                response = (f"{icon('success')} Мут снят" if removed
                            else f"{icon('info')} Этот чат не находится в муте")
            elif command == "!mutestatus":
                mute = self.repo.is_muted(account_id, msg.chat.id)
                response = (f"{icon('disabled')} Мут активен {format_until(mute['until_at'])}" if mute
                            else f"{icon('info')} Мут не активен")
            else:
                response = self.stats_text(owner_id, account_id, msg.chat.id)
        except ValueError as exc:
            response = f"{icon('warning')} {escape(str(exc))}"
        try:
            await self._delete_command(msg.business_connection_id, msg.chat.id, msg.message_id)
        except Exception as exc:
            log.warning("Команда %s выполнена, но сообщение %s не удалено: %s", command, msg.message_id, exc)
            response += (f"\n{icon('warning')} Команда выполнена, но Telegram не разрешил "
                         "удалить её из чата. Проверьте права Business-бота и доступ к этому чату.")
        self.enqueue(owner_id, response)
        return True

    async def _delete_command(self, connection_id, chat_id, message_id):
        self.repo.mark_self_deleted(connection_id, chat_id, message_id)
        try:
            await self.bot.delete_business_messages(connection_id, [message_id])
        except Exception:
            self.repo.consume_self_deleted(connection_id, chat_id, message_id)
            raise

    async def _delete(self, connection_id, chat_id, message_id, account_id):
        self.repo.mark_self_deleted(connection_id, chat_id, message_id)
        try:
            await self.bot.delete_business_messages(connection_id, [message_id])
            self.repo.set_mute_error(account_id, chat_id, None)
        except Exception as exc:
            self.repo.consume_self_deleted(connection_id, chat_id, message_id)
            self.repo.set_mute_error(account_id, chat_id, exc)
            raise

    def mute_target(self, owner_id, target, duration=None):
        row = self.repo.resolve_chat(owner_id, target)
        if not row:
            raise LookupError("Чат не найден. Бот должен сначала получить хотя бы одно сообщение из этого чата")
        seconds = parse_duration(duration)
        until = int(time.time()) + seconds if seconds else 0
        self.repo.mute(owner_id, row, until)
        return row, until

    def unmute_target(self, owner_id, target):
        row = self.repo.resolve_chat(owner_id, target) or self.repo.resolve_mute(owner_id, target)
        if not row:
            return None, False
        return row, self.repo.unmute(owner_id, row["account_id"], row["chat_id"])

    def stats_text(self, owner_id, account_id, chat_id, section="overview"):
        result = self.repo.stat(owner_id, account_id, chat_id)
        if not result:
            return f"{icon('stats_empty')} Статистика пока пуста"
        row, active_days, words = result
        if section not in ("overview", "media", "words"):
            section = "overview"
        total = row["total_messages"]
        owner_pct = round(row["owner_messages"] * 100 / total, 1) if total else 0
        peer_pct = round(row["peer_messages"] * 100 / total, 1) if total else 0
        owner_avg = round(row["owner_chars"] / row["owner_messages"]) if row["owner_messages"] else 0
        peer_avg = round(row["peer_chars"] / row["peer_messages"]) if row["peer_messages"] else 0
        hours = __import__("json").loads(row["hours_json"])
        peak = hours.index(max(hours)) if hours and max(hours) else None
        media = __import__("json").loads(row["media_json"])
        lines = [
            f"{icon('chat_stats')} <b>Статистика чата</b>",
            f"{icon('profiles')} {escape(self.chat_name(row))}",
            f"{icon('account')} Аккаунт: {escape(self.account_labels.get(account_id, str(account_id)))} · {str(account_id)[-4:]}",
            "",
        ]
        if section == "overview":
            lines.extend((
                f"{icon('messages')} Сообщений: {total}",
                f"{icon('owner_messages')} Вы: {row['owner_messages']} — {owner_pct}%",
                f"{icon('peer_messages')} Собеседник: {row['peer_messages']} — {peer_pct}%",
                f"{icon('date')} Активных дней: {active_days}",
                f"{icon('average_length')} Средняя длина — вы: {owner_avg} симв.",
                f"{icon('average_length')} Средняя длина — собеседник: {peer_avg} симв.",
            ))
            if peak is not None:
                lines.append(f"{icon('time')} Пик активности: {peak:02d}:00–{(peak + 1) % 24:02d}:00")
            lines.extend(("", f"{icon('stat_info')} Статистика накапливается с момента подключения бота."))
        elif section == "media":
            lines.append(f"{icon('media')} <b>Медиа</b>")
            lines.extend(
                f"{icon(MEDIA_ICONS[kind])} {MEDIA_NAMES[kind]}: {count}"
                if kind in MEDIA_ICONS else f"{escape(kind)}: {count}"
                for kind, count in sorted(media.items(), key=lambda x: -x[1])
            )
            if not media:
                lines.append("Пока нет медиа")
        else:
            lines.append(f"{icon('top_words')} <b>Топ слов</b>")
            if words:
                lines.extend(f"{escape(item['word'])} — {item['count']}" for item in words)
            else:
                lines.append("Пока нет слов")
        return "\n".join(lines)

    @staticmethod
    def chat_name(row):
        return ("@" + row["username"]) if row["username"] else (row["display_name"] or str(row["chat_id"]))
