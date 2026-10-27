import asyncio
import json

import anthropic
import httpx2

from bot.insults import InsultFilter
from bot.journal import Journal


def make(tmp_path, answer: str = "yes", stop_reason: str = "end_turn", status: int = 200, limit: int = 100):
    seen = []
    notes = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx2.Response(status, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": answer}], "stop_reason": stop_reason,
            "usage": {"input_tokens": 200, "output_tokens": 3},
        })

    api = anthropic.AsyncAnthropic(
        api_key="key", max_retries=0, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    journal = Journal(tmp_path / "assistant.db")
    async def notify(text):
        notes.append(text)

    insults = InsultFilter(api, "claude-haiku-5-5", journal, limit, notify)
    insults.notes = notes
    return insults, seen, journal


def check(insults, text):
    return asyncio.run(insults.is_insult(5, text))


def test_asks_about_one_message_and_counts_the_cost(tmp_path):
    insults, seen, journal = make(tmp_path, "Yes")
    assert check(insults, "ты идиот")
    assert seen[0]["model"] == "claude-haiku-5-5"
    assert seen[0]["messages"] == [{"role": "user", "content": "<message>\nты идиот\n</message>"}]
    [(chat_id, requests, cost)] = journal.top_chats("2000-01-01")
    assert (chat_id, requests) == (5, 1) and cost > 0


def test_no_means_keep(tmp_path):
    insults, _, _ = make(tmp_path, "no")
    assert not check(insults, "блин, ну и погода")


def test_short_messages_skip_the_model(tmp_path):
    insults, seen, _ = make(tmp_path)
    assert not check(insults, "ок")
    assert not check(insults, "👍👍?")
    assert seen == []


def test_errors_and_refusals_keep_the_message(tmp_path):
    insults, _, _ = make(tmp_path, stop_reason="refusal")
    assert not check(insults, "ты идиот")
    insults, _, _ = make(tmp_path, status=500)
    assert not check(insults, "ты идиот")


def test_daily_limit_per_chat(tmp_path):
    insults, seen, _ = make(tmp_path, limit=2)
    assert check(insults, "ты идиот")
    assert check(insults, "ты идиот")
    assert not check(insults, "ты идиот")  # over the limit: not asked
    assert len(seen) == 2
    assert len(insults.notes) == 1
    assert asyncio.run(insults.is_insult(6, "ты идиот"))  # another chat has its own
