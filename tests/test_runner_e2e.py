import json
from pathlib import Path

import httpx
import pytest
import respx

from macropulse.alerter import DiscordAlerter
from macropulse.config import Settings
from macropulse.dedup import SeenStore
from macropulse.providers.replay import ReplayProvider
from macropulse.runner import run_once

WEBHOOK = "https://discord.com/api/webhooks/111/abc"
FX = Path(__file__).parent / "fixtures" / "april_23_2026_post.json"


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setenv("DEDUP_DB_PATH", str(tmp_path / "seen.db"))
    monkeypatch.setenv("NITTER_HANDLES", "[]")
    monkeypatch.setenv("TRUTHSOCIAL_HANDLES", "[]")
    return Settings()


@pytest.mark.asyncio
@respx.mock
async def test_april_23_reference(tmp_path, settings):
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(204))
    provider = ReplayProvider(FX)
    seen = SeenStore(tmp_path / "seen.db")
    async with DiscordAlerter(webhook_url=WEBHOOK, timeout=5.0) as alerter:
        sent_first = await run_once([provider], alerter, seen, settings)
        sent_second = await run_once([provider], alerter, seen, settings)

    assert sent_first == 1
    assert sent_second == 0
    assert route.call_count == 1

    payload = json.loads(route.calls.last.request.content.decode())
    embed = payload["embeds"][0]
    assert "shoot and kill" in embed["title"]
    sentiment_field = next(f for f in embed["fields"] if f["name"] == "Sentiment")
    assert float(sentiment_field["value"]) < -0.6


@pytest.mark.asyncio
@respx.mock
async def test_discord_failure_does_not_kill_loop(tmp_path, settings):
    """Acceptance criterion: a Discord outage must not raise out of run_once.

    The post is left unmarked in dedup so the next tick can retry.
    """
    respx.post(WEBHOOK).mock(return_value=httpx.Response(500))
    provider = ReplayProvider(FX)
    seen = SeenStore(tmp_path / "seen.db")
    async with DiscordAlerter(
        webhook_url=WEBHOOK,
        timeout=5.0,
        max_attempts=2,
        backoff_min=0,
        backoff_max=0,
    ) as alerter:
        sent = await run_once([provider], alerter, seen, settings)

    assert sent == 0
    assert seen.has_seen("truthsocial:replay-april-23") is False
