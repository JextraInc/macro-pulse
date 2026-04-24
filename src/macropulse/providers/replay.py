import json
from pathlib import Path

from macropulse.models import Post
from macropulse.providers.base import FeedProvider


class ReplayProvider(FeedProvider):
    """One-shot provider that replays posts from a JSON fixture file."""

    name = "replay"

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._emitted = False

    async def fetch(self) -> list[Post]:
        if self._emitted:
            return []
        raw = json.loads(self._path.read_text())
        self._emitted = True
        return [Post.model_validate(item) for item in raw]
