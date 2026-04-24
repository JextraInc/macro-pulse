from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DISCORD_WEBHOOK_URL: SecretStr

    POLL_INTERVAL_SECONDS: int = Field(default=60, ge=5)
    SENTIMENT_THRESHOLD: float = Field(default=-0.6, le=0.0, ge=-1.0)

    BEARISH_KEYWORDS: list[str] = Field(
        default_factory=lambda: [
            "shoot and kill",
            "mining",
            "blockade",
            "seized",
            "devastate",
            "no peace",
            "missile",
            "retaliation",
            "oil embargo",
            "strait closed",
        ]
    )

    TRUTHSOCIAL_HANDLES: list[str] = Field(default_factory=lambda: ["realDonaldTrump"])

    NITTER_INSTANCES: list[str] = Field(
        default_factory=lambda: ["https://nitter.net", "https://nitter.privacydev.net"]
    )
    NITTER_HANDLES: list[str] = Field(default_factory=list)

    DEDUP_DB_PATH: Path = Path("./data/seen.db")
    DEDUP_TTL_DAYS: int = Field(default=7, ge=1)

    LOG_LEVEL: str = "INFO"
    HTTP_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0.0)
    USER_AGENT: str = "MacroPulseSPX/1.0"
