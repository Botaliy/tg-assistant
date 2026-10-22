"""One-time interactive login: asks for the phone, the code and the 2FA password,
then saves the session to DATA_DIR. Run it once before starting the bot."""

import asyncio

from telethon import utils

from bot.config import get_settings
from bot.main import build_client


async def login() -> None:
    settings = get_settings()
    client = build_client(settings)
    await client.start()
    me = await client.get_me()
    print(f"Logged in as {utils.get_display_name(me)}. Session: {settings.session_path}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(login())
