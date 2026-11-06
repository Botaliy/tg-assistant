"""Meme ratings (".memes"): in the chats I picked, every meme — a photo, a
video or a GIF, mine or anyone's — gets a reply with buttons 1–10, plus a
joke button if one is set. The average is in the message and changes with
every click. ".top" shows the week's best memes and authors, and every
Sunday afternoon each chat gets it by itself.

A userbot can't send buttons, so it sends the message through the panel
bot's inline mode ("via @bot"): the panel bot doesn't have to be in the
chat, the clicks still reach it. Its inline answers are for me only; its
meme buttons are the one thing it takes from anyone.

An album is one meme. Votes live in the journal, one score per person (a
new click changes it), so restarts lose nothing.
"""

import asyncio
import datetime
import logging
import random
from collections.abc import Awaitable, Callable

from telethon import Button, TelegramClient, events, utils
from telethon.errors import MessageNotModifiedError, RPCError

from bot.journal import Journal
from bot.store import Store

log = logging.getLogger("tg-assistant.memes")

QUESTION = "Оцени мем"
SCORES = range(1, 11)
CHECK_EVERY_SECONDS = 60
TOP_DAYS = 7
SUNDAY = 6
QUERY = "meme "  # the inline query: "meme <token>"
DATA = "mr:"  # button data: "mr:<token>:<score or j>"
NO_INLINE = (
    "🗳 Не получается прислать оценку мема: включи inline-режим у пульта — "
    "@BotFather → /setinline → выбери бота → любой текст-подсказка"
)


def is_meme(message) -> bool:
    """A photo, a video or a GIF; not a sticker, a round video or a voice."""
    if message.sticker or message.video_note or message.voice:
        return False
    return bool(message.photo or message.gif or message.video)


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def votes(n: int) -> str:
    return f"{n} {plural(n, 'голос', 'голоса', 'голосов')}"


def header(avg: float | None, voters: int) -> str:
    if avg is None:
        return f"{QUESTION} 👇"
    return f"{QUESTION} · ⭐ {avg:.1f} ({votes(voters)})"


def buttons(token: int, joke: str = "", jokes: int = 0) -> list:
    rows = [
        [Button.inline(str(n), f"{DATA}{token}:{n}".encode()) for n in SCORES[:5]],
        [Button.inline(str(n), f"{DATA}{token}:{n}".encode()) for n in SCORES[5:]],
    ]
    if joke:
        rows.append([Button.inline(f"{joke} · {jokes}" if jokes else joke, f"{DATA}{token}:j".encode())])
    return rows


def link(chat_id: int, msg_id: int) -> str:
    """A link to a message, which only supergroups and channels have."""
    peer = str(chat_id)
    return f"https://t.me/c/{peer[4:]}/{msg_id}" if peer.startswith("-100") else ""


def format_top(journal: Journal, chat_id: int, now: float) -> str:
    since = now - TOP_DAYS * 86400
    memes = journal.best_memes(chat_id, since)
    if not memes:
        return "За неделю ещё ни один мем не получил оценок"
    lines = ["🏆 Лучшие мемы недели"]
    for place, (meme_id, author, score, voters) in enumerate(memes, 1):
        url = link(chat_id, meme_id)
        lines.append(f"{place}. {score:.1f}/10 — {author} ({votes(voters)})" + (f" {url}" if url else ""))
    lines.append("")
    lines.append("👑 Лучшие авторы")
    for place, (author, score, count) in enumerate(journal.best_meme_authors(chat_id, since), 1):
        lines.append(f"{place}. {author} — {score:.1f} в среднем, {count} {plural(count, 'мем', 'мема', 'мемов')}")
    return "\n".join(lines)


