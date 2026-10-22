import asyncio
import json

import httpx
import pytest

from bot.deepseek import DeepSeek, DeepSeekError
from bot.journal import Usage


def client(handler):
    deepseek = DeepSeek("key", "deepseek-chat")
    deepseek._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=deepseek._http.headers)
    return deepseek


def ask(deepseek):
    return asyncio.run(deepseek.ask("будь дерзким", [{"role": "user", "content": "привет"}]))


def test_sends_system_first_and_returns_the_text():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "сам привет"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "prompt_cache_hit_tokens": 100, "prompt_cache_miss_tokens": 20,
                      "completion_tokens": 7},
        })

    assert ask(client(handler)) == ("сам привет", Usage(input=20, output=7, cache_read=100))
    assert seen["auth"] == "Bearer key"
    assert seen["body"]["model"] == "deepseek-chat"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "будь дерзким"}
    assert "reasoning_effort" not in seen["body"]


def test_passes_effort_when_set():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ок"}, "finish_reason": "stop"}]})

    deepseek = client(handler)
    deepseek.effort = "high"
    ask(deepseek)
    assert seen["body"]["reasoning_effort"] == "high"


def test_content_filter_is_a_refusal():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]})

    assert ask(client(handler))[0] is None


def test_cost_from_configured_prices():
    deepseek = DeepSeek("key", "deepseek-flash", prices=(0.3, 0.03, 0.5))
    assert deepseek.cost(Usage(input=1_000_000, cache_read=1_000_000, output=2_000_000)) == pytest.approx(1.33)


def test_errors_become_deepseek_errors():
    def handler(request):
        return httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})

    with pytest.raises(DeepSeekError, match="402"):
        ask(client(handler))
