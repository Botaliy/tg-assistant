import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from bot import commands
from bot.journal import Usage
from bot.store import Store
from tests.test_ai_limits import FRIEND, STRANGER, make


def drafting(tmp_path):
    ai, client = make(tmp_path, known_unlimited=True)
    shown, systems, reads, typing = [], [], [], []

    async def show_draft(draft_id, warning):
        shown.append((draft_id, warning))

    async def ask(system, messages):
        systems.append(system)
        return f"вариант {len(systems)}", Usage()

    iter_messages = client.iter_messages

    def iter_and_count(chat_id, **kwargs):
        reads.append(kwargs.get("limit"))
        return iter_messages(chat_id, **kwargs)

    def action(chat_id, kind):
        typing.append(chat_id)
        return contextlib.nullcontext()

    ai.show_draft, ai._ask = show_draft, ask
    client.iter_messages, client.action = iter_and_count, action
    return ai, client, SimpleNamespace(shown=shown, systems=systems, reads=reads, typing=typing)


def test_a_drafts_chat_brings_drafts_instead_of_sending(tmp_path):
    ai, client, seen = drafting(tmp_path)
    ai.store.set_drafts(FRIEND, True)
    asyncio.run(ai._reply(FRIEND, None, None))
    assert client.sent == [] and seen.typing == []
    [(draft_id, warning)] = seen.shown
    assert warning is None
    assert ai.journal.get_draft(draft_id).text == "вариант 1"

    assert asyncio.run(ai.send_draft(draft_id)) == "Отправлено @friend"
    assert client.sent == [FRIEND]
    assert ai.store.ai_chats[FRIEND].replies == 1
    assert ai.journal.get_draft(draft_id) is None


def test_draft_on_request_reads_n_messages_and_redo_replaces_it(tmp_path):
    ai, client, seen = drafting(tmp_path)
    asyncio.run(ai.draft_now(7, "@someone", context=30))
    assert seen.reads == [40]  # N plus room for my own messages after theirs
    [(draft_id, _)] = seen.shown
    assert ai.journal.get_draft(draft_id).context == 30

    asyncio.run(ai.redraft(draft_id))
    assert "«вариант 1»" in seen.systems[1]
    assert seen.shown[1][0] == draft_id
    assert ai.journal.get_draft(draft_id).text == "вариант 2"
    assert client.sent == []


def test_edited_draft_goes_out_as_typed(tmp_path):
    ai, client, seen = drafting(tmp_path)
    sent_texts = []
    send = client.send_message

    async def send_message(chat_id, text, **kwargs):
        sent_texts.append(text)
        return await send(chat_id, text, **kwargs)

    client.send_message = send_message
    asyncio.run(ai.draft_now(FRIEND, "@friend"))
    asyncio.run(ai.send_draft(seen.shown[0][0], "мой текст, site.com"))
    assert sent_texts == ["мой текст, site.com"]


def test_a_link_in_a_draft_is_only_a_warning(tmp_path):
    ai, client, seen = drafting(tmp_path)

    async def ask(system, messages):
        return "глянь site.com", Usage()

    ai._ask = ask
    asyncio.run(ai.draft_now(FRIEND, "@friend"))
    assert seen.shown[0][1] == "со ссылкой или контактом"


def voice(message_id=11):
    return SimpleNamespace(id=message_id, voice=object(), video_note=None, video=None, gif=None, sticker=None)


def test_voice_gets_a_canned_answer_once_and_no_model(tmp_path):
    ai, client, seen = drafting(tmp_path)
    ai.media_reply = "напиши текстом"
    ai.store.ai_enabled = True
    sender = SimpleNamespace(id=FRIEND)

    async def go():
        assert await ai.on_incoming(FRIEND, sender, None, voice())
        assert await ai.on_incoming(FRIEND, sender, None, voice(12))
        ai._cancel_report(FRIEND)

    asyncio.run(go())
    assert client.sent == [FRIEND]
    assert seen.systems == [] and ai._pending == {}


def test_voice_in_a_drafts_chat_gets_nothing(tmp_path):
    ai, client, _ = drafting(tmp_path)
    ai.media_reply = "напиши текстом"
    ai.store.set_drafts(STRANGER, True)

    async def go():
        await ai.on_incoming(STRANGER, SimpleNamespace(id=STRANGER), None, voice())
        ai._cancel_report(STRANGER)

    asyncio.run(go())
    assert client.sent == []


def test_gif_is_not_a_video_to_answer():
    from bot.ai import is_voice_or_video

    gif = SimpleNamespace(voice=None, video_note=None, video=object(), gif=object(), sticker=None)
    video = SimpleNamespace(voice=None, video_note=None, video=object(), gif=None, sticker=None)
    assert not is_voice_or_video(gif) and is_voice_or_video(video)


def test_ai_draft_command_switches_the_mode(tmp_path):
    store = Store(tmp_path / "rules.json")
    store.ai_enabled = True
    answer = commands.execute(store, commands.parse(".ai draft"), 5, "@vasya")
    assert answer.text == "ИИ присылает черновики для @vasya"
    assert store.ai_chats[5].drafts
    commands.execute(store, commands.parse(".ai"), 5, "@vasya")
    assert not store.ai_chats[5].drafts
    assert not Store(tmp_path / "rules.json").ai_chats[5].drafts


def test_draft_context():
    assert commands.parse_draft_context("") is None
    assert commands.parse_draft_context("30") == 30
    assert commands.parse_draft_context("500") == commands.MAX_DRAFT_CONTEXT
    with pytest.raises(ValueError):
        commands.parse_draft_context("abc")
