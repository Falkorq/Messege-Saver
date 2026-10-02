import functools
import logging
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from telebot import types
from telebot.asyncio_helper import ApiTelegramException

from profile_monitor.style import icon, styled_button, truncate_html
from .service import format_until


log = logging.getLogger(__name__)
PAGE_SIZE = 6
MUTELIST_LIMIT = 40
MOSCOW = timezone(timedelta(hours=3))


def activity_time(value):
    return datetime.fromtimestamp(value, MOSCOW).strftime("%d.%m %H:%M") if value else "—"


def button(text, data):
    label, icon_id = styled_button(text)
    return types.InlineKeyboardButton(label, callback_data=data, icon_custom_emoji_id=icon_id)


def keyboard(*rows):
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        markup.row(*row)
    return markup


def guarded_callback(bot):
    """Dismiss the loading indicator even when rendering fails, and never crash on forged data."""
    def decorator(handler):
        @functools.wraps(handler)
        async def wrapper(call):
            text = None
            try:
                return await handler(call)
            except (ValueError, IndexError, KeyError):
                text = "⚠️ Некорректные данные кнопки. Откройте раздел заново через /menu"
            except Exception:
                log.exception("Ошибка обработки кнопки %r", call.data)
                text = "⚠️ Не удалось обновить экран. Откройте раздел заново через /menu"
            finally:
                if not getattr(call, "_answered", False):
                    call._answered = True
                    try:
                        await bot.answer_callback_query(call.id, text, show_alert=bool(text))
                    except Exception:
                        log.exception("Не удалось ответить на callback %s", call.id)
        return wrapper
    return decorator


