"""Meme polls (".memes"): in the chats I picked, every meme — a photo, a
video or a GIF, mine or anyone's — gets a "Оцени мем" poll from 1 to 10 as
a reply. After poll_hours the poll closes and the average goes to the chat.
".top" shows the week's best memes and authors from those averages, and
every Sunday afternoon each chat gets it by itself.

An album is one meme: one poll. Polls live in the journal, so a restart
doesn't leave them open forever.
"""

import asyncio
import datetime
import logging
import random
import time

from telethon import TelegramClient, types, utils
from telethon.errors import RPCError

from bot.journal import Journal
from bot.store import Store

log = logging.getLogger("tg-assistant.memes")

QUESTION = "Оцени мем"
SCORES = range(1, 11)
CHECK_EVERY_SECONDS = 60
TOP_DAYS = 7
SUNDAY = 6


def is_meme(message) -> bool:
    """A photo, a video or a GIF; not a sticker, a round video or a voice."""
    if message.sticker or message.video_note or message.voice:
        return False
    return bool(message.photo or message.gif or message.video)


def make_poll(poll_id: int, closed: bool = False) -> types.InputMediaPoll:
    return types.InputMediaPoll(poll=types.Poll(
        id=poll_id,
        question=types.TextWithEntities(QUESTION, []),
        answers=[types.PollAnswer(types.TextWithEntities(str(n), []), bytes([n])) for n in SCORES],
        hash=0,
        closed=closed or None,
        public_voters=True,
    ))


def average(results: types.PollResults | None) -> tuple[float, int] | None:
    """The average score and how many voted; None when nobody did."""
    if results is None or not results.results:
        return None
    votes = {r.option[0]: r.voters or 0 for r in results.results if r.option}
    total = sum(votes.values())
    if not total:
        return None
    return sum(score * n for score, n in votes.items()) / total, total


def summary(score: float, voters: int) -> str:
    return f"{medal(score)} Мем набрал {score:.1f}/10 ({voters} {plural(voters, 'голос', 'голоса', 'голосов')})"


def medal(score: float) -> str:
    return "🏆" if score >= 8 else "👍" if score >= 5 else "🥱"


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def link(chat_id: int, msg_id: int) -> str:
    """A link to a message, which only supergroups and channels have."""
    peer = str(chat_id)
    return f"https://t.me/c/{peer[4:]}/{msg_id}" if peer.startswith("-100") else ""


def format_top(journal: Journal, chat_id: int, now: float | None = None) -> str:
    since = (time.time() if now is None else now) - TOP_DAYS * 86400
    memes = journal.best_memes(chat_id, since)
    if not memes:
        return "За неделю ещё ни один мем не получил оценок"
    lines = ["🏆 Лучшие мемы недели"]
    for place, (meme_id, author, score, voters) in enumerate(memes, 1):
        url = link(chat_id, meme_id)
        lines.append(
            f"{place}. {score:.1f}/10 — {author} ({voters} {plural(voters, 'голос', 'голоса', 'голосов')})"
            + (f" {url}" if url else "")
        )
    lines.append("")
    lines.append("👑 Лучшие авторы")
    for place, (author, score, count) in enumerate(journal.best_meme_authors(chat_id, since), 1):
        lines.append(f"{place}. {author} — {score:.1f} в среднем, {count} {plural(count, 'мем', 'мема', 'мемов')}")
    return "\n".join(lines)


class MemePolls:
    def __init__(
        self, client: TelegramClient, store: Store, journal: Journal, poll_hours: float,
        top_hour: int = -1, tz: datetime.tzinfo = datetime.UTC,
    ):
        self.client = client
        self.store = store
        self.journal = journal
        self.poll_seconds = poll_hours * 3600
        self.top_hour = top_hour
        self.tz = tz
        self._last_album: dict[int, int] = {}  # chat id -> grouped_id already polled
        # Chats a poll, a score or a top is being sent to right now, and what
        # was sent: my handlers mustn't take it for me writing there.
        self._sending: set[int] = set()
        self._sent: set[tuple[int, int]] = set()

    def is_own(self, event) -> bool:
        """Whether an outgoing message is a poll, a score or a top from here, not mine."""
        return event.chat_id in self._sending or (event.chat_id, event.id) in self._sent

    async def send(self, chat_id: int, **kwargs):
        self._sending.add(chat_id)
        try:
            sent = await self.client.send_message(chat_id, **kwargs)
            self._sent.add((chat_id, sent.id))
            return sent
        finally:
            self._sending.discard(chat_id)

    async def on_message(self, message) -> None:
        chat_id = message.chat_id
        if chat_id not in self.store.memes or not is_meme(message):
            return
        if message.grouped_id:
            if self._last_album.get(chat_id) == message.grouped_id:
                return  # the rest of an album that already has its poll
            self._last_album[chat_id] = message.grouped_id
        poll_id = random.getrandbits(63)
        try:
            sent = await self.send(chat_id, file=make_poll(poll_id), reply_to=message.id)
        except (RPCError, ValueError) as e:
            log.warning("Can't send a meme poll in %s: %s", chat_id, e)
            return
        log.info("Meme poll %s for %s in %s", sent.id, message.id, chat_id)
        try:
            sender = await message.get_sender()
        except (RPCError, ValueError):
            sender = None
        author = (utils.get_display_name(sender) if sender else "") or "Кто-то"
        self.journal.add_meme_poll(
            chat_id, sent.id, poll_id, message.id, message.sender_id, author, time.time() + self.poll_seconds,
        )

    async def close_due(self, now: float | None = None) -> None:
        for chat_id, msg_id, poll_id in self.journal.meme_polls_due(time.time() if now is None else now):
            await self.close(chat_id, msg_id, poll_id)

    async def close(self, chat_id: int, msg_id: int, poll_id: int) -> None:
        try:
            closed = await self.client.edit_message(chat_id, msg_id, file=make_poll(poll_id, closed=True))
        except (RPCError, ValueError) as e:
            # Deleted or otherwise gone: it can't be closed later either.
            log.warning("Can't close meme poll %s in %s: %s", msg_id, chat_id, e)
            self.journal.close_meme_poll(chat_id, msg_id, None, 0)
            return
        result = average(getattr(getattr(closed, "media", None), "results", None))
        if result is None:
            # The edit may come back without counts; the message itself has them.
            try:
                fresh = await self.client.get_messages(chat_id, ids=msg_id)
                result = average(getattr(getattr(fresh, "media", None), "results", None))
            except (RPCError, ValueError) as e:
                log.warning("Can't read meme poll %s in %s: %s", msg_id, chat_id, e)
        self.journal.close_meme_poll(chat_id, msg_id, *(result or (None, 0)))
        if result is None:
            return  # nobody voted: nothing to brag about
        try:
            await self.send(chat_id, message=summary(*result), reply_to=msg_id)
        except (RPCError, ValueError) as e:
            log.warning("Can't post a meme score in %s: %s", chat_id, e)

    def top(self, chat_id: int) -> str:
        return format_top(self.journal, chat_id)

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
        """Closes polls as they come due and sends the weekly top, forever."""
        while True:
            try:
                await self.close_due()
                await self.weekly_top()
            except Exception:
                log.exception("Meme polls job failed")
            await asyncio.sleep(CHECK_EVERY_SECONDS)
