from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration read from env vars (and an optional .env file).

    Only DISCORD_BOT_TOKEN is strictly required at runtime; every other value
    has a sensible default so the backend can be booted locally without any
    setup.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080

    data_dir: Path = Path("data")
    db_path: Path = Field(default=Path("data/pvpskill.db"))

    discord_bot_token: str = ""
    discord_guild_id: int = 1496516138958590004
    discord_verify_channel_id: int = 1496516140808142852
    discord_invite_url: str = "https://discord.gg/8uNSjtpj5m"

    mctiers_base_url: str = "https://mctiers.com/api/v2"
    mctiers_cache_ttl_seconds: int = 120

    verify_code_ttl_seconds: int = 600  # 10 minutes
    verify_code_length: int = 8

    # Shared secret for HMAC between the Minecraft mod and this backend.
    # Leave blank to disable signature verification (recommended for dev only).
    mod_shared_secret: str = ""

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
