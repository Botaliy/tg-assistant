"""Two clients in one process: the userbot (my account) does the work —
deletes messages and auto-replies — and the panel bot is how I control it."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from telethon import TelegramClient, events, utils
from telethon.errors import RPCError

from bot import commands, panel
from bot.commands import Answer, error
from bot.config import Settings, get_settings
from bot.rules import Cooldown
from bot.store import Store

if TYPE_CHECKING:
    from bot.ai import AiResponder

log = logging.getLogger("tg-assistant")



def build_client(settings: Settings) -> TelegramClient:
    return TelegramClient(str(settings.session_path), settings.api_id, settings.api_hash)


class Assistant:
    """What the userbot can do for me; used by both the userbot and the panel."""

    def __init__(self, client: TelegramClient, store: Store, ai_available: bool):
        self.client = client
        self.store = store
        self.ai_available = ai_available
        self.ai: AiResponder | None = None  # set once the AI is up
        self._tasks: set[asyncio.Task] = set()

    async def delete(self, chat_id: int, ids: list[int]) -> bool:
        # revoke=True removes the messages for the other side too. In groups
        # that only works for someone else's messages if I'm an admin there.
        try:
            await self.client.delete_messages(chat_id, ids, revoke=True)
            return True
        except (ValueError, RPCError) as e:
            log.warning("Can't delete %s in %s: %s", ids, chat_id, e)
            return False

    async def delete_last(self, chat_id: int, count: int, label: str) -> Answer:
        """Deletes the interlocutor's last `count` messages in a chat."""
        ids = []
        try:
            async for m in self.client.iter_messages(chat_id, limit=500):
                if not m.out:
                    ids.append(m.id)
                    if len(ids) == count:
                        break
        except (ValueError, RPCError) as e:
            return error(f"Не открыть чат {label}: {e}")
        if ids and not await self.delete(chat_id, ids):
            return error(f"Не получилось удалить сообщения ({label})")
        return Answer(f"Удалено сообщений: {len(ids)} ({label})")

    async def resolve(self, target: str) -> tuple[int, str]:
        """Chat id and a label for "@username", "+phone" or "#chat_id".

        Raises ValueError when there's no such chat.
        """
        if target.startswith("#"):
            chat_id = int(target[1:])
            return chat_id, await self.label_for(chat_id)
        try:
            entity = await self.client.get_entity(target)
        except RPCError as e:
            raise ValueError(str(e)) from e
        label = target if target.startswith("@") else utils.get_display_name(entity)
        return utils.get_peer_id(entity), label

    async def label_for(self, chat_id: int) -> str:
        try:
            entity = await self.client.get_entity(chat_id)
        except (ValueError, RPCError):
            return ""
        username = getattr(entity, "username", None)
        return f"@{username}" if username else utils.get_display_name(entity)

    @property
    def deepseek_available(self) -> bool:
        return self.ai is not None and self.ai.deepseek is not None

    def answer_with_ai(self, chat_id: int, label: str, reply_to: int | None = None) -> Answer:
        """A one-off AI answer in a chat, in the background."""
        if self.ai is None:
            return error(commands.NO_AI)
        task = asyncio.create_task(self.ai.answer_now(chat_id, label, reply_to))
        self._tasks.add(task)  # keep a reference until it's done
        task.add_done_callback(self._tasks.discard)
        return Answer(f"ИИ отвечает {label or chat_id}")

    async def run_command(
        self, cmd: commands.Command, chat_id: int | None, label: str,
        reply_to: int | None = None,
    ) -> Answer:
        """A text command for chat_id (None: every private chat).

        ".del" as a reply needs the conversation itself and is handled by the
        userbot; here ".del" is only ".del @user N".
        """
        if cmd.target:
            try:
                chat_id, label = await self.resolve(cmd.target)
            except ValueError:
                return error(f"Не нашёл {cmd.target}")
        if cmd.name == "del":
            try:
                count = commands.parse_del_count(cmd.arg)
            except ValueError as e:
                return error(str(e))
            if chat_id is None or count is None:
                return error("Формат: .del @user N")
            return await self.delete_last(chat_id, count, label)
        if cmd.name == "ans":
            if chat_id is None:
                return error("Кому? Формат: .ans @user")
            return self.answer_with_ai(chat_id, label, None if cmd.target else reply_to)
        if cmd.name == "reset":
            if chat_id is None:
                return error("Кому? Формат: .reset @user")
            if self.ai is None:
                return error(commands.NO_AI)
            self.ai.reset_limit(chat_id)
            return Answer(f"Лимит ИИ для {label or chat_id} сброшен")
        if cmd.name == "ai" and not self.ai_available:
            return error(commands.NO_AI)
        if cmd.name == "model" and commands.MODELS.get(cmd.arg.lower()) and not self.deepseek_available:
            return error(commands.NO_DEEPSEEK)
        return commands.execute(self.store, cmd, chat_id, label)


