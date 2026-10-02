from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    PeerIdInvalidError, UserIdInvalidError, UserInvalidError,
    UsernameInvalidError, UsernameNotOccupiedError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputUser

from .models import ProfileSnapshot


def active_username(user):
    username = getattr(user, "username", None)
    if username:
        return username
    for item in getattr(user, "usernames", None) or ():
        if getattr(item, "active", False) and getattr(item, "username", None):
            return item.username
    return None


class ProfileTelegramClient:
    def __init__(self, api_id, api_hash, session_string, photo_dir):
        self.client = TelegramClient(StringSession(session_string), api_id, api_hash)
        self.photo_dir = Path(photo_dir)
        self.photo_dir.mkdir(parents=True, exist_ok=True)

    async def start(self):
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("TELEGRAM_SESSION не авторизована; создайте StringSession локально")

    async def close(self):
        await self.client.disconnect()

    async def fetch(self, target, access_hash=None, username=None, allow_id_search=True):
        entity = None
        if isinstance(target, int) and access_hash is not None:
            # A StringSession has no persistent entity cache. The saved hash
            # lets one GetFullUser request work across process restarts.
            try:
                full = await self.client(GetFullUserRequest(InputUser(target, access_hash)))
            except (ValueError, PeerIdInvalidError, UserIdInvalidError, UserInvalidError):
                full = None
            if full is not None:
                entity = next((user for user in getattr(full, "users", []) if user.id == target), None)
                if entity is None:
                    raise ValueError(f"Telegram не вернул профиль пользователя {target}")
        if entity is None:
            # Existing watchlist rows from older versions have no access_hash.
            # Resolve their last known public username once, then persist the hash.
            if isinstance(target, int) and username:
                try:
                    entity = await self.resolve(username)
                except (ValueError, UsernameInvalidError, UsernameNotOccupiedError):
                    entity = None
                if entity is not None and entity.id != target:
                    entity = None
            if entity is None:
                entity = await self.resolve(target, search_known_id=allow_id_search)
            full = await self.client(GetFullUserRequest(entity))
        refreshed = next((user for user in getattr(full, "users", []) if user.id == entity.id), entity)
        # GetFullUser may return a minimal User without the username or hash
        # that get_entity just resolved. Keep those verified values for the
        # initial baseline and for checks after a StringSession restart.
        resolved_hash = (getattr(refreshed, "access_hash", None)
                         or getattr(entity, "access_hash", None) or access_hash)
        if resolved_hash is not None:
            refreshed.access_hash = resolved_hash
        resolved_username = active_username(refreshed)
        if not resolved_username and entity is not refreshed:
            resolved_username = active_username(entity)
        if not resolved_username and isinstance(target, str):
            resolved_username = target.lstrip("@")
        photo = getattr(refreshed, "photo", None)
        photo_id = str(photo.photo_id) if photo and getattr(photo, "photo_id", None) else None
        return ProfileSnapshot(
            user_id=refreshed.id,
            username=resolved_username,
            first_name=getattr(refreshed, "first_name", None),
            last_name=getattr(refreshed, "last_name", None),
            bio=getattr(full.full_user, "about", None),
            photo_id=photo_id,
        ), refreshed

    async def resolve(self, target, search_known_id=True):
        try:
            return await self.client.get_entity(target)
        except (ValueError, PeerIdInvalidError, UserIdInvalidError, UserInvalidError) as original_error:
            if not isinstance(target, int):
                raise
            if not search_known_id:
                raise ValueError(
                    "Для этого ID нет сохранённого access_hash. "
                    "Повторно добавьте пользователя по его текущему @username."
                ) from original_error
            # Telegram needs an access_hash in addition to a numeric ID. Loading
            # dialogs and contacts is done only while adding an unknown ID, never
            # during every scheduled check.
            dialogs = await self.client.get_dialogs(limit=None)
            for dialog in dialogs:
                entity = getattr(dialog, "entity", None)
                if getattr(entity, "id", None) == target:
                    return entity
            contacts = await self.client(GetContactsRequest(hash=0))
            for entity in getattr(contacts, "users", []):
                if entity.id == target:
                    return entity
            raise ValueError(
                "Telegram не может открыть этот ID без access_hash. "
                "Добавьте пользователя по @username либо сначала откройте с ним диалог "
                "в аккаунте TELEGRAM_SESSION."
            ) from original_error

    def photo_path(self, user_id, photo_id):
        if not photo_id:
            return None
        return self.photo_dir / str(user_id) / f"{photo_id}.jpg"

    async def download_photo(self, entity, user_id, photo_id):
        path = self.photo_path(user_id, photo_id)
        if path is None:
            return None
        if path.exists():
            return str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result = await self.client.download_profile_photo(entity, file=str(path))
        return str(result) if result else None
