"""DeepSeek as the AI for chosen chats (".model deepseek"), e.g. banter with a
friend that Claude keeps too polite.

Its API speaks the OpenAI chat format, so plain httpx is enough, no SDK.
Everything else — context, limits, the link filter, plain-text sending —
stays in bot.ai and works the same for both models.
"""

import httpx

from bot.journal import Usage

URL = "https://api.deepseek.com/chat/completions"


class DeepSeekError(Exception):
    """A failed request: network, auth, balance, rate limit."""


class DeepSeek:
    def __init__(
        self, api_key: str, model: str, effort: str = "",
        prices: tuple[float, float, float] = (0, 0, 0),
    ):
        self.model = model
        self.effort = effort
        # $ per 1M tokens: input, input from DeepSeek's cache, output.
        self.prices = prices
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=httpx.Timeout(60, connect=10),
        )

    def cost(self, usage: Usage) -> float:
        price_in, price_hit, price_out = self.prices
        return (usage.input * price_in + usage.cache_read * price_hit + usage.output * price_out) / 1_000_000

    async def ask(self, system: str, messages: list[dict]) -> tuple[str | None, Usage]:
        """The reply text (None when DeepSeek's own filter declined) and the
        tokens it took. DeepSeek caches repeated prefixes by itself."""
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            # Thinking counts towards it too: room for that plus a short reply.
            "max_tokens": 4000,
            # DeepSeek's suggestion for conversation: livelier than the default.
            "temperature": 1.3,
        }
        if self.effort:
            body["reasoning_effort"] = self.effort
        try:
            response = await self._http.post(URL, json=body)
        except httpx.HTTPError as e:
            raise DeepSeekError(str(e) or type(e).__name__) from e
        if response.status_code != 200:
            raise DeepSeekError(f"HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        tokens = data.get("usage") or {}
        usage = Usage(
            input=tokens.get("prompt_cache_miss_tokens", tokens.get("prompt_tokens", 0)),
            output=tokens.get("completion_tokens", 0),
            cache_read=tokens.get("prompt_cache_hit_tokens", 0),
        )
        choice = data["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            return None, usage
        return choice["message"].get("content") or "", usage
