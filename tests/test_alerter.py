import time
from datetime import UTC, datetime

import httpx
import pytest
import respx

from macropulse.alerter import DiscordAlerter
from macropulse.models import Alert, Post

WEBHOOK = "https://discord.com/api/webhooks/111/abc"


def _alert() -> Alert:
    post = Post(
        id="truthsocial:1",
        author="realDonaldTrump",
        source="truthsocial",
        content="they want to shoot and kill our people, no peace",
        url="https://truthsocial.com/@realDonaldTrump/1",
        created_at=datetime(2026, 4, 23, 17, 0, tzinfo=UTC),
    )
    return Alert(post=post, matched_keyword="shoot and kill", compound_score=-0.81)


@pytest.mark.asyncio
@respx.mock
async def test_embed_shape():
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(204))
    async with DiscordAlerter(webhook_url=WEBHOOK, timeout=5.0) as alerter:
        await alerter.send(_alert())
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "shoot and kill" in body
    import json

    payload = json.loads(body)
    embed = payload["embeds"][0]
    assert "shoot and kill" in embed["title"]
    assert "they want to" in embed["description"]
    assert embed["color"] == 0xE74C3C
    assert embed["url"] == "https://truthsocial.com/@realDonaldTrump/1"
    field_names = {f["name"] for f in embed["fields"]}
    assert {"Sentiment", "Author", "Source", "Posted At"}.issubset(field_names)


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_429():
    route = respx.post(WEBHOOK).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}, json={"message": "rate"}),
            httpx.Response(204),
        ]
    )
    start = time.monotonic()
    async with DiscordAlerter(webhook_url=WEBHOOK, timeout=5.0) as alerter:
        await alerter.send(_alert())
    elapsed = time.monotonic() - start
    assert route.call_count == 2
    assert elapsed >= 1.0


@pytest.mark.asyncio
@respx.mock
async def test_embed_posted_at_is_utc():
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(204))
    async with DiscordAlerter(webhook_url=WEBHOOK, timeout=5.0) as alerter:
        await alerter.send(_alert())
    import json

    payload = json.loads(route.calls.last.request.content.decode())
    posted_at = next(f["value"] for f in payload["embeds"][0]["fields"] if f["name"] == "Posted At")
    assert posted_at.endswith("+00:00") or posted_at.endswith("Z")
