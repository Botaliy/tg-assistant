import asyncio
import contextlib
from types import SimpleNamespace

from bot.ai import AiResponder
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
        stranger_limit=100, me_name="Я", notify=notify, known_unlimited=known_unlimited,
    )

    async def ask(system, messages):
        return "ага"

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
