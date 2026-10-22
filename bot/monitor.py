"""Watching the bot from outside, and copies of its data.

- Heartbeat: every HEARTBEAT_SECONDS, if both clients are connected and
  Telegram answers, GET HEALTHCHECK_URL (e.g. a check on healthchecks.io);
  otherwise GET its "/fail". When the bot is down the panel can't say so —
  the service notices the silence and tells me by mail or its own bot.
- Backups: every BACKUP_DAYS days (and on ".backup") the panel gets
  rules.json and a copy of assistant.db. Never the session: that one is full
  access to the account.
"""

import asyncio
import logging
import sqlite3
import tempfile
import time
from pathlib import Path

import httpx
from telethon import TelegramClient
from telethon.errors import RPCError

from bot.journal import Journal

log = logging.getLogger("tg-assistant.monitor")

HEARTBEAT_SECONDS = 5 * 60
BACKUP_CHECK_SECONDS = 60 * 60


async def alive(clients: list[TelegramClient]) -> bool:
    """Connected, and Telegram actually answers."""
    if not all(c.is_connected() for c in clients):
        return False
    try:
        await asyncio.wait_for(clients[0].get_me(), timeout=30)
    except (RPCError, ConnectionError, asyncio.TimeoutError) as e:
        log.warning("Telegram doesn't answer: %s", e)
        return False
    return True


async def heartbeat(clients: list[TelegramClient], url: str) -> None:
    async with httpx.AsyncClient(timeout=10) as http:
        while True:
            try:
                ok = await alive(clients)
                await http.get(url if ok else url.rstrip("/") + "/fail")
            except httpx.HTTPError as e:
                log.warning("Can't reach the healthcheck: %s", e)
            except Exception:
                log.exception("Heartbeat failed")  # never take the bot down with it
            await asyncio.sleep(HEARTBEAT_SECONDS)


async def send_backup(bot: TelegramClient, owner_id: int, journal: Journal, rules_path: Path) -> None:
    """rules.json and a consistent copy of the database, as files in the panel."""
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "assistant.db"
        target = sqlite3.connect(db_copy)
        try:
            journal.db.backup(target)
        finally:
            target.close()
        files = [str(p) for p in (rules_path, db_copy) if p.exists()]
        stamp = time.strftime("%Y-%m-%d")
        await bot.send_file(
            owner_id, files, force_document=True,
            caption=f"💾 Бэкап {stamp}: правила и база (без сессии). Восстановить — положить файлы в data/",
        )
    journal.set_meta("last_backup", str(time.time()))
    log.info("Backup sent to the panel")


async def backups(bot: TelegramClient, owner_id: int, journal: Journal, rules_path: Path, every_days: int) -> None:
    while True:
        last = float(journal.get_meta("last_backup") or 0)
        if time.time() - last >= every_days * 24 * 3600:
            try:
                await send_backup(bot, owner_id, journal, rules_path)
            except Exception:
                log.exception("Backup failed")  # never take the bot down with it
        await asyncio.sleep(BACKUP_CHECK_SECONDS)
