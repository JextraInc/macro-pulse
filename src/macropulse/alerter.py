from __future__ import annotations

import asyncio
from collections import deque
from types import TracebackType
from typing import Self

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from macropulse.logging import get_logger
from macropulse.models import Alert

log = get_logger(__name__)

_MAX_DESC_LEN = 2000
_RED = 0xE74C3C


class _TransientDiscordError(Exception):
    def __init__(self, status: int, retry_after: float | None = None) -> None:
        super().__init__(f"Discord transient {status}")
        self.status: int = status
        self.retry_after: float | None = retry_after


class DiscordAlerter:
    """Async Discord webhook sender with tenacity retries and a 30 req/min limiter."""

    def __init__(
        self,
        webhook_url: str,
        timeout: float = 15.0,
        user_agent: str = "MacroPulseSPX/1.0",
        max_per_minute: int = 30,
    ) -> None:
        self._webhook_url = webhook_url
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Content-Type": "application/json"},
        )
        self._max_per_minute = max_per_minute
        self._recent: deque[float] = deque()
        self._lock = asyncio.Lock()

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

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            cutoff = now - 60.0
            while self._recent and self._recent[0] < cutoff:
                self._recent.popleft()
            if len(self._recent) >= self._max_per_minute:
                sleep_for = 60.0 - (now - self._recent[0])
                await asyncio.sleep(max(sleep_for, 0.0))
            self._recent.append(asyncio.get_event_loop().time())

    async def send(self, alert: Alert) -> None:
        payload = self._build_payload(alert)
        try:
            async for attempt in AsyncRetrying(
                reraise=True,
                stop=stop_after_attempt(5),
                wait=wait_exponential(multiplier=1, min=1, max=30),
                retry=retry_if_exception_type((_TransientDiscordError, httpx.TransportError)),
            ):
                with attempt:
                    await self._throttle()
                    await self._send_once(payload)
        except RetryError as e:
            inner = e.last_attempt.exception()
            if inner is not None:
                raise inner from e
            raise

    async def _send_once(self, payload: dict[str, object]) -> None:
        resp = await self._client.post(self._webhook_url, json=payload)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "1"))
            log.warning("discord.rate_limited", retry_after=retry_after)
            await asyncio.sleep(retry_after)
            raise _TransientDiscordError(429, retry_after)
        if 500 <= resp.status_code < 600:
            raise _TransientDiscordError(resp.status_code)
        resp.raise_for_status()

    @staticmethod
    def _build_payload(alert: Alert) -> dict[str, object]:
        content = alert.post.content
        description = (
            content if len(content) <= _MAX_DESC_LEN else content[: _MAX_DESC_LEN - 1] + "…"
        )
        return {
            "embeds": [
                {
                    "title": f"🚨 Bearish Trigger: {alert.matched_keyword}",
                    "description": description,
                    "url": alert.post.url,
                    "color": _RED,
                    "timestamp": alert.post.created_at.isoformat(),
                    "fields": [
                        {
                            "name": "Sentiment",
                            "value": f"{alert.compound_score:.3f}",
                            "inline": True,
                        },
                        {"name": "Author", "value": alert.post.author, "inline": True},
                        {"name": "Source", "value": alert.post.source, "inline": True},
                        {
                            "name": "Posted At",
                            "value": alert.post.created_at.astimezone().isoformat(),
                            "inline": False,
                        },
                    ],
                }
            ]
        }
