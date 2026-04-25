"""Tests for TruthSocialProvider.

Mocking strategy: curl_cffi has no respx-equivalent and curl-level mocking
adds little signal here. We stub `self._client.request` with an `AsyncMock`
that returns canned response objects. The retry path and JSON parsing are
both still covered through the `_request()` retry loop.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from macropulse.providers.truthsocial import TruthSocialProvider, _Transient

FX = Path(__file__).parent / "fixtures"


class _FakeResponse:
    """Minimal stand-in for curl_cffi.requests.Response.

    Only implements the surface used by `_request()`: status_code, raise_for_status,
    and json(). Tests pass either a status code (success/error) or a JSON body.
    """

    def __init__(self, status_code: int = 200, body: Any | None = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            from curl_cffi import requests as cffi_requests

            # The provider's _should_retry distinguishes HTTP errors from
            # transport errors by whether `response` is set. A real
            # raise_for_status() always sets it on HTTP errors.
            raise cffi_requests.RequestsError(
                f"{self.status_code} {self}",
                code=self.status_code,
                response=self,
            )


def _account_lookup_body() -> dict[str, Any]:
    return json.loads((FX / "truthsocial_account_lookup.json").read_text())


def _statuses_body() -> list[dict[str, Any]]:
    return json.loads((FX / "truthsocial_statuses.json").read_text())


@pytest.mark.asyncio
async def test_strips_html_and_prefixes_id() -> None:
    """Happy path: account lookup → statuses → Post with stripped HTML."""
    p = TruthSocialProvider(handles=["realDonaldTrump"], timeout=5.0)
    p._client.request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _FakeResponse(200, _account_lookup_body()),
            _FakeResponse(200, _statuses_body()),
        ]
    )
    try:
        posts = await p.fetch()
    finally:
        await p.aclose()

    assert len(posts) == 1
    assert posts[0].id == "truthsocial:987654321"
    assert posts[0].content == "hello world"
    assert posts[0].author == "realDonaldTrump"


@pytest.mark.asyncio
async def test_retries_on_5xx() -> None:
    """503 on the statuses endpoint should retry and ultimately succeed."""
    p = TruthSocialProvider(
        handles=["realDonaldTrump"],
        timeout=5.0,
        max_attempts=3,
        backoff_min=0,
        backoff_max=0,
    )
    p._client.request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _FakeResponse(200, _account_lookup_body()),
            _FakeResponse(503),
            _FakeResponse(200, _statuses_body()),
        ]
    )
    try:
        posts = await p.fetch()
    finally:
        await p.aclose()

    assert len(posts) == 1
    # 1 lookup + 2 status attempts (one 503, one success).
    assert p._client.request.call_count == 3


@pytest.mark.asyncio
async def test_429_treated_as_transient() -> None:
    """429 should also retry, not propagate as a fatal HTTP error."""
    p = TruthSocialProvider(
        handles=["realDonaldTrump"],
        timeout=5.0,
        max_attempts=3,
        backoff_min=0,
        backoff_max=0,
    )
    p._client.request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _FakeResponse(200, _account_lookup_body()),
            _FakeResponse(429),
            _FakeResponse(200, _statuses_body()),
        ]
    )
    try:
        posts = await p.fetch()
    finally:
        await p.aclose()

    assert len(posts) == 1
    assert p._client.request.call_count == 3


@pytest.mark.asyncio
async def test_persistent_5xx_logs_and_drops_handle() -> None:
    """If retries are exhausted, fetch() returns empty for that handle and
    logs the failure rather than propagating the exception.
    """
    p = TruthSocialProvider(
        handles=["realDonaldTrump"],
        timeout=5.0,
        max_attempts=2,
        backoff_min=0,
        backoff_max=0,
    )
    p._client.request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _FakeResponse(200, _account_lookup_body()),
            _FakeResponse(503),
            _FakeResponse(503),
        ]
    )
    try:
        posts = await p.fetch()
    finally:
        await p.aclose()
    assert posts == []


@pytest.mark.asyncio
async def test_404_clears_id_cache() -> None:
    """A 404 on the statuses endpoint (handle deleted/renamed) should evict
    the cached account id so the next call re-resolves.
    """
    p = TruthSocialProvider(handles=["realDonaldTrump"], timeout=5.0)
    p._id_cache["realDonaldTrump"] = "12345"
    p._client.request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[_FakeResponse(404)]
    )
    try:
        posts = await p.fetch()
    finally:
        await p.aclose()
    assert posts == []
    assert "realDonaldTrump" not in p._id_cache


def test_transient_class_is_local() -> None:
    """Sanity: _Transient is a local marker, not leaked from elsewhere."""
    assert issubclass(_Transient, Exception)
