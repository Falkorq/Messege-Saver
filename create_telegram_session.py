"""One-time local helper that creates TELEGRAM_SESSION for the profile monitor."""

import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    load_dotenv()
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        print("\nTELEGRAM_SESSION=" + client.session.save())


if __name__ == "__main__":
    asyncio.run(main())
