import asyncio
import logging
import random
import time

from telethon.errors import FloodWaitError


log = logging.getLogger(__name__)


class ProfileScheduler:
    def __init__(self, repository, service, owner_routes=None):
        self.repo = repository
        self.service = service
        self.owner_routes = owner_routes
        self._stopping = False

    async def run(self):
        # Existing snapshots are baselines. A restart never creates synthetic changes.
        while not self._stopping:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Цикл мониторинга упал; повторю через 30 секунд")
            await asyncio.sleep(30)

    async def run_cycle(self, pause_between=True):
        rows = self.repo.due(int(time.time()))
        for row in rows:
            if self._stopping:
                break
            if self.owner_routes is not None and row["owner_id"] not in self.owner_routes.values():
                continue
            try:
                await self.service.check_row(row)
            except FloodWaitError as exc:
                self.repo.set_error(row["owner_id"], row["user_id"], f"FloodWait {exc.seconds}s")
                if pause_between:
                    # Один FloodWait — сигнал о лимите на весь аккаунт: прерываем цикл целиком.
                    await asyncio.sleep(min(exc.seconds + 1, 300))
                    return
            except asyncio.CancelledError:
                raise
            except ValueError as exc:
                log.warning("Не удалось проверить профиль %s: %s", row["user_id"], exc)
                self.repo.set_error(row["owner_id"], row["user_id"], exc)
            except Exception as exc:
                log.exception("Ошибка проверки профиля %s", row["user_id"])
                self.repo.set_error(row["owner_id"], row["user_id"], exc)
            if pause_between:
                await asyncio.sleep(random.uniform(0.4, 1.2))

    def stop(self):
        self._stopping = True
