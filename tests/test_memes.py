import asyncio
from types import SimpleNamespace

from telethon import types

from bot import commands
from bot.journal import Journal
from bot.memes import MemePolls, average, format_top, is_meme, link, plural, summary
from bot.store import Store


def message(chat_id=5, msg_id=1, photo=True, grouped_id=None, sender="Вася", sender_id=11, **media):
    fields = {"photo": photo, "gif": False, "video": False, "sticker": False, "video_note": False, "voice": False}
    fields.update(media)

    async def get_sender():
        return types.User(id=sender_id, first_name=sender) if sender else None

    return SimpleNamespace(
        chat_id=chat_id, id=msg_id, grouped_id=grouped_id, sender_id=sender_id, get_sender=get_sender, **fields,
    )


class FakeClient:
    def __init__(self, results=None):
        self.sent = []
        self.edited = []
        self.results = results

    async def send_message(self, chat_id, message="", file=None, reply_to=None, link_preview=True):
        self.sent.append((chat_id, message, file, reply_to))
        return SimpleNamespace(id=100 + len(self.sent))

    async def get_messages(self, chat_id, ids=None):
        return SimpleNamespace(media=SimpleNamespace(results=self.results))

    async def edit_message(self, chat_id, msg_id, file=None):
        self.edited.append((chat_id, msg_id, file))
        return SimpleNamespace(media=SimpleNamespace(results=self.results))


def make(tmp_path, results=None):
    store = Store(tmp_path / "rules.json")
    store.set_memes(5, True, "Мемы")
    journal = Journal(tmp_path / "assistant.db")
    client = FakeClient(results)
    return MemePolls(client, store, journal, 6), client, journal


def results(**votes):
    return types.PollResults(results=[
        types.PollAnswerVoters(option=bytes([int(score[1:])]), voters=n) for score, n in votes.items()
    ])


def test_what_counts_as_a_meme():
    assert is_meme(message())
    assert is_meme(message(photo=False, gif=True, video=True))
    assert is_meme(message(photo=False, video=True))
    assert not is_meme(message(photo=False))
    assert not is_meme(message(photo=False, video=True, video_note=True))
    assert not is_meme(message(photo=False, video=True, sticker=True))


def test_every_meme_gets_a_poll_from_1_to_10(tmp_path):
    memes, client, journal = make(tmp_path)
    asyncio.run(memes.on_message(message(msg_id=7)))
    asyncio.run(memes.on_message(message(msg_id=8)))
    assert len(client.sent) == 2
    chat_id, _, file, reply_to = client.sent[0]
    assert (chat_id, reply_to) == (5, 7)
    assert [a.text.text for a in file.poll.answers] == [str(n) for n in range(1, 11)]
    assert file.poll.public_voters and not file.poll.closed
    # In the journal, so a restart doesn't lose it.
    reopened = Journal(tmp_path / "assistant.db")
    assert [row[:2] for row in reopened.meme_polls_due(float("inf"))] == [(5, 101), (5, 102)]


def test_other_chats_and_text_get_nothing(tmp_path):
    memes, client, _ = make(tmp_path)
    asyncio.run(memes.on_message(message(chat_id=6)))
    asyncio.run(memes.on_message(message(photo=False)))
    assert client.sent == []


def test_an_album_is_one_meme(tmp_path):
    memes, client, _ = make(tmp_path)
    for i in range(3):
        asyncio.run(memes.on_message(message(msg_id=i, grouped_id=42)))
    asyncio.run(memes.on_message(message(msg_id=10, grouped_id=43)))
    assert len(client.sent) == 2


def test_my_own_polls_are_not_me_writing(tmp_path):
    memes, _, _ = make(tmp_path)
    asyncio.run(memes.on_message(message()))
    assert memes.is_own(SimpleNamespace(chat_id=5, id=101))
    assert not memes.is_own(SimpleNamespace(chat_id=5, id=7))


def test_a_due_poll_closes_with_the_average(tmp_path):
    memes, client, journal = make(tmp_path, results=results(s10=2, s7=1, s1=0))
    asyncio.run(memes.on_message(message()))
    asyncio.run(memes.close_due(now=0))
    assert client.edited == []  # not yet
    asyncio.run(memes.close_due(now=float("inf")))
    [(chat_id, msg_id, file)] = client.edited
    assert (chat_id, msg_id) == (5, 101) and file.poll.closed
    assert client.sent[-1][1:] == ("🏆 Мем набрал 9.0/10 (3 голоса)", None, 101)
    assert journal.meme_polls_due(float("inf")) == []
    assert journal.best_memes(5, 0) == [(1, "Вася", 9.0, 3)]


