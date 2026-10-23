import asyncio
import time
from types import SimpleNamespace

from bot import commands
from bot.store import Store
from tests.test_ai_limits import FRIEND, STRANGER, make


def mine(chat_id, message_id=20):
    return SimpleNamespace(chat_id=chat_id, id=message_id)


def test_writing_to_a_friend_pauses_the_ai(tmp_path):
    ai, client = make(tmp_path, known_unlimited=True)

    async def go():
        await ai.on_outgoing(mine(FRIEND))
        assert await ai.on_incoming(FRIEND, SimpleNamespace(id=FRIEND))  # keyword replies stay out too
        assert ai._pending == {}
        await ai._reply(FRIEND, None, None)

    asyncio.run(go())
    assert client.sent == []
    assert FRIEND in ai.store.ai_chats
    assert 0 < ai.store.ai_chats[FRIEND].pause_left() <= 10


def test_ai_is_back_once_i_go_quiet(tmp_path):
    ai, client = make(tmp_path, known_unlimited=True)
    asyncio.run(ai.on_outgoing(mine(FRIEND)))
    ai.store.ai_chats[FRIEND].paused_until = time.time() - 1
    asyncio.run(ai._reply(FRIEND, None, None))
    assert client.sent == [FRIEND]


def test_my_message_cancels_the_reply_waiting_its_turn(tmp_path):
    ai, client = make(tmp_path, known_unlimited=True)

    async def go():
        await ai.on_incoming(FRIEND, SimpleNamespace(id=FRIEND))
        pending = ai._pending[FRIEND]
        await ai.on_outgoing(mine(FRIEND))
        await asyncio.sleep(0)
        assert pending.cancelled()

    asyncio.run(go())
    assert client.sent == []


def test_the_ais_own_message_is_no_takeover(tmp_path):
    ai, client = make(tmp_path, known_unlimited=True)
    asyncio.run(ai.send_draft(ai.journal.save_draft(FRIEND, "@friend", "ага", None, 10)))
    sent = SimpleNamespace(chat_id=FRIEND, id=100 + len(client.sent))
    asyncio.run(ai.on_outgoing(sent))
    assert not ai.paused(FRIEND)


def test_writing_to_a_stranger_still_ends_the_ai_there(tmp_path):
    ai, _ = make(tmp_path, known_unlimited=True)
    asyncio.run(ai.on_outgoing(mine(STRANGER)))
    assert STRANGER not in ai.store.ai_chats


def test_pause_survives_a_restart_and_my_report_starts_after_it(tmp_path):
    ai, _ = make(tmp_path, known_unlimited=True)
    asyncio.run(ai.on_outgoing(mine(FRIEND, message_id=42)))
    chat = Store(tmp_path / "rules.json").ai_chats[FRIEND]
    assert chat.pause_left() and chat.reported_up_to == 42


def test_ai_command_ends_the_pause(tmp_path):
    ai, _ = make(tmp_path, known_unlimited=True)
    asyncio.run(ai.on_outgoing(mine(FRIEND)))
    assert "⏸" in commands.execute(ai.store, commands.parse(".rules"), None).text
    answer = commands.execute(ai.store, commands.parse(".ai"), FRIEND, "@friend")
    assert "уже" not in answer.text
    assert not ai.paused(FRIEND)
