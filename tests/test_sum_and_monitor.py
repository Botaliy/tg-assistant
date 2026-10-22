import asyncio
import json
import sqlite3
from types import SimpleNamespace

from telethon import types

from bot import monitor
from bot.journal import Journal, Usage
from tests.test_ai_limits import make

GROUP = -100


def summarizing(tmp_path, messages):
    ai, client = make(tmp_path, known_unlimited=True)
    notes, prompts, reads = [], [], []

    async def iter_messages(chat_id, limit=None, min_id=0, **kwargs):
        reads.append((limit, min_id))
        for m in [m for m in reversed(messages) if m.id > min_id][:limit]:
            yield m

    async def ask(system, msgs):
        prompts.append(msgs[0]["content"])
        return "- спорили о пицце", Usage()

    async def notify(text):
        notes.append(text)

    client.iter_messages, ai._ask, ai.notify = iter_messages, ask, notify
    return ai, SimpleNamespace(notes=notes, prompts=prompts, reads=reads)


def msg(id, text, out=False, name="Петя"):
    return SimpleNamespace(id=id, out=out, raw_text=text, sender=types.User(id=7, first_name=name))


def test_sum_reads_whats_unread(tmp_path):
    messages = [msg(1, "старое"), msg(2, "пицца?"), msg(3, "ананасы", out=True)]
    ai, seen = summarizing(tmp_path, messages)

    async def unread(chat_id):
        return 2, 1

    ai._unread = unread
    asyncio.run(ai.summarize(GROUP, "Друзья"))
    assert seen.reads == [(2, 1)]
    assert seen.prompts == ["Петя: пицца?\nЯ: ананасы"]
    assert seen.notes == ["📰 Друзья, непрочитанные (2):\n- спорили о пицце"]
    [(model, requests, *_)] = ai.journal.spend("2000-01-01")
    assert requests == 1


def test_sum_without_unread_takes_the_last_ones(tmp_path):
    ai, seen = summarizing(tmp_path, [msg(1, "привет")])

    async def unread(chat_id):
        return 0, 1

    ai._unread = unread
    asyncio.run(ai.summarize(GROUP, "Друзья"))
    assert seen.reads == [(50, 0)]
    assert "непрочитанных нет" in seen.notes[0]


def test_sum_of_last_n(tmp_path):
    ai, seen = summarizing(tmp_path, [msg(i, f"m{i}") for i in range(1, 6)])
    asyncio.run(ai.summarize(GROUP, "Друзья", count=2))
    assert seen.prompts == ["Петя: m4\nПетя: m5"]


class FakeBot:
    def __init__(self):
        self.files = None

    async def send_file(self, chat_id, files, **kwargs):
        # The copy lives in a temporary directory: read it while it's there.
        self.files = {f.rsplit("/", 1)[-1]: open(f, "rb").read() for f in files}


def test_backup_sends_rules_and_a_copy_of_the_database_but_not_the_session(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"rules": []}))
    (tmp_path / "assistant.session").write_text("secret")
    journal = Journal(tmp_path / "assistant.db")
    journal.count_reply(5)
    bot = FakeBot()
    asyncio.run(monitor.send_backup(bot, 1, journal, rules))
    assert set(bot.files) == {"rules.json", "assistant.db"}
    copy = tmp_path / "copy.db"
    copy.write_bytes(bot.files["assistant.db"])
    assert sqlite3.connect(copy).execute("SELECT count FROM replies").fetchone() == (1,)
    assert journal.get_meta("last_backup")


def test_not_alive_when_disconnected():
    client = SimpleNamespace(is_connected=lambda: False)
    assert not asyncio.run(monitor.alive([client]))