def test_nobody_voted_no_score(tmp_path):
    memes, client, journal = make(tmp_path, results=results(s5=0))
    asyncio.run(memes.on_message(message()))
    asyncio.run(memes.close_due(now=float("inf")))
    assert len(client.sent) == 1
    assert journal.meme_polls_due(float("inf")) == [] and journal.best_memes(5, 0) == []


def test_top_of_the_week(tmp_path):
    journal = Journal(tmp_path / "assistant.db")
    now = 30 * 86400
    for msg_id, (author_id, author, score, voters, age) in enumerate([
        (1, "Вася", 9.5, 4, 1), (1, "Вася", 6.0, 3, 2), (2, "Петя", 8.0, 5, 3),
        (3, "Оля", 10.0, 1, 10),  # older than a week
        (3, "Оля", 7.0, 2, 1),
    ]):
        ts = now - age * 86400
        journal.add_meme_poll(-1001234, msg_id + 100, 0, msg_id, author_id, author, ts, ts=ts)
        journal.close_meme_poll(-1001234, msg_id + 100, score, voters)
    assert format_top(journal, -1001234, now) == "\n".join([
        "🏆 Лучшие мемы недели",
        "1. 9.5/10 — Вася (4 голоса) https://t.me/c/1234/0",
        "2. 8.0/10 — Петя (5 голосов) https://t.me/c/1234/2",
        "3. 7.0/10 — Оля (2 голоса) https://t.me/c/1234/4",
        "",
        "👑 Лучшие авторы",
        "1. Петя — 8.0 в среднем, 1 мем",
        "2. Вася — 7.8 в среднем, 2 мема",
        "3. Оля — 7.0 в среднем, 1 мем",
    ])
    assert format_top(journal, 5, now) == "За неделю ещё ни один мем не получил оценок"


def test_average_and_words():
    assert average(None) is None
    assert average(results(s3=1, s4=1)) == (3.5, 2)
    assert [plural(n, "голос", "голоса", "голосов") for n in (1, 2, 5, 11, 21, 22, 112)] == [
        "голос", "голоса", "голосов", "голосов", "голос", "голоса", "голосов"]
    assert summary(4.2, 5) == "🥱 Мем набрал 4.2/10 (5 голосов)"
    assert link(5, 3) == ""


def test_memes_command(tmp_path):
    store = Store(tmp_path / "rules.json")
    run = lambda text, chat_id=5: commands.execute(store, commands.parse(text), chat_id, "Мемы")
    assert run(".memes").text == "Оцениваем мемы для Мемы"
    assert run(".memes on").text == "Мемы для Мемы уже оцениваются"
    assert "Оцениваем мемы: Мемы #5" in run(".rules").text
    assert run(".memes off").text == "Больше не оцениваем мемы для Мемы"
    assert run(".memes", chat_id=None).is_error
    assert run(".memes лол").is_error
    assert Store(store.path).memes == {}


def test_weekly_top_on_sunday_afternoon_once(tmp_path):
    import datetime

    memes, client, journal = make(tmp_path)
    memes.top_hour = 14
    sunday = datetime.datetime(2026, 11, 8, 14, 5, tzinfo=datetime.UTC)
    journal.add_meme_poll(5, 200, 0, 9, 11, "Вася", 0, ts=sunday.timestamp() - 3600)
    journal.close_meme_poll(5, 200, 8.5, 2)
    memes.store.set_memes(6, True, "Тихий чат")  # no scores this week: no top there

    asyncio.run(memes.weekly_top(sunday.replace(hour=13)))  # too early
    asyncio.run(memes.weekly_top(sunday - datetime.timedelta(days=1)))  # Saturday
    assert client.sent == []
    asyncio.run(memes.weekly_top(sunday))
    asyncio.run(memes.weekly_top(sunday.replace(hour=20)))  # already sent
    [(chat_id, text, _, _)] = client.sent
    assert chat_id == 5 and text.startswith("🏆 Лучшие мемы недели\n1. 8.5/10 — Вася")
    asyncio.run(memes.weekly_top(sunday + datetime.timedelta(days=7)))  # next week, no new scores
    assert len(client.sent) == 1
