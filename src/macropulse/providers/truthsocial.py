"""Truth Social provider.

Uses `curl_cffi` (libcurl with browser TLS impersonation) instead of `httpx`.
The `truthsocial.com` edge enforces TLS-fingerprint bot mitigation via
Cloudflare; a vanilla Python TLS handshake gets HTTP 403 ("Sorry, you have
been blocked"). Safari 17's JA3/JA4 fingerprint passes the check, so we
impersonate it for every request to this host.

The Mastodon-compatible JSON endpoints we hit:
  - GET /api/v1/accounts/lookup?acct=<handle>     →  account JSON (id, etc.)
  - GET /api/v1/accounts/<id>/statuses            →  posts list

Tenacity-driven retries on 429 / 5xx / network errors are unchanged.
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Any, Literal, Self, cast

from curl_cffi import requests as cffi_requests
from selectolax.parser import HTMLParser
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from macropulse.logging import get_logger
from macropulse.models import Post
from macropulse.providers.base import FeedProvider

log = get_logger(__name__)

_BASE = "https://truthsocial.com"

# Safari 17 is the curl_cffi profile that currently slips past Cloudflare's
# bot management on truthsocial.com. If a future Cloudflare ruleset starts
# fingerprinting Safari 17 too, rotate this to a newer profile (e.g.
# "chrome131" once curl_cffi adds it, or a future safari profile).
_IMPERSONATE_PROFILE = "safari17_0"


class _Transient(Exception):
    pass


def _should_retry(exc: BaseException) -> bool:
    """Retry only transient errors.

    `_Transient` wraps 429 and 5xx HTTP responses. `RequestsError` covers
    both transport-level failures (DNS, connect, timeout) and HTTP-status
    errors raised by `raise_for_status()`. We retry the former and let the
    latter propagate immediately so a 404 on the lookup endpoint surfaces
    fast (mirrors the original httpx semantics where HTTPStatusError was
    not in the retry whitelist).
    """
    if isinstance(exc, _Transient):
        return True
    if isinstance(exc, cffi_requests.RequestsError):
        return getattr(exc, "response", None) is None
    return False


class TruthSocialProvider(FeedProvider):
    name = "truthsocial"

    def __init__(
        self,
        handles: list[str],
        timeout: float = 15.0,
        user_agent: str | None = None,
        max_attempts: int = 5,
        backoff_min: float = 1,
        backoff_max: float = 30,
        impersonate: str = _IMPERSONATE_PROFILE,
    ) -> None:
        self._handles = handles
        # IMPORTANT: under TLS impersonation, every header curl_cffi sets must
        # match the impersonated browser to slip past Cloudflare's bot
        # mitigation. Overriding User-Agent specifically breaks the match
        # ("Safari TLS handshake but Python UA" is a fingerprint mismatch
        # Cloudflare flags). We therefore IGNORE the user_agent argument
        # under impersonation and let curl_cffi pick the matching UA. The
        # parameter remains for API compatibility but is effectively a no-op.
        del user_agent
        # curl_cffi's AsyncSession is generic in the response model type;
        # we don't constrain it because we only use the default response
        # shape, hence the [Any] annotation.
        self._client: cffi_requests.AsyncSession[Any] = cffi_requests.AsyncSession(
            timeout=timeout,
            impersonate=impersonate,  # type: ignore[arg-type]
            # Accept is safe to override — browsers do that on XHR. Anything
            # else (UA, Accept-Language, Sec-Fetch-*) must come from
            # impersonation.
            headers={"Accept": "application/json"},
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
        # curl_cffi's AsyncSession exposes only `close()` (which is async).
        await self._client.close()

    async def fetch(self) -> list[Post]:
        posts: list[Post] = []
        for handle in self._handles:
            try:
                acct_id = await self._resolve_id(handle)
                statuses = await self._statuses(acct_id)
            except cffi_requests.RequestsError as exc:
                # 404 invalidates the cached account id (handle was renamed
                # or removed). RequestsError carries an HTTP status when
                # raised from raise_for_status().
                status_code = getattr(exc, "code", None)
                if status_code == 404:
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
                # See `_should_retry` for the policy: retry on transport
                # errors and 429/5xx, but not on permanent HTTP errors.
                retry=retry_if_exception(_should_retry),
            ):
                with attempt:
                    # curl_cffi types `method` as a Literal of HTTP verbs.
                    # We accept str at the public boundary and cast here.
                    resp = await self._client.request(
                        cast(Literal["GET", "POST", "PUT", "DELETE", "OPTIONS",
                                     "HEAD", "TRACE", "PATCH", "QUERY"], method),
                        url,
                        **kwargs,
                    )
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
