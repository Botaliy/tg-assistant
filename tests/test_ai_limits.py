import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from bot.ai import AiResponder
from bot.journal import Journal, Usage
from bot.store import Store

STRANGER, FRIEND = 1, 2


class FakeClient:
    def __init__(self):
        self.sent = []

    async def iter_messages(self, chat_id, **kwargs):
        yield SimpleNamespace(id=10, out=False, raw_text="привет")

    def action(self, chat_id, kind):
        return contextlib.nullcontext()

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(chat_id)
        return SimpleNamespace(id=100 + len(self.sent))


def make(tmp_path, known_unlimited):
    store = Store(tmp_path / "rules.json")
    store.ai_enabled = True
    store.set_ai(STRANGER, True, "@stranger", auto=True)
    store.set_ai(FRIEND, True, "@friend")
    client = FakeClient()

    async def notify(text):
        pass

    ai = AiResponder(
        client, store, api=None, model="m", effort="low", per_chat_limit=1, total_limit=2,
        stranger_limit=100, me_name="Я", notify=notify, journal=Journal(tmp_path / "assistant.db"),
        known_unlimited=known_unlimited,
    )

    async def ask(system, messages):
        return "ага", Usage(input=1000, output=10)

    ai._ask = ask
    ai._schedule_report = lambda chat_id: None
    return ai, client


def answer(ai, chat_id, times):
    async def go():
        for _ in range(times):
            await ai._reply(chat_id, None, None)
    asyncio.run(go())


def test_friend_skips_daily_limits(tmp_path):
    ai, client = make(tmp_path, known_unlimited=True)
    answer(ai, FRIEND, 5)
    assert client.sent.count(FRIEND) == 5
    # The friend didn't use up the total: the stranger still gets their one reply a day.
    answer(ai, STRANGER, 3)
    assert client.sent.count(STRANGER) == 1


def test_limits_back_when_switched_off(tmp_path):
    ai, client = make(tmp_path, known_unlimited=False)
    answer(ai, FRIEND, 5)
    assert client.sent.count(FRIEND) == 1


def test_daily_counters_survive_a_restart(tmp_path):
    ai, client = make(tmp_path, known_unlimited=False)
    answer(ai, FRIEND, 1)
    ai, client = make(tmp_path, known_unlimited=False)  # the same database
    answer(ai, FRIEND, 1)
    assert client.sent == []
    ai.reset_limit(FRIEND)
    answer(ai, FRIEND, 1)
    assert client.sent == [FRIEND]


def test_spend_is_written_down(tmp_path):
    ai, _ = make(tmp_path, known_unlimited=True)
    ai.model = "claude-haiku-5-5"
    answer(ai, FRIEND, 2)
    [(model, requests, tokens_in, tokens_out, cost)] = ai.journal.spend("2000-01-01")
    assert (model, requests, tokens_in, tokens_out) == ("claude-haiku-5-5", 2, 2000, 20)
    assert cost == pytest.approx(2 * (1000 * 0.10 + 10 * 0.50) / 1_000_000)


class FakeDeepSeek:
    def __init__(self, answer):
        self.answer = answer
        self.systems = []

    model = "deepseek-flash"

    async def ask(self, system, messages):
        self.systems.append(system)
        return self.answer, Usage(input=1000, output=10)

    def cost(self, usage):
        return 0.0


def deepseek_chat(tmp_path, deepseek):
    ai, client = make(tmp_path, known_unlimited=True)
    ai.deepseek = deepseek
    ai.store.set_model(FRIEND, "deepseek")
    notes = []

    async def notify(text):
        notes.append(text)

    ai.notify = notify
    return ai, client, notes


def test_deepseek_chat_goes_to_deepseek_without_the_delicate_topics_rule(tmp_path):
    deepseek = FakeDeepSeek("сам такой")
    ai, client, _ = deepseek_chat(tmp_path, deepseek)
    answer(ai, FRIEND, 1)
    assert client.sent == [FRIEND]
    [system] = deepseek.systems
    assert "деликатн" not in system and "[skip]" in system
    # Other chats stay on Claude, with the rule.
    answer(ai, STRANGER, 1)
    assert len(deepseek.systems) == 1


def test_deepseek_refusal_is_reported_as_such(tmp_path):
    ai, client, notes = deepseek_chat(tmp_path, FakeDeepSeek(None))
    answer(ai, FRIEND, 1)
    assert client.sent == []
    assert notes and "DeepSeek отказался" in notes[0]


def test_deepseek_chat_without_a_key_stays_silent(tmp_path):
    ai, client, notes = deepseek_chat(tmp_path, None)
    answer(ai, FRIEND, 1)
    assert client.sent == []
    assert "DEEPSEEK_API_KEY" in notes[0]
