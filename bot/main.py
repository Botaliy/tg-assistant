import asyncio
import logging

from telethon import TelegramClient, events, utils
from telethon.errors import RPCError
from telethon.tl.functions.channels import CreateChannelRequest

from bot import commands
from bot.commands import Answer, error
from bot.config import Settings, get_settings
from bot.rules import Cooldown
from bot.store import Store

log = logging.getLogger("tg-assistant")

CONTROL_CHAT_TITLE = "Ассистент"


def build_client(settings: Settings) -> TelegramClient:
    return TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)


async def ensure_control_chat(client: TelegramClient, store: Store) -> int:
    """The private group where I give commands and get answers.

    Created on the first start. A supergroup, because a basic group can't be
    created with nobody but me in it.
    """
    if store.control_chat_id is not None:
        for attempt in range(2):
            try:
                await client.get_entity(store.control_chat_id)
                return store.control_chat_id
            except ValueError:
                # Not in the session's entity cache (e.g. after a re-login):
                # loading dialogs fills it.
                if attempt == 0:
                    await client.get_dialogs()
            except RPCError as e:
                log.warning("Control chat %s is gone (%s), creating a new one", store.control_chat_id, e)
                break

    result = await client(CreateChannelRequest(
        title=CONTROL_CHAT_TITLE, about="Служебный чат tg-assistant", megagroup=True,
    ))
    chat_id = utils.get_peer_id(result.chats[0])
    store.set_control_chat(chat_id)
    await client.send_message(chat_id, commands.HELP)
    log.info("Created control chat %s", chat_id)
    return chat_id


async def run() -> None:
    settings = get_settings()
    store = Store(settings.rules_path)
    cooldown = Cooldown(settings.reply_cooldown_seconds)
    client = build_client(settings)

    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Not logged in yet: run `python -m bot.login` first")
    me = await client.get_me()
    control_id = await ensure_control_chat(client, store)

    async def delete(chat_id: int, ids: list[int]) -> bool:
        # revoke=True removes the messages for the other side too. In groups
        # that only works for someone else's messages if I'm an admin there.
        try:
            await client.delete_messages(chat_id, ids, revoke=True)
            return True
        except RPCError as e:
            log.warning("Can't delete %s in %s: %s", ids, chat_id, e)
            return False

    async def resolve(target: str) -> tuple[int, str]:
        """Chat id and a label for "@username", "+phone" or "#chat_id"."""
        if target.startswith("#"):
            chat_id = int(target[1:])
            try:
                return chat_id, utils.get_display_name(await client.get_entity(chat_id))
            except (ValueError, RPCError):
                return chat_id, ""
        entity = await client.get_entity(target)
        label = target if target.startswith("@") else utils.get_display_name(entity)
        return utils.get_peer_id(entity), label

    async def run_del(event, cmd: commands.Command, chat_id: int | None, label: str) -> Answer:
        try:
            count = commands.parse_del_count(cmd.arg)
        except ValueError as e:
            return error(str(e))
        if count is not None:
            if chat_id is None:
                return error("Чьи сообщения? Формат: .del @user N")
            ids = []
            async for m in client.iter_messages(chat_id, limit=500):
                if not m.out:
                    ids.append(m.id)
                    if len(ids) == count:
                        break
            if ids and not await delete(chat_id, ids):
                return error(f"Не получилось удалить сообщения ({label})")
            return Answer(f"Удалено сообщений: {len(ids)} ({label})")
        if event.is_reply:
            if not await delete(event.chat_id, [event.reply_to_msg_id]):
                return error("Не получилось удалить сообщение")
            return Answer("Удалено")
        return error("Напиши .del ответом на сообщение или .del N")

    async def handle(event, cmd: commands.Command, in_control: bool, title: str) -> Answer:
        if cmd.target:
            try:
                chat_id, label = await resolve(cmd.target)
            except (ValueError, RPCError):
                return error(f"Не нашёл {cmd.target}")
        elif in_control:
            chat_id, label = None, ""
        else:
            chat_id, label = event.chat_id, title
        if cmd.name == "del":
            return await run_del(event, cmd, chat_id, label)
        return commands.execute(store, cmd, chat_id, label)

    @client.on(events.NewMessage(outgoing=True))
    async def on_command(event) -> None:
        cmd = commands.parse(event.raw_text)
        if cmd is None:
            return
        in_control = event.chat_id == control_id
        title = ""
        if not in_control:
            # Typed in a conversation: hide it from the interlocutor right away.
            await delete(event.chat_id, [event.id])
            title = utils.get_display_name(await event.get_chat())

        try:
            answer = await handle(event, cmd, in_control, title)
        except Exception as e:
            log.exception("Command %r failed", event.raw_text)
            answer = error(f"Ошибка: {e}")

        if in_control:
            await event.reply(answer.text)
        elif answer.is_error or cmd.name in commands.OUTPUT_COMMANDS:
            # In conversations it's silent: only errors and asked-for output,
            # and those go to the control chat.
            await client.send_message(control_id, f"[{title}] {answer.text}")

    @client.on(events.NewMessage(incoming=True))
    async def on_incoming(event) -> None:
        if event.chat_id == control_id:
            return
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

    log.info("Running as %s (id %s), control chat %s", utils.get_display_name(me), me.id, control_id)
    await client.run_until_disconnected()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
