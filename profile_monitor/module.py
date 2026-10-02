import asyncio
import logging
import os
from pathlib import Path

from .repository import ProfileRepository
from .scheduler import ProfileScheduler
from .service import ProfileMonitorService, notification_text
from .telegram_client import ProfileTelegramClient
from .ui import ProfileUI


log = logging.getLogger(__name__)


class ProfileMonitorModule:
    """Lifecycle boundary for the optional MTProto profile monitor."""

    def __init__(self, bot, db, owner_routes, data_dir, enqueue_notification, banner_path=None):
        self.bot = bot
        self.repo = ProfileRepository(db)
        self.repo.migrate()
        self.owner_routes = owner_routes
        self.data_dir = Path(data_dir)
        self.enqueue_notification = enqueue_notification
        self.telegram = None
        self.service = None
        self.scheduler = None
        self.task = None
        self.enabled = False

        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        session = os.getenv("TELEGRAM_SESSION")
        supplied = [bool(api_id), bool(api_hash), bool(session)]
        if any(supplied) and not all(supplied):
            raise SystemExit("Для мониторинга профилей укажите TELEGRAM_API_ID, TELEGRAM_API_HASH и TELEGRAM_SESSION вместе")
        if not all(supplied):
            log.warning("Мониторинг профилей выключен: MTProto-параметры не настроены")
            return

        try:
            api_id = int(api_id)
        except ValueError as exc:
            raise SystemExit("TELEGRAM_API_ID должен быть числом") from exc
        self.telegram = ProfileTelegramClient(api_id, api_hash, session, self.data_dir / "photos")

        async def notifier(owner_id, old, new, changes, format_name):
            self.enqueue_notification(owner_id, notification_text(old, new, changes, format_name))

        self.service = ProfileMonitorService(self.repo, self.telegram, notifier)
        self.scheduler = ProfileScheduler(self.repo, self.service, self.owner_routes)
        self.ui = ProfileUI(
            bot, self.service, self.repo, self.owner_routes, self.data_dir, banner_path
        )
        self.ui.register()
        self.enabled = True

    async def start(self):
        if not self.enabled:
            return
        await self.telegram.start()
        self.task = asyncio.create_task(self.scheduler.run(), name="profile-monitor")
        log.info("Мониторинг профилей запущен")

    async def close(self):
        if not self.enabled:
            return
        self.scheduler.stop()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.telegram.close()
