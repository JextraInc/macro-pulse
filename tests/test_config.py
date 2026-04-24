from pathlib import Path

from macropulse.config import Settings


def test_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
    monkeypatch.setenv("DEDUP_DB_PATH", str(tmp_path / "seen.db"))
    s = Settings()
    assert s.POLL_INTERVAL_SECONDS == 60
    assert s.SENTIMENT_THRESHOLD == -0.6
    assert "shoot and kill" in s.BEARISH_KEYWORDS
    assert "realDonaldTrump" in s.TRUTHSOCIAL_HANDLES
    assert s.DEDUP_TTL_DAYS == 7
    assert s.DEDUP_DB_PATH == Path(tmp_path / "seen.db")  # noqa: SIM300


def test_webhook_required(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
