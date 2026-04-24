from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Self

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


class _Transient(Exception):
    pass


class NitterProvider(FeedProvider):
    name = "nitter"

    def __init__(
        self,
        instances: list[str],
        handles: list[str],
        timeout: float = 15.0,
        user_agent: str = "MacroPulseSPX/1.0",
        max_attempts: int = 2,
        backoff_min: float = 0,
        backoff_max: float = 2,
    ) -> None:
        self._instances = instances
        self._handles = handles
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/rss+xml, text/xml, */*"},
        )
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
            xml = await self._fetch_for_handle(handle)
            if xml is None:
                continue
            posts.extend(self._parse(handle, xml))
        return posts

    async def _fetch_for_handle(self, handle: str) -> str | None:
        for instance in self._instances:
            url = f"{instance.rstrip('/')}/{handle}/rss"
            try:
                text = await self._get_with_retry(url)
                log.info("nitter.instance_ok", instance=instance, handle=handle)
                return text
            except Exception as exc:
                log.warning(
                    "nitter.instance_failed", instance=instance, handle=handle, error=str(exc)
                )
                continue
        log.warning("nitter.all_instances_failed", handle=handle)
        return None

    async def _get_with_retry(self, url: str) -> str:
        try:
            async for attempt in AsyncRetrying(
                reraise=True,
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential(multiplier=1, min=self._backoff_min, max=self._backoff_max),
                retry=retry_if_exception_type((_Transient, httpx.TransportError)),
            ):
                with attempt:
                    resp = await self._client.get(url)
                    if resp.status_code == 429 or 500 <= resp.status_code < 600:
                        raise _Transient(f"{resp.status_code}")
                    resp.raise_for_status()
                    return resp.text
        except RetryError as e:
            inner = e.last_attempt.exception()
            if inner is not None:
                raise inner from e
            raise
        raise RuntimeError("unreachable")

    @staticmethod
    def _parse(handle: str, xml: str) -> list[Post]:
        root = ET.fromstring(xml)
        items = root.findall(".//item")
        posts: list[Post] = []
        for item in items:
            guid = (item.findtext("guid") or item.findtext("link") or "").strip()
            link = (item.findtext("link") or guid).strip()
            desc_raw = item.findtext("description") or ""
            pub = item.findtext("pubDate") or ""
            try:
                created = parsedate_to_datetime(pub)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                continue
            text = HTMLParser(desc_raw).text(separator="")
            content = " ".join(text.split())
            posts.append(
                Post(
                    id=f"nitter:{guid}",
                    author=handle,
                    source="nitter",
                    content=content,
                    url=link,
                    created_at=created,
                )
            )
        return posts
