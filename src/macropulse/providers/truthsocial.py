from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Any, Self

import httpx
from selectolax.parser import HTMLParser
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from macropulse.logging import get_logger
from macropulse.models import Post
from macropulse.providers.base import FeedProvider

log = get_logger(__name__)

_BASE = "https://truthsocial.com"


class _Transient(Exception):
    pass


class TruthSocialProvider(FeedProvider):
    name = "truthsocial"

    def __init__(
        self,
        handles: list[str],
        timeout: float = 15.0,
        user_agent: str = "MacroPulseSPX/1.0",
        max_attempts: int = 5,
        backoff_min: float = 1,
        backoff_max: float = 30,
    ) -> None:
        self._handles = handles
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        self._id_cache: dict[str, str] = {}
        self._max_attempts = max_attempts
        self._backoff_min = backoff_min
        self._backoff_max = backoff_max

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self) -> list[Post]:
        posts: list[Post] = []
        for handle in self._handles:
            try:
                acct_id = await self._resolve_id(handle)
                statuses = await self._statuses(acct_id)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    self._id_cache.pop(handle, None)
                log.warning("truthsocial.fetch_failed", handle=handle, error=str(exc))
                continue
            except Exception as exc:
                log.warning("truthsocial.fetch_failed", handle=handle, error=str(exc))
                continue
            for s in statuses:
                posts.append(self._to_post(handle, s))
        return posts

    async def _resolve_id(self, handle: str) -> str:
        if handle in self._id_cache:
            return self._id_cache[handle]
        data = await self._request(
            "GET", f"{_BASE}/api/v1/accounts/lookup", params={"acct": handle}
        )
        acct_id = str(data["id"])
        self._id_cache[handle] = acct_id
        return acct_id

    async def _statuses(self, acct_id: str) -> list[dict[str, Any]]:
        result = await self._request(
            "GET",
            f"{_BASE}/api/v1/accounts/{acct_id}/statuses",
            params={"limit": 40, "exclude_replies": "false", "exclude_reblogs": "false"},
        )
        return result  # type: ignore[no-any-return]

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            async for attempt in AsyncRetrying(
                reraise=True,
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential(
                    multiplier=1, min=self._backoff_min, max=self._backoff_max
                ),
                retry=retry_if_exception_type((_Transient, httpx.TransportError)),
            ):
                with attempt:
                    resp = await self._client.request(method, url, **kwargs)
                    if resp.status_code == 429 or 500 <= resp.status_code < 600:
                        raise _Transient(f"{resp.status_code} on {url}")
                    resp.raise_for_status()
                    return resp.json()
        except RetryError as e:
            inner = e.last_attempt.exception()
            if inner is not None:
                raise inner from e
            raise
        raise RuntimeError("unreachable")

    @staticmethod
    def _strip_html(html: str) -> str:
        # selectolax with separator=" " can produce double-spaces between inline
        # tags; use no separator and rely on the existing whitespace between
        # text nodes. Collapse any runs of whitespace to a single space.
        text = HTMLParser(html).text(separator="")
        return " ".join(text.split())

    def _to_post(self, handle: str, status: dict[str, Any]) -> Post:
        return Post(
            id=f"truthsocial:{status['id']}",
            author=handle,
            source="truthsocial",
            content=self._strip_html(status.get("content") or ""),
            url=status.get("url", ""),
            created_at=_parse_iso(status["created_at"]),
        )


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
