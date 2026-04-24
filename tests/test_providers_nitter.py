from pathlib import Path

import httpx
import pytest
import respx

from macropulse.providers.nitter import NitterProvider

FX = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
@respx.mock
async def test_rotates_instances_on_502():
    rss = (FX / "nitter_rss.xml").read_text()
    bad = respx.get("https://nitter.net/realDonaldTrump/rss").mock(
        return_value=httpx.Response(502)
    )
    good = respx.get("https://nitter.privacydev.net/realDonaldTrump/rss").mock(
        return_value=httpx.Response(200, text=rss, headers={"Content-Type": "application/rss+xml"})
    )
    async with NitterProvider(
        instances=["https://nitter.net", "https://nitter.privacydev.net"],
        handles=["realDonaldTrump"],
        timeout=5.0,
        max_attempts=1,  # don't retry within an instance; just rotate
    ) as p:
        posts = await p.fetch()
    assert bad.called
    assert good.called
    assert len(posts) == 1
    assert posts[0].id.startswith("nitter:")
    assert posts[0].author == "realDonaldTrump"
    assert "shoot and kill" in posts[0].content.lower()


@pytest.mark.asyncio
@respx.mock
async def test_all_instances_fail_returns_empty():
    respx.get("https://nitter.net/realDonaldTrump/rss").mock(return_value=httpx.Response(502))
    respx.get("https://nitter.privacydev.net/realDonaldTrump/rss").mock(
        return_value=httpx.Response(503)
    )
    async with NitterProvider(
        instances=["https://nitter.net", "https://nitter.privacydev.net"],
        handles=["realDonaldTrump"],
        timeout=5.0,
        max_attempts=1,
    ) as p:
        posts = await p.fetch()
    assert posts == []
