import json
from pathlib import Path

import httpx
import pytest
import respx

from macropulse.providers.truthsocial import TruthSocialProvider

FX = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
@respx.mock
async def test_strips_html_and_prefixes_id():
    respx.get("https://truthsocial.com/api/v1/accounts/lookup").mock(
        return_value=httpx.Response(
            200, json=json.loads((FX / "truthsocial_account_lookup.json").read_text())
        )
    )
    respx.get("https://truthsocial.com/api/v1/accounts/12345/statuses").mock(
        return_value=httpx.Response(
            200, json=json.loads((FX / "truthsocial_statuses.json").read_text())
        )
    )
    async with TruthSocialProvider(handles=["realDonaldTrump"], timeout=5.0) as p:
        posts = await p.fetch()
    assert len(posts) == 1
    assert posts[0].id == "truthsocial:987654321"
    assert posts[0].content == "hello world"
    assert posts[0].author == "realDonaldTrump"


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_5xx():
    lookup = respx.get("https://truthsocial.com/api/v1/accounts/lookup").mock(
        return_value=httpx.Response(
            200, json=json.loads((FX / "truthsocial_account_lookup.json").read_text())
        )
    )
    statuses = respx.get("https://truthsocial.com/api/v1/accounts/12345/statuses").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                200, json=json.loads((FX / "truthsocial_statuses.json").read_text())
            ),
        ]
    )
    # short backoff override for test speed
    async with TruthSocialProvider(
        handles=["realDonaldTrump"], timeout=5.0, max_attempts=3, backoff_min=0, backoff_max=0
    ) as p:
        posts = await p.fetch()
    assert len(posts) == 1
    assert lookup.called
    assert statuses.call_count == 2