class MemeRatings:
    def __init__(
        self, client: TelegramClient, store: Store, journal: Journal, bot_username: str,
        notify: Callable[[str], Awaitable[None]], joke: str = "",
        top_hour: int = -1, tz: datetime.tzinfo = datetime.UTC,
    ):
        self.client = client
        self.store = store
        self.journal = journal
        self.bot_username = bot_username
        self.notify = notify
        self.joke = joke
        self.top_hour = top_hour
        self.tz = tz
        self._last_album: dict[int, int] = {}  # chat id -> grouped_id already rated
        self._told_no_inline = False
        # Chats a rating or a top is being sent to right now, and what was
        # sent: my handlers mustn't take it for me writing there.
        self._sending: set[int] = set()
        self._sent: set[tuple[int, int]] = set()

    def is_own(self, event) -> bool:
        """Whether an outgoing message is a rating or a top from here, not mine."""
        return event.chat_id in self._sending or (event.chat_id, event.id) in self._sent

    async def _sending_to(self, chat_id: int, send: Callable[[], Awaitable]):
        self._sending.add(chat_id)
        try:
            sent = await send()
            self._sent.add((chat_id, sent.id))
            return sent
        finally:
            self._sending.discard(chat_id)

    async def send(self, chat_id: int, **kwargs):
        return await self._sending_to(chat_id, lambda: self.client.send_message(chat_id, **kwargs))

    # The userbot's side: a meme in a chat -> the rating message under it.

    async def on_message(self, message) -> None:
        chat_id = message.chat_id
        if chat_id not in self.store.memes or not is_meme(message):
            return
        if message.grouped_id:
            if self._last_album.get(chat_id) == message.grouped_id:
                return  # the rest of an album that's already rated
            self._last_album[chat_id] = message.grouped_id
        try:
            sender = await message.get_sender()
        except (RPCError, ValueError):
            sender = None
        token = random.getrandbits(62)
        self.journal.add_meme(
            token, chat_id, message.id, message.sender_id, (utils.get_display_name(sender) if sender else "") or "Кто-то",
        )
        try:
            results = await self.client.inline_query(self.bot_username, f"{QUERY}{token}", entity=chat_id)
            if not results:
                raise ValueError("the panel bot gave no inline result")
            sent = await self._sending_to(chat_id, lambda: results[0].click(chat_id, reply_to=message.id))
        except (RPCError, ValueError) as e:
            log.warning("Can't send a meme rating in %s: %s", chat_id, e)
            if not self._told_no_inline:
                self._told_no_inline = True
                await self.notify(f"{NO_INLINE}\n({e})")
            return
        log.info("Meme rating %s for %s in %s", sent.id, message.id, chat_id)

    # The panel bot's side: the inline answer and the clicks.

    def setup_bot(self, bot: TelegramClient, owner_id: int) -> None:
        @bot.on(events.InlineQuery())
        async def on_inline(event) -> None:
            text = event.text or ""
            # Anyone can type @bot in any chat: only my userbot's rating requests get an answer.
            if event.sender_id != owner_id or not text.startswith(QUERY) or not text[len(QUERY):].isdigit():
                await event.answer([], cache_time=0, private=True)
                return
            token = int(text[len(QUERY):])
            result = event.builder.article(QUESTION, id=str(token), text=header(None, 0), buttons=buttons(token, self.joke))
            await event.answer([result], cache_time=0, private=True)

        @bot.on(events.CallbackQuery(pattern=DATA.encode()))
        async def on_click(event) -> None:
            _, token, choice = event.data.decode().split(":")
            token = int(token)
            if not self.journal.has_meme(token):
                await event.answer("Этот мем уже не оценить")
                return
            if choice == "j":
                pressed = self.journal.toggle_meme_joke(token, event.sender_id)
                toast = "😏" if pressed else "Передумал 🙂"
            else:
                self.journal.vote_meme(token, event.sender_id, int(choice))
                toast = f"Твоя оценка: {choice}"
            avg, voters, jokes = self.journal.meme_tally(token)
            try:
                await bot.edit_message(
                    event.query.msg_id, header(avg, voters), buttons=buttons(token, self.joke, jokes),
                )
            except MessageNotModifiedError:
                pass
            except RPCError as e:
                log.warning("Can't update a meme rating: %s", e)
            await event.answer(toast)

    # The top.

    def top(self, chat_id: int) -> str:
        return format_top(self.journal, chat_id, datetime.datetime.now(self.tz).timestamp())

    async def weekly_top(self, now: datetime.datetime | None = None) -> None:
        """On Sunday from top_hour: the week's top to every meme chat, once a
        week (remembered in the journal, so a restart doesn't send it twice)."""
        now = now or datetime.datetime.now(self.tz)
        if self.top_hour < 0 or now.weekday() != SUNDAY or now.hour < self.top_hour:
            return
        week = now.date().isoformat()
        if self.journal.get_meta("meme_top_sent") == week:
            return
        self.journal.set_meta("meme_top_sent", week)
        for chat_id in list(self.store.memes):
            if not self.journal.best_memes(chat_id, now.timestamp() - TOP_DAYS * 86400, limit=1):
                continue  # a quiet week: no "nothing to show" message
            try:
                await self.send(chat_id, message=format_top(self.journal, chat_id, now.timestamp()), link_preview=False)
            except (RPCError, ValueError) as e:
                log.warning("Can't post the weekly meme top in %s: %s", chat_id, e)

    async def run(self) -> None:
        """Sends the weekly top when it's time, forever."""
        while True:
            try:
                await self.weekly_top()
            except Exception:
                log.exception("Weekly meme top failed")
            await asyncio.sleep(CHECK_EVERY_SECONDS)