def setup_userbot(
    client: TelegramClient, assistant: Assistant, store: Store,
    cooldown: Cooldown, notify: Callable[[str], Awaitable[None]], panel_id: int,
    ai: "AiResponder | None",
) -> None:
    @client.on(events.NewMessage(outgoing=True))
    async def on_command(event) -> None:
        if event.chat_id == panel_id:
            return  # commands to the panel bot are its business
        if ai is not None and ai.is_own(event):
            return  # the AI's reply is never a command, whatever it says
        # A forwarded message is someone else's text, even if it starts with a dot.
        cmd = None if event.fwd_from else commands.parse(event.raw_text)
        if cmd is None:
            if ai is not None and event.is_private:
                await ai.on_outgoing(event)
            return
        log.info("Command %r in %s, reply to %s", event.raw_text, event.chat_id, event.reply_to_msg_id)
        # Typed in a conversation: hide it from the interlocutor right away.
        await assistant.delete(event.chat_id, [event.id])
        title = utils.get_display_name(await event.get_chat())

        try:
            if cmd.name == "del" and not cmd.target and not cmd.arg:
                if not event.is_reply:
                    answer = error("Напиши .del ответом на сообщение или .del N")
                elif await assistant.delete(event.chat_id, [event.reply_to_msg_id]):
                    answer = Answer("Удалено")
                else:
                    answer = error("Не получилось удалить сообщение")
            elif cmd.name == "del" and not cmd.target:
                count = commands.parse_del_count(cmd.arg)
                answer = await assistant.delete_last(event.chat_id, count, title)
            else:
                answer = await assistant.run_command(cmd, event.chat_id, title, event.reply_to_msg_id)
        except ValueError as e:
            answer = error(str(e))
        except Exception as e:
            log.exception("Command %r failed", event.raw_text)
            answer = error(f"Ошибка: {e}")
        log.info("Command .%s in %s: %s", cmd.name, event.chat_id, answer.text)

        # Silent in conversations: only errors and asked-for output, and
        # those go to the panel.
        if answer.is_error or cmd.name in commands.OUTPUT_COMMANDS:
            await notify(f"[{title}] {answer.text}")

    @client.on(events.NewMessage(incoming=True))
    async def on_incoming(event) -> None:
        if event.chat_id == panel_id:
            return
        text = event.raw_text or ""
        if store.should_delete(event.chat_id, event.is_private, text):
            await assistant.delete(event.chat_id, [event.id])
            log.info("Auto-deleted message %s in %s", event.id, event.chat_id)
            return

        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return  # two auto-responders talking to each other never ends well
        mention_id = event.id if event.mentioned else None
        if ai is not None and await ai.on_incoming(event.chat_id, sender, mention_id):
            return  # the AI answers instead of keyword rules

        rule = store.reply_for(event.chat_id, event.is_private, text)
        if rule is not None and cooldown.ready((event.chat_id, rule.id)):
            await event.reply(rule.text)
            log.info("Auto-replied in %s by rule %s", event.chat_id, rule.id)


async def run() -> None:
    settings = get_settings()
    store = Store(settings.rules_path)
    client = build_client(settings)

    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Not logged in yet: run `python -m bot.login` first")
    me = await client.get_me()

    bot = TelegramClient(str(settings.panel_session_path), settings.api_id, settings.api_hash)
    await bot.start(bot_token=settings.bot_token)
    bot_me = await bot.get_me()

    assistant = Assistant(client, store, ai_available=bool(settings.anthropic_api_key))

    async def notify(text: str) -> None:
        try:
            # Plain text: AI replies and reports quote strangers' messages.
            await bot.send_message(me.id, text, parse_mode=None, link_preview=False)
        except (RPCError, ValueError) as e:
            # ValueError: the panel bot hasn't seen me yet (no /start pressed).
            log.warning("Can't reach the panel: %s", e)

    ai = None
    if settings.anthropic_api_key:
        # Imported only when needed: the SDK adds ~35 MB to a 60 MB process.
        import anthropic

        from bot.ai import AiResponder
        from bot.deepseek import DeepSeek

        ai = AiResponder(
            client, store, anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key),
            settings.ai_model, settings.ai_effort, settings.ai_daily_per_chat,
            settings.ai_daily_total, settings.ai_stranger_limit, utils.get_display_name(me), notify,
            known_unlimited=settings.ai_known_unlimited,
            deepseek=DeepSeek(settings.deepseek_api_key, settings.deepseek_model, settings.deepseek_effort)
            if settings.deepseek_api_key else None,
        )
    setup_userbot(
        client, assistant, store, Cooldown(settings.reply_cooldown_seconds), notify, bot_me.id, ai,
    )
    assistant.ai = ai
    panel.setup(bot, assistant, store, owner_id=me.id)

    if not store.panel_started:
        # A bot can't write first: press /start in it on my behalf, once.
        await client.send_message(bot_me.username, "/start")
        store.set_panel_started()

    log.info(
        "Running as %s (id %s), panel @%s, AI %s", utils.get_display_name(me), me.id,
        bot_me.username, settings.ai_model if ai else "off",
    )
    if ai is not None and ai.deepseek is not None:
        log.info("DeepSeek on: %s, effort %s", ai.deepseek.model, ai.deepseek.effort or "default")
    await asyncio.gather(client.run_until_disconnected(), bot.run_until_disconnected())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
