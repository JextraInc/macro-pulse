from pathlib import Path

import pytest

from macropulse.providers.replay import ReplayProvider


@pytest.mark.asyncio
async def test_replay_returns_posts_once():
    fx = Path(__file__).parent / "fixtures" / "april_23_2026_post.json"
    provider = ReplayProvider(fx)
    first = await provider.fetch()
    second = await provider.fetch()
    assert len(first) == 1
    assert first[0].id == "truthsocial:replay-april-23"
    assert second == []