class BusinessToolsUI:
    def __init__(self, bot, service, repository, routes, placeholder_path):
        self.bot = bot
        self.service = service
        self.repo = repository
        self.routes = routes
        self.placeholder = Path(placeholder_path)

    def register(self):
        bot = self.bot
        guard = guarded_callback(bot)

        @bot.message_handler(commands=["mute", "unmute", "mutelist", "chatstats"])
        async def commands(msg):
            if not self.authorized_message(msg):
                return
            await self.handle_command(msg)

        @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("bt:mutes:"))
        @guard
        async def mutes_callback(call):
            if not await self.authorized_callback(call): return
            await self.render_mutes(call.message.chat.id, call.message.message_id, int(call.data.split(":")[2]))

        @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("bt:unmute:"))
        @guard
        async def unmute_callback(call):
            if not await self.authorized_callback(call): return
            parts = call.data.split(":")
            if len(parts) != 5:
                raise ValueError("bad callback data")
            _, _, account_id, chat_id, page = parts
            self.repo.unmute(self.owner(call.from_user.id), int(account_id), int(chat_id))
            await self.render_mutes(call.message.chat.id, call.message.message_id, int(page))
            call._answered = True
            await bot.answer_callback_query(call.id, "✅ Мут снят")

        @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("bt:stats:"))
        @guard
        async def stats_callback(call):
            if not await self.authorized_callback(call): return
            parts = call.data.split(":")
            sort, page = (parts[2], int(parts[3])) if len(parts) == 4 else ("recent", int(parts[2]))
            await self.render_stats(call.message.chat.id, call.message.message_id, page, sort)

        @bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("bt:stat:"))
        @guard
        async def stat_callback(call):
            if not await self.authorized_callback(call): return
            parts = call.data.split(":")
            if len(parts) < 5:
                raise ValueError("bad callback data")
            _, _, account_id, chat_id, page = parts[:5]
            sort = parts[5] if len(parts) > 5 else "recent"
            section = parts[6] if len(parts) > 6 else "overview"
            await self.render_stat(call.message.chat.id, call.message.message_id,
                                   int(account_id), int(chat_id), int(page), sort, section)

    async def handle_command(self, msg):
        owner_id = self.owner(msg.from_user.id)
        command, *tail = (msg.text or "").split(maxsplit=1)
        command = command.split("@")[0]
        args = tail[0].split() if tail else []
        try:
            if command == "/mute":
                if not args:
                    text = f"{icon('question')} Использование: <code>/mute @username 1h</code>"
                else:
                    row, until = self.service.mute_target(owner_id, args[0], args[1] if len(args) > 1 else None)
                    text = f"{icon('disabled')} {escape(self.service.chat_name(row))} в муте {format_until(until)}"
            elif command == "/unmute":
                if not args:
                    text = f"{icon('question')} Использование: <code>/unmute @username</code>"
                else:
                    row, removed = self.service.unmute_target(owner_id, args[0])
                    text = (f"{icon('success')} Мут снят с {escape(self.service.chat_name(row))}" if removed
                            else f"{icon('warning')} Пользователь не найден или не находится в муте")
            elif command == "/mutelist":
                text = self.mutes_text(owner_id)
            else:
                if not args:
                    summary = self.repo.stats_summary(owner_id)
                    rows = self.repo.stats_page(owner_id, size=8, sort="messages")
                    names = "\n".join(
                        f"• {escape(self.service.chat_name(row))} · {row['total_messages']} соо. "
                        f"({escape(self.account_name(row['account_id']))})" for row in rows
                    )
                    empty = f"{icon('stats_empty')} Статистика пока пуста"
                    text = (f"{icon('chat_list_stats')} <b>Статистика чатов · всё время</b>\n"
                            f"Чатов: {summary['chats']} · сообщений: {summary['messages']}\n"
                            f"Вы: {summary['own']} · собеседники: {summary['peer']}\n\n"
                            f"{names or empty}"
                            "\n\nПодробно: <code>/chatstats @username</code> или откройте «Статистика» в /menu")
                else:
                    if len(args) > 1 and not args[1].isdigit():
                        raise ValueError("После имени чата укажите числовой ID аккаунта")
                    account_id = int(args[1]) if len(args) > 1 else None
                    matches = self.repo.matching_chats(owner_id, args[0], account_id)
                    if len(matches) == 1:
                        row = matches[0]
                        text = self.service.stats_text(owner_id, row["account_id"], row["chat_id"])
                    elif matches:
                        choices = "\n".join(
                            f"• {escape(self.account_name(row['account_id']))}: "
                            f"<code>/chatstats {escape(args[0])} {row['account_id']}</code>"
                            for row in matches
                        )
                        text = f"{icon('info')} Этот чат есть у нескольких аккаунтов:\n{choices}"
                    else:
                        text = f"{icon('warning')} Чат не найден"
        except (ValueError, LookupError) as exc:
            text = f"{icon('warning')} {escape(str(exc))}"
        await self.bot.send_message(msg.chat.id, text, parse_mode="HTML")

    async def render_mutes(self, chat_id, message_id, page):
        rows = self.repo.active_mutes(self.owner(chat_id))
        total = len(rows); page = max(0, min(page, max(0, (total - 1) // PAGE_SIZE)))
        shown = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        lines = [f"{icon('disabled')} <b>Муты</b>", ""]
        buttons = []
        for row in shown:
            name = self.service.chat_name(row)
            lines.append(f"{escape(name)} — {format_until(row['until_at'])}")
            if row["last_error"]:
                lines.append(f"{icon('warning')} Ошибка удаления: {escape(row['last_error'])}")
            buttons.append([button("Снять мут · " + name[:24],
                                   f"bt:unmute:{row['account_id']}:{row['chat_id']}:{page}")])
        if not shown:
            lines.append(f"{icon('disabled')} Список мутов пуст")
        nav = []
        if page: nav.append(button("◀", f"bt:mutes:{page-1}"))
        if (page + 1) * PAGE_SIZE < total: nav.append(button("▶", f"bt:mutes:{page+1}"))
        if nav: buttons.append(nav)
        buttons.append([button("◀ Назад", "pm:nav:tools")])
        await self.edit(chat_id, message_id, "\n".join(lines), keyboard(*buttons))

    async def render_stats(self, chat_id, message_id, page, sort="recent"):
        owner_id = self.owner(chat_id)
        summary = self.repo.stats_summary(owner_id)
        total = summary["chats"]
        page = max(0, min(page, max(0, (total - 1) // PAGE_SIZE)))
        sort = sort if sort in ("recent", "messages") else "recent"
        shown = self.repo.stats_page(owner_id, page, PAGE_SIZE, sort)
        lines = [f"{icon('chat_list_stats')} <b>Статистика чатов · всё время</b>",
                 f"Чатов: {total} · сообщений: {summary['messages']}",
                 f"Вы: {summary['own']} · собеседники: {summary['peer']}", ""]
        buttons = []
        for row in shown:
            name = self.service.chat_name(row)
            lines.append(f"{escape(name)} · {row['total_messages']} соо. · {activity_time(row['last_at'])}"
                         f"\n{escape(self.account_name(row['account_id']))}")
            buttons.append([button(f"{name[:22]} ·{str(row['account_id'])[-4:]}",
                                   f"bt:stat:{row['account_id']}:{row['chat_id']}:{page}:{sort}")])
        if not shown:
            lines.append(f"{icon('stats_empty')} Статистика пока пуста")
        buttons.append([button("Недавние" + (" ✓" if sort == "recent" else ""), "bt:stats:recent:0"),
                        button("По сообщениям" + (" ✓" if sort == "messages" else ""), "bt:stats:messages:0")])
        nav = []
        if page: nav.append(button("◀", f"bt:stats:{sort}:{page-1}"))
        if (page + 1) * PAGE_SIZE < total: nav.append(button("▶", f"bt:stats:{sort}:{page+1}"))
        if nav: buttons.append(nav)
        buttons.append([button("◀ Назад", "pm:nav:tools")])
        await self.edit(chat_id, message_id, "\n".join(lines), keyboard(*buttons))

    async def render_stat(self, chat_id, message_id, account_id, target_chat_id, page,
                          sort="recent", section="overview"):
        text = self.service.stats_text(self.owner(chat_id), account_id, target_chat_id, section)
        prefix = f"bt:stat:{account_id}:{target_chat_id}:{page}:{sort}:"
        await self.edit(chat_id, message_id, text,
                        keyboard(
                            [button("📊 Обзор", prefix + "overview"),
                             button("📎 Медиа", prefix + "media"),
                             button("💳 Топ слов", prefix + "words")],
                            [button("◀ Назад", f"bt:stats:{sort}:{page}")],
                        ))

    def account_name(self, account_id):
        label = self.service.account_labels.get(account_id, str(account_id))
        return f"{label} · {str(account_id)[-4:]}"

    def mutes_text(self, owner_id):
        rows = self.repo.active_mutes(owner_id)
        if not rows:
            return f"{icon('info')} Список мутов пуст"
        hidden = len(rows) - MUTELIST_LIMIT
        lines = [f"{escape(self.service.chat_name(row))} — {format_until(row['until_at'])}"
                 for row in rows[:MUTELIST_LIMIT]]
        if hidden > 0:
            lines.append(f"… и ещё {hidden} (полный список — «Муты» в /menu)")
        return f"{icon('disabled')} <b>Муты</b>\n\n" + "\n".join(lines)

    async def edit(self, chat_id, message_id, text, markup):
        with open(self.placeholder, "rb") as image:
            media = types.InputMediaPhoto(image, caption=truncate_html(text, 900), parse_mode="HTML")
            try:
                await self.bot.edit_message_media(media, chat_id, message_id, reply_markup=markup)
            except ApiTelegramException as exc:
                if exc.error_code != 400 or "message is not modified" not in str(exc).lower():
                    raise

    def authorized_message(self, msg):
        return msg.chat.type == "private" and msg.from_user and msg.from_user.id in self.routes

    async def authorized_callback(self, call):
        if call.from_user.id in self.routes and call.message.chat.id == call.from_user.id:
            return True
        call._answered = True
        try:
            await self.bot.answer_callback_query(call.id, "⚠️ Нет доступа", show_alert=True)
        except Exception:
            log.exception("Не удалось ответить на callback %s", call.id)
        return False

    def owner(self, actor_id):
        return self.routes[actor_id]
