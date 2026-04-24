from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Sequence
from typing import Any

from macropulse.alerter import DiscordAlerter
from macropulse.config import Settings
from macropulse.dedup import SeenStore
from macropulse.logging import configure_logging, get_logger
from macropulse.models import Post
from macropulse.providers.base import FeedProvider
from macropulse.providers.nitter import NitterProvider
from macropulse.providers.truthsocial import TruthSocialProvider
from macropulse.signal import evaluate


def build_providers(settings: Settings) -> list[FeedProvider]:
    providers: list[FeedProvider] = []
    if settings.TRUTHSOCIAL_HANDLES:
        providers.append(
            TruthSocialProvider(
                handles=settings.TRUTHSOCIAL_HANDLES,
                timeout=settings.HTTP_TIMEOUT_SECONDS,
                user_agent=settings.USER_AGENT,
            )
        )
    if settings.NITTER_HANDLES and settings.NITTER_INSTANCES:
        providers.append(
            NitterProvider(
                instances=settings.NITTER_INSTANCES,
                handles=settings.NITTER_HANDLES,
                timeout=settings.HTTP_TIMEOUT_SECONDS,
                user_agent=settings.USER_AGENT,
            )
        )
    return providers


async def run_once(
    providers: Sequence[FeedProvider],
    alerter: DiscordAlerter,
    seen: SeenStore,
    settings: Settings,
) -> int:
    log = get_logger(__name__)
    results = await asyncio.gather(*(p.fetch() for p in providers), return_exceptions=True)
    alerts_sent = 0
    for provider, result in zip(providers, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("provider.failed", provider=provider.name, error=str(result))
            continue
        for post in result:
            alerts_sent += await _process_post(post, alerter, seen, settings, log)
    return alerts_sent


async def _process_post(
    post: Post,
    alerter: DiscordAlerter,
    seen: SeenStore,
    settings: Settings,
    log: Any,
) -> int:
    if seen.has_seen(post.id):
        return 0
    alert = evaluate(post, settings.BEARISH_KEYWORDS, settings.SENTIMENT_THRESHOLD)
    if alert is None:
        seen.mark_seen(post.id)
        return 0
    log.info(
        "alert.match",
        post_id=post.id,
        provider=post.source,
        keyword=alert.matched_keyword,
        compound=alert.compound_score,
    )
    try:
        await alerter.send(alert)
    except Exception as exc:
        log.error("alert.send_failed", post_id=post.id, error=str(exc))
        return 0
    seen.mark_seen(post.id)
    return 1


async def main(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    configure_logging(settings.LOG_LEVEL)
    log = get_logger(__name__)
    log.info("runner.starting")

    seen = SeenStore(settings.DEDUP_DB_PATH)
    seen.prune(settings.DEDUP_TTL_DAYS)

    providers = build_providers(settings)
    alerter = DiscordAlerter(
        webhook_url=settings.DISCORD_WEBHOOK_URL.get_secret_value(),
        timeout=settings.HTTP_TIMEOUT_SECONDS,
        user_agent=settings.USER_AGENT,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        while not stop.is_set():
            try:
                n = await run_once(providers, alerter, seen, settings)
                log.info("tick.done", alerts_sent=n)
            except Exception as exc:
                log.error("tick.error", error=str(exc))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.POLL_INTERVAL_SECONDS)
    finally:
        log.info("runner.shutting_down")
        await alerter.aclose()
        for p in providers:
            close = getattr(p, "aclose", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    await close()
        seen.close()
        log.info("runner.stopped")
