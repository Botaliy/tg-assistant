"""DeepSeek as the AI for chosen chats (".model deepseek"), e.g. banter with a
friend that Claude keeps too polite.

Its API speaks the OpenAI chat format, so plain httpx is enough, no SDK.
Everything else — context, limits, the link filter, plain-text sending —
stays in bot.ai and works the same for both models.
"""

import httpx

URL = "https://api.deepseek.com/chat/completions"


class DeepSeekError(Exception):
    """A failed request: network, auth, balance, rate limit."""


class DeepSeek:
    def __init__(self, api_key: str, model: str):
        self.model = model
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=httpx.Timeout(60, connect=10),
        )

    async def ask(self, system: str, messages: list[dict]) -> str | None:
        """The reply text; None when DeepSeek's own filter declined."""
        try:
            response = await self._http.post(URL, json={
                "model": self.model,
                "messages": [{"role": "system", "content": system}, *messages],
                "max_tokens": 400,
                # DeepSeek's suggestion for conversation: livelier than the default.
                "temperature": 1.3,
            })
        except httpx.HTTPError as e:
            raise DeepSeekError(str(e) or type(e).__name__) from e
        if response.status_code != 200:
            raise DeepSeekError(f"HTTP {response.status_code}: {response.text[:200]}")
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            return None
        return choice["message"].get("content") or ""
