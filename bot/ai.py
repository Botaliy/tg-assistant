"""AI conversation mode: in chats where I turned it on, Claude answers for me.

Context stays small. Older messages are folded into a short summary kept per
chat (Store.ai_chats); only the messages after it go to the model verbatim.
That verbatim tail only grows between folds, so the request prefix is stable
and prompt caching works until the next fold.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import anthropic
from telethon import TelegramClient

from bot.store import AiChat, Store

log = logging.getLogger("tg-assistant.ai")

# Fold when this many messages piled up after the summary; keep this many verbatim.
FOLD_AT = 30
KEEP = 10
# People often send several short messages in a row: answer once they pause.
REPLY_DELAY_SECONDS = 8
# The model answers with exactly this when nothing needs saying ("ок", "👍").
SKIP = "[skip]"

SYSTEM = """Ты отвечаешь в Telegram вместо владельца аккаунта ({me}) в его личной переписке \
с собеседником ({partner}). Сообщения собеседника приходят как user, прежние ответы владельца — \
как assistant.

Пиши как живой человек в мессенджере: коротко, без markdown и списков, на языке собеседника, \
в тоне прежних ответов владельца. Не выдумывай фактов о его жизни, планов и обещаний, которых нет \
в переписке; если не знаешь, отвечай уклончиво или переспроси.
Если на последние сообщения отвечать не нужно, ответь ровно {skip}.
Если тебя всерьёз спросят, не бот ли ты, честно скажи, что это автоответчик владельца.{persona}{summary}"""

FOLD_SYSTEM = """Ты сжимаешь переписку в Telegram в краткое резюме для продолжения разговора.
Сохрани: кто есть кто, о чём договорились, открытые вопросы, важные факты, тон общения.
Пиши по-русски, сжато, не больше 15 строк. Верни только резюме."""


def describe(message) -> str:
    """A message as text; media becomes a short placeholder."""
    text = message.raw_text or ""
    for attr, name in (
        ("sticker", "стикер"), ("voice", "голосовое"), ("video_note", "кружок"),
        ("photo", "фото"), ("video", "видео"), ("gif", "gif"), ("document", "файл"),
    ):
        if getattr(message, attr, None):
            return f"[{name}] {text}".strip()
    return text


class AiResponder:
    def __init__(
        self, client: TelegramClient, store: Store, api: anthropic.AsyncAnthropic,
        model: str, effort: str, me_name: str, notify: Callable[[str], Awaitable[None]],
    ):
        self.client = client
        self.store = store
        self.api = api
        self.model = model
        self.effort = effort
        self.me_name = me_name
        self.notify = notify
        self._pending: dict[int, asyncio.Task] = {}

    def on_incoming(self, chat_id: int) -> None:
        """A new message in an AI chat: (re)start the wait before answering."""
        task = self._pending.pop(chat_id, None)
        if task is not None:
            task.cancel()
        self._pending[chat_id] = asyncio.create_task(self._reply_later(chat_id))

    async def _reply_later(self, chat_id: int) -> None:
        try:
            await asyncio.sleep(REPLY_DELAY_SECONDS)
            await self._reply(chat_id)
        except asyncio.CancelledError:
            raise
        except anthropic.APIError as e:
            log.warning("Claude request failed in %s: %s", chat_id, e)
            await self.notify(f"ИИ не смог ответить ({self._label(chat_id)}): {e}")
        except Exception:
            log.exception("AI reply failed in %s", chat_id)
        finally:
            if self._pending.get(chat_id) is asyncio.current_task():
                del self._pending[chat_id]

    def _label(self, chat_id: int) -> str:
        chat = self.store.ai_chats.get(chat_id)
        return (chat.label if chat else "") or str(chat_id)

    async def _reply(self, chat_id: int) -> None:
        chat = self.store.ai_chats.get(chat_id)
        if chat is None:
            return  # turned off while we were waiting
        history = [
            m async for m in self.client.iter_messages(
                chat_id, limit=FOLD_AT + KEEP, min_id=chat.summarized_up_to,
            )
        ]
        history.reverse()
        if not history or history[-1].out:
            return  # I've already answered myself

        if len(history) > FOLD_AT:
            old, history = history[:-KEEP], history[-KEEP:]
            await self._fold(chat, old)

        messages = []
        for m in history:
            text = describe(m)
            if not text:
                continue
            role = "assistant" if m.out else "user"
            if not messages and role == "assistant":
                continue  # the conversation sent to the model must start with the other side
            messages.append({"role": role, "content": text})
        if not messages:
            return

        partner = chat.label or "имя неизвестно"
        system = SYSTEM.format(
            me=self.me_name, partner=partner, skip=SKIP,
            persona=f"\n\nКак общается владелец:\n{self.store.persona}" if self.store.persona else "",
            summary=f"\n\nРаньше в этой переписке:\n{chat.summary}" if chat.summary else "",
        )
        async with self.client.action(chat_id, "typing"):
            text = await self._ask(system, messages)
        if not text or text.strip() == SKIP:
            log.info("AI chose not to answer in %s", chat_id)
            return
        await self.client.send_message(chat_id, text.strip())
        log.info("AI answered in %s", chat_id)

    async def _fold(self, chat: AiChat, old: list) -> None:
        transcript = "\n".join(
            f"{self.me_name if m.out else chat.label or 'Собеседник'}: {describe(m)}" for m in old
        )
        prompt = f"Прежнее резюме:\n{chat.summary or '(нет)'}\n\nНовые сообщения:\n{transcript}"
        summary = await self._ask(FOLD_SYSTEM, [{"role": "user", "content": prompt}])
        if summary:
            chat.summary = summary.strip()
            chat.summarized_up_to = old[-1].id
            self.store.save()
            log.info("Folded %d messages into the summary for %s", len(old), chat.label)

    async def _ask(self, system: str, messages: list[dict]) -> str | None:
        response = await self.api.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            system=system,
            messages=messages,
            output_config={"effort": self.effort},
            # Re-runs a request a safety classifier declined on a fitting model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            cache_control={"type": "ephemeral"},
        )
        if response.stop_reason == "refusal":
            log.warning("Claude declined: %s", response.stop_details)
            return None
        return "".join(b.text for b in response.content if b.type == "text")
