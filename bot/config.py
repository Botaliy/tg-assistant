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


@lru_cache
def get_settings() -> Settings:
    return Settings()
