from typing import Protocol, runtime_checkable

from macropulse.models import Post


@runtime_checkable
class FeedProvider(Protocol):
    name: str

    async def fetch(self) -> list[Post]: ...
