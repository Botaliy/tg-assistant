import asyncio
import logging

from telethon import TelegramClient, events, utils
from telethon.errors import RPCError

from bot import commands
from bot.config import Settings, get_settings
from bot.rules import Cooldown
from bot.store import Store

log = logging.getLogger("tg-assistant")


def build_client(settings: Settings) -> TelegramClient:
    return TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)


async def run() -> None:
    settings = get_settings()
    store = Store(settings.rules_path)
    cooldown = Cooldown(settings.reply_cooldown_seconds)
    client = build_client(settings)

    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Not logged in yet: run `python -m bot.login` first")
    me = await client.get_me()

    async def delete(chat_id: int, ids: list[int]) -> None:
        # revoke=True removes the messages for the other side too. In groups
        # that only works for someone else's messages if I'm an admin there.
        try:
            await client.delete_messages(chat_id, ids, revoke=True)
        except RPCError as e:
            log.warning("Can't delete %s in %s: %s", ids, chat_id, e)

    async def run_del(event, cmd: commands.Command) -> str | None:
        try:
            count = commands.parse_del_count(cmd.arg)
        except ValueError as e:
            return str(e)
        if count is not None:
            ids = []
            async for m in client.iter_messages(event.chat_id, limit=500):
                if not m.out:
                    ids.append(m.id)
                    if len(ids) == count:
                        break
        elif event.is_reply:
            ids = [event.reply_to_msg_id]
        else:
            return "Напиши .del ответом на сообщение или .del N"
        await delete(event.chat_id, ids + [event.id])
        log.info("Deleted %d message(s) in %s", len(ids), event.chat_id)
        return None

    @client.on(events.NewMessage(outgoing=True))
    async def on_command(event) -> None:
        cmd = commands.parse(event.raw_text)
        if cmd is None:
            return
        in_saved = event.chat_id == me.id

        if cmd.name == "del":
            answer = await run_del(event, cmd)
        else:
            answer = commands.execute(store, cmd, chat_id=None if in_saved else event.chat_id)
            if not in_saved:
                # Don't leave ".mute" hanging in front of the interlocutor.
                await delete(event.chat_id, [event.id])

        if answer is None:
            return
        if in_saved:
            await event.reply(answer)
        else:
            # Answers about other chats go to Saved Messages, not into the chat.
            title = utils.get_display_name(await event.get_chat())
            await client.send_message("me", f"[{title}] {answer}")

    @client.on(events.NewMessage(incoming=True))
    async def on_incoming(event) -> None:
        text = event.raw_text or ""
        if store.should_delete(event.chat_id, event.is_private, text):
            await delete(event.chat_id, [event.id])
            log.info("Auto-deleted message %s in %s", event.id, event.chat_id)
            return

        rule = store.reply_for(event.chat_id, event.is_private, text)
        if rule is None:
            return
        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return  # two auto-responders talking to each other never ends well
        if cooldown.ready((event.chat_id, rule.id)):
            await event.reply(rule.text)
            log.info("Auto-replied in %s by rule %s", event.chat_id, rule.id)

    log.info("Running as %s (id %s)", utils.get_display_name(me), me.id)
    await client.run_until_disconnected()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
