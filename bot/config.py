from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8"
    )

    # From https://my.telegram.org -> API development tools.
    api_id: int
    api_hash: str
    # The panel bot with buttons, from @BotFather. It only listens to the
    # account the userbot is logged into.
    bot_token: str

    # Holds the login session (full access to the account — keep it private)
    # and the rules file.
    data_dir: str = "data"

    # The same auto-reply goes to the same chat at most once per this many
    # seconds, so a chatty interlocutor doesn't get the canned answer 10 times.
    reply_cooldown_seconds: int = 600

    # AI chats (see bot.ai): Claude answers for me. Empty key = turned off.
    anthropic_api_key: str = ""
    ai_model: str = "claude-haiku-5-5"
    # low | medium | high | xhigh | max. Chat replies don't need deep thinking.
    ai_effort: str = "low"
    # Spend caps: replies per chat and in total per day.
    ai_daily_per_chat: int = 20
    ai_daily_total: int = 200
    # A stranger gets at most this many AI replies, ever; then the AI hands
    # the chat over to me with a report.
    ai_stranger_limit: int = 10
    # Private chats with people I know (not strangers picked up on their own)
    # skip both daily caps. Set to false to put the caps back.
    ai_known_unlimited: bool = True
    # DeepSeek for chats switched to it (".model deepseek"), from
    # https://platform.deepseek.com. Empty key = only Claude.
    deepseek_api_key: str = ""
    # "deepseek-chat" is an old alias for deepseek-flash with thinking off,
    # the weakest option.
    deepseek_model: str = "deepseek-flash"
    # low | high | max; empty = the model's default (high). Short chat
    # replies don't need it to think long.
    deepseek_effort: str = "low"
    # DeepSeek's prices, $ per 1M tokens, for ".usage": input, input served from
    # its cache, output. 0 = not counted (tokens are still shown).
    deepseek_price_input: float = 0.0
    deepseek_price_cache_hit: float = 0.0
    deepseek_price_output: float = 0.0

    @property
    def data_path(self) -> Path:
        path = (BASE_DIR / self.data_dir).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def session_path(self) -> Path:
        # Telethon appends ".session" itself.
        return self.data_path / "assistant"

    @property
    def panel_session_path(self) -> Path:
        return self.data_path / "panel"

    @property
    def rules_path(self) -> Path:
        return self.data_path / "rules.json"

    @property
    def journal_path(self) -> Path:
        return self.data_path / "assistant.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
