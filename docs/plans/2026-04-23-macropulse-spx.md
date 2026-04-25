# MacroPulse SPX Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a production-grade async Python service that polls Truth Social + Nitter, flags bearish escalation posts via VADER + keyword match, and fires Discord webhook alerts — per the spec at `/Users/Ahmed/Downloads/macropulse-spx-claude-code-prompt.md`.

**Architecture:** Async `runner` loop drives a pool of `FeedProvider` implementations (Truth Social Mastodon API, Nitter RSS, Replay file). Each tick fans out `fetch()` in parallel, filters via pure `signal.evaluate()` (VADER + keyword regex), dedupes via SQLite, sends alerts through a rate-limited, retrying `DiscordAlerter`. Pydantic settings, structlog JSON logs, tenacity retries.

**Tech Stack:** Python 3.11 (since `uv` is unavailable on this machine, we fall back to `pip` + `requirements.txt` per the spec), httpx, pydantic + pydantic-settings, vaderSentiment, structlog, tenacity, selectolax, pytest + pytest-asyncio + respx, ruff, mypy --strict, stdlib sqlite3.

**Reference:** Full component contract lives in `/Users/Ahmed/Downloads/macropulse-spx-claude-code-prompt.md`. This plan is the execution trace; re-read the spec when ambiguity arises.

**Banned in `src/`:** `raise NotImplementedError`, `pass  # TODO`, `return []  # placeholder`, hardcoded fake data at runtime. Mocks only live in `tests/` via respx/fixtures.

---

## Task 0: Scaffold project

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt` (pip fallback path)
- Create: `requirements-dev.txt`
- Modify: `.gitignore` (add `data/`, `.venv/`, `.env`, `__pycache__/`, `*.egg-info/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`)
- Create: `src/macropulse/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py` (empty for now)

**Step 1:** Write `pyproject.toml` with:
- `[project]` name `macropulse`, version `0.1.0`, python `>=3.11`.
- Dependencies pinned: `httpx>=0.27`, `pydantic>=2.7`, `pydantic-settings>=2.3`, `vaderSentiment>=3.3.2`, `structlog>=24.1`, `tenacity>=8.3`, `selectolax>=0.3.21`.
- Optional `[project.optional-dependencies].dev`: `pytest>=8.2`, `pytest-asyncio>=0.23`, `respx>=0.21`, `ruff>=0.4`, `mypy>=1.10`.
- `[tool.ruff]` line-length 100, target-version py311, select = `["E", "F", "I", "UP", "B", "SIM", "RUF"]`.
- `[tool.mypy]` strict = true, python_version = "3.11", `plugins = ["pydantic.mypy"]`.
- `[tool.pytest.ini_options]` asyncio_mode = "auto", testpaths = `["tests"]`.
- `[build-system]` requires = `["setuptools>=68"]`, build-backend `setuptools.build_meta`.
- `[tool.setuptools.packages.find]` where = `["src"]`.

**Step 2:** Mirror runtime deps into `requirements.txt` and dev deps into `requirements-dev.txt`.

**Step 3:** Create venv + install:

```bash
cd /Users/Ahmed/Desktop/Dev/Jextra/macro-pulse
python3.11 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"
```

**Step 4:** Smoke-check:

```bash
.venv/bin/python -c "import httpx, pydantic, pydantic_settings, vaderSentiment, structlog, tenacity, selectolax; print('ok')"
.venv/bin/pytest -q  # should exit 5 (no tests collected, non-fatal) or 0
.venv/bin/ruff check .
```

**Step 5:** Commit

```bash
git add pyproject.toml requirements.txt requirements-dev.txt .gitignore src/macropulse/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore: scaffold macropulse package + tooling"
```

---

## Task 1: `models.py`

**Files:**
- Create: `src/macropulse/models.py`
- Create: `tests/test_models.py`

**Step 1:** Write the failing test `tests/test_models.py`:

```python
from datetime import datetime, timezone
from macropulse.models import Alert, Post


def test_post_roundtrips():
    post = Post(
        id="truthsocial:1",
        author="realDonaldTrump",
        source="truthsocial",
        content="hello",
        url="https://truthsocial.com/@realDonaldTrump/1",
        created_at=datetime(2026, 4, 23, 17, 0, tzinfo=timezone.utc),
    )
    assert post.source == "truthsocial"
    assert post.created_at.tzinfo is not None


def test_alert_wraps_post():
    post = Post(
        id="truthsocial:1",
        author="realDonaldTrump",
        source="truthsocial",
        content="hello",
        url="https://t.example/1",
        created_at=datetime(2026, 4, 23, 17, 0, tzinfo=timezone.utc),
    )
    alert = Alert(post=post, matched_keyword="hello", compound_score=-0.75)
    assert alert.post.id == "truthsocial:1"
    assert alert.compound_score == -0.75
```

**Step 2:** Run — expect ImportError/ModuleNotFoundError.

```bash
.venv/bin/pytest tests/test_models.py -v
```

**Step 3:** Implement `src/macropulse/models.py`:

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PostSource = Literal["truthsocial", "nitter", "replay"]


class Post(BaseModel):
    id: str
    author: str
    source: PostSource
    content: str
    url: str
    created_at: datetime


class Alert(BaseModel):
    post: Post
    matched_keyword: str
    compound_score: float = Field(..., le=1.0, ge=-1.0)
```

**Step 4:** `pytest tests/test_models.py -v` → PASS.

**Step 5:** Commit

```bash
git add src/macropulse/models.py tests/test_models.py
git commit -m "feat(models): Post and Alert pydantic models"
```

---

## Task 2: `config.py`

**Files:**
- Create: `src/macropulse/config.py`
- Create: `.env.example` (placeholder; final pass in Task 13)
- Create: `tests/test_config.py`

**Step 1:** Write `tests/test_config.py`:

```python
from pathlib import Path

from macropulse.config import Settings


def test_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/x/y")
    monkeypatch.setenv("DEDUP_DB_PATH", str(tmp_path / "seen.db"))
    s = Settings()
    assert s.POLL_INTERVAL_SECONDS == 60
    assert s.SENTIMENT_THRESHOLD == -0.6
    assert "shoot and kill" in s.BEARISH_KEYWORDS
    assert "realDonaldTrump" in s.TRUTHSOCIAL_HANDLES
    assert s.DEDUP_TTL_DAYS == 7
    assert s.DEDUP_DB_PATH == Path(tmp_path / "seen.db")


def test_webhook_required(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
```

**Step 2:** Run — expect fail.

**Step 3:** Implement `src/macropulse/config.py`:

```python
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DISCORD_WEBHOOK_URL: SecretStr

    POLL_INTERVAL_SECONDS: int = Field(default=60, ge=5)
    SENTIMENT_THRESHOLD: float = Field(default=-0.6, le=0.0, ge=-1.0)

    BEARISH_KEYWORDS: list[str] = Field(
        default_factory=lambda: [
            "shoot and kill",
            "mining",
            "blockade",
            "seized",
            "devastate",
            "no peace",
            "missile",
            "retaliation",
            "oil embargo",
            "strait closed",
        ]
    )

    TRUTHSOCIAL_HANDLES: list[str] = Field(default_factory=lambda: ["realDonaldTrump"])

    NITTER_INSTANCES: list[str] = Field(
        default_factory=lambda: ["https://nitter.net", "https://nitter.privacydev.net"]
    )
    NITTER_HANDLES: list[str] = Field(default_factory=list)

    DEDUP_DB_PATH: Path = Path("./data/seen.db")
    DEDUP_TTL_DAYS: int = Field(default=7, ge=1)

    LOG_LEVEL: str = "INFO"
    HTTP_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0.0)
    USER_AGENT: str = "MacroPulseSPX/1.0"
```

**Step 4:** `pytest tests/test_config.py -v` → PASS.

**Step 5:** Commit

```bash
git add src/macropulse/config.py tests/test_config.py
git commit -m "feat(config): pydantic-settings Settings with defaults"
```

---

## Task 3: `logging.py`

**Files:**
- Create: `src/macropulse/logging.py`
- Create: `tests/test_logging.py`

**Step 1:** Test:

```python
import json
import logging as stdlog

from macropulse.logging import configure_logging, get_logger


def test_logger_emits_json(capsys):
    configure_logging(level="INFO")
    log = get_logger("test")
    log.info("hello", post_id="abc", compound=-0.7)
    out = capsys.readouterr().out.strip().splitlines()[-1]
    parsed = json.loads(out)
    assert parsed["event"] == "hello"
    assert parsed["post_id"] == "abc"
    assert parsed["compound"] == -0.7
    assert "ts" in parsed and "level" in parsed
    # reset root so other tests aren't affected
    stdlog.getLogger().handlers.clear()
```

**Step 2:** Run — fail.

**Step 3:** Implement:

```python
import logging as stdlog
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    stdlog.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(stdlog, level.upper(), stdlog.INFO),
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(stdlog, level.upper(), stdlog.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
```

Note: structlog's `EventRenamer` exists in 24.x. The test asserts `event` key — the default key is already `event`, so EventRenamer is belt-and-suspenders. If EventRenamer import fails, drop it and rely on the default.

**Step 4:** `pytest tests/test_logging.py -v` → PASS.

**Step 5:** Commit

```bash
git add src/macropulse/logging.py tests/test_logging.py
git commit -m "feat(logging): structlog JSON configuration"
```

---

## Task 4: `signal.py`

**Files:**
- Create: `src/macropulse/signal.py`
- Create: `tests/test_signal.py`

**Step 1:** Tests (covers every bullet in spec §Tests → test_signal.py):

```python
from datetime import datetime, timezone

import pytest

from macropulse.models import Post
from macropulse.signal import evaluate


def _post(content: str) -> Post:
    return Post(
        id="x:1",
        author="a",
        source="replay",
        content=content,
        url="https://example.com/1",
        created_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
    )


KEYWORDS = ["shoot and kill", "mining", "blockade", "retaliation"]


def test_keyword_and_negative_returns_alert():
    alert = evaluate(_post("we will shoot and kill the enemy, destroy everything"), KEYWORDS, -0.6)
    assert alert is not None
    assert alert.matched_keyword == "shoot and kill"
    assert alert.compound_score < -0.6


def test_keyword_but_not_negative_enough_returns_none():
    alert = evaluate(_post("shoot and kill a great free-throw percentage tonight!"), KEYWORDS, -0.6)
    assert alert is None


def test_no_keyword_even_if_negative_returns_none():
    alert = evaluate(_post("terrible awful disaster pain suffering death"), KEYWORDS, -0.6)
    assert alert is None


def test_word_boundary_mining_does_not_match_examining():
    alert = evaluate(_post("examining the data is frustrating and painful"), ["mining"], -0.6)
    assert alert is None


def test_multi_match_returns_first_in_list_order():
    alert = evaluate(
        _post("blockade and retaliation escalate, horrible devastating war"),
        ["retaliation", "blockade"],  # retaliation first
        -0.6,
    )
    assert alert is not None
    assert alert.matched_keyword == "retaliation"


@pytest.mark.parametrize(
    "kw,text,should_match",
    [
        ("mining", "mining operations started", True),
        ("mining", "examining operations", False),
        ("shoot and kill", "I will shoot and kill them", True),
    ],
)
def test_boundary_matrix(kw, text, should_match):
    alert = evaluate(_post(text + " horrible awful devastating war"), [kw], -0.3)
    assert (alert is not None) is should_match
```

**Step 2:** Run — fail (ImportError).

**Step 3:** Implement `src/macropulse/signal.py`:

```python
import re
from functools import lru_cache

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from macropulse.models import Alert, Post


@lru_cache(maxsize=1)
def _analyzer() -> SentimentIntensityAnalyzer:
    return SentimentIntensityAnalyzer()


@lru_cache(maxsize=512)
def _compiled(keyword: str) -> re.Pattern[str]:
    kw = keyword.lower()
    if " " in kw:
        return re.compile(re.escape(kw))
    return re.compile(rf"\b{re.escape(kw)}\b")


def evaluate(post: Post, keywords: list[str], threshold: float) -> Alert | None:
    """Return an Alert iff a keyword matches AND VADER compound < threshold.

    - Multi-word keywords use substring match on lowercased content.
    - Single-word keywords use word boundaries to avoid "mining" matching "examining".
    - On multiple keyword matches, the first in list order wins (deterministic).
    """
    text = post.content.lower()
    matched: str | None = None
    for kw in keywords:
        if _compiled(kw).search(text):
            matched = kw
            break
    if matched is None:
        return None

    compound = _analyzer().polarity_scores(post.content)["compound"]
    if compound >= threshold:
        return None
    return Alert(post=post, matched_keyword=matched, compound_score=compound)
```

**Step 4:** `pytest tests/test_signal.py -v` → all PASS. If VADER scores surprise the assertions, adjust the test text so it’s unambiguously negative (keep the structural assertions).

**Step 5:** Commit

```bash
git add src/macropulse/signal.py tests/test_signal.py
git commit -m "feat(signal): keyword + VADER evaluator with word-boundary matching"
```

---

## Task 5: `dedup.py`

**Files:**
- Create: `src/macropulse/dedup.py`
- Create: `tests/test_dedup.py`

**Step 1:** Tests:

```python
from datetime import datetime, timedelta, timezone

from macropulse.dedup import SeenStore


def test_roundtrip_memory():
    store = SeenStore(":memory:")
    assert store.has_seen("a") is False
    store.mark_seen("a")
    assert store.has_seen("a") is True


def test_prune_removes_old_rows():
    store = SeenStore(":memory:")
    old = datetime.now(timezone.utc) - timedelta(days=30)
    fresh = datetime.now(timezone.utc)
    store._insert_with_ts("old", old)  # type: ignore[attr-defined]
    store._insert_with_ts("fresh", fresh)  # type: ignore[attr-defined]
    store.prune(ttl_days=7)
    assert store.has_seen("old") is False
    assert store.has_seen("fresh") is True


def test_dedup_creates_file(tmp_path):
    db = tmp_path / "nested" / "seen.db"
    store = SeenStore(db)
    store.mark_seen("a")
    assert db.exists()
    store.close()
```

**Step 2:** Run — fail.

**Step 3:** Implement `src/macropulse/dedup.py`:

```python
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class SeenStore:
    """SQLite-backed dedup store. `:memory:` supported for tests."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS seen_posts (
      id TEXT PRIMARY KEY,
      seen_at TIMESTAMP NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_seen_at ON seen_posts(seen_at);
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path
        if isinstance(db_path, Path):
            db_path.parent.mkdir(parents=True, exist_ok=True)
            target = str(db_path)
        else:
            target = db_path
        self._conn = sqlite3.connect(
            target, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False
        )
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    def has_seen(self, post_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM seen_posts WHERE id = ?", (post_id,))
        return cur.fetchone() is not None

    def mark_seen(self, post_id: str) -> None:
        self._insert_with_ts(post_id, datetime.now(timezone.utc))

    def _insert_with_ts(self, post_id: str, ts: datetime) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_posts (id, seen_at) VALUES (?, ?)",
            (post_id, ts),
        )
        self._conn.commit()

    def prune(self, ttl_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
        cur = self._conn.execute("DELETE FROM seen_posts WHERE seen_at < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()
```

**Step 4:** `pytest tests/test_dedup.py -v` → PASS.

**Step 5:** Commit

```bash
git add src/macropulse/dedup.py tests/test_dedup.py
git commit -m "feat(dedup): sqlite-backed SeenStore with prune"
```

---

## Task 6: `alerter.py`

**Files:**
- Create: `src/macropulse/alerter.py`
- Create: `tests/test_alerter.py`

**Step 1:** Tests (covers spec §Tests → test_alerter.py):

```python
from datetime import datetime, timezone

import httpx
import pytest
import respx

from macropulse.alerter import DiscordAlerter
from macropulse.models import Alert, Post

WEBHOOK = "https://discord.com/api/webhooks/111/abc"


def _alert() -> Alert:
    post = Post(
        id="truthsocial:1",
        author="realDonaldTrump",
        source="truthsocial",
        content="they want to shoot and kill our people, no peace",
        url="https://truthsocial.com/@realDonaldTrump/1",
        created_at=datetime(2026, 4, 23, 17, 0, tzinfo=timezone.utc),
    )
    return Alert(post=post, matched_keyword="shoot and kill", compound_score=-0.81)


@pytest.mark.asyncio
@respx.mock
async def test_embed_shape():
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(204))
    async with DiscordAlerter(webhook_url=WEBHOOK, timeout=5.0) as alerter:
        await alerter.send(_alert())
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "shoot and kill" in body
    # embed shape
    import json

    payload = json.loads(body)
    embed = payload["embeds"][0]
    assert "shoot and kill" in embed["title"]
    assert "they want to" in embed["description"]
    assert embed["color"] == 0xE74C3C
    assert embed["url"] == "https://truthsocial.com/@realDonaldTrump/1"
    field_names = {f["name"] for f in embed["fields"]}
    assert {"Sentiment", "Author", "Source", "Posted At"}.issubset(field_names)


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_429():
    route = respx.post(WEBHOOK).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}, json={"message": "rate"}),
            httpx.Response(204),
        ]
    )
    async with DiscordAlerter(webhook_url=WEBHOOK, timeout=5.0) as alerter:
        await alerter.send(_alert())
    assert route.call_count == 2
```

**Step 2:** Run — fail.

**Step 3:** Implement `src/macropulse/alerter.py`:

```python
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
        self.status = status
        self.retry_after = retry_after


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
            raise e.last_attempt.exception() or e

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
                        {"name": "Sentiment", "value": f"{alert.compound_score:.3f}", "inline": True},
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
```

**Step 4:** `pytest tests/test_alerter.py -v` → PASS.

**Step 5:** Commit

```bash
git add src/macropulse/alerter.py tests/test_alerter.py
git commit -m "feat(alerter): Discord webhook sender with retry + rate limit"
```

---

## Task 7: `providers/base.py` + `providers/replay.py` + fixtures bootstrap

**Files:**
- Create: `src/macropulse/providers/__init__.py` (empty)
- Create: `src/macropulse/providers/base.py`
- Create: `src/macropulse/providers/replay.py`
- Create: `tests/fixtures/april_23_2026_post.json`
- Create: `tests/test_providers_replay.py`

**Step 1:** Fixture `tests/fixtures/april_23_2026_post.json`:

```json
[
  {
    "id": "truthsocial:replay-april-23",
    "author": "realDonaldTrump",
    "source": "truthsocial",
    "content": "They will shoot and kill innocents. Iran threatens retaliation. Oil embargo imminent. Horrible, devastating war looms — no peace possible.",
    "url": "https://truthsocial.com/@realDonaldTrump/replay-april-23",
    "created_at": "2026-04-23T17:00:00+00:00"
  }
]
```

**Step 2:** Tests:

```python
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
```

**Step 3:** Run — fail.

**Step 4:** Implement `src/macropulse/providers/base.py`:

```python
from typing import Protocol, runtime_checkable

from macropulse.models import Post


@runtime_checkable
class FeedProvider(Protocol):
    name: str

    async def fetch(self) -> list[Post]: ...
```

Implement `src/macropulse/providers/replay.py`:

```python
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
```

**Step 5:** `pytest tests/test_providers_replay.py -v` → PASS.

**Step 6:** Commit

```bash
git add src/macropulse/providers/__init__.py src/macropulse/providers/base.py src/macropulse/providers/replay.py tests/fixtures/april_23_2026_post.json tests/test_providers_replay.py
git commit -m "feat(providers): FeedProvider protocol + ReplayProvider"
```

---

## Task 8: `providers/truthsocial.py`

**Files:**
- Create: `src/macropulse/providers/truthsocial.py`
- Create: `tests/fixtures/truthsocial_account_lookup.json`
- Create: `tests/fixtures/truthsocial_statuses.json`
- Create: `tests/test_providers_truthsocial.py`

**Step 1:** Fixtures.

`tests/fixtures/truthsocial_account_lookup.json`:

```json
{ "id": "12345", "username": "realDonaldTrump", "acct": "realDonaldTrump" }
```

`tests/fixtures/truthsocial_statuses.json`:

```json
[
  {
    "id": "987654321",
    "created_at": "2026-04-23T17:00:00.000Z",
    "content": "<p>hello <b>world</b></p>",
    "url": "https://truthsocial.com/@realDonaldTrump/987654321",
    "account": { "acct": "realDonaldTrump" }
  }
]
```

**Step 2:** Tests (covers spec §Tests → test_providers_truthsocial.py):

```python
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
        return_value=httpx.Response(200, json=json.loads((FX / "truthsocial_account_lookup.json").read_text()))
    )
    respx.get("https://truthsocial.com/api/v1/accounts/12345/statuses").mock(
        return_value=httpx.Response(200, json=json.loads((FX / "truthsocial_statuses.json").read_text()))
    )
    async with TruthSocialProvider(handles=["realDonaldTrump"], timeout=5.0) as p:
        posts = await p.fetch()
    assert len(posts) == 1
    assert posts[0].id == "truthsocial:987654321"
    assert posts[0].content == "hello world"
    assert posts[0].author == "realDonaldTrump"


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_5xx(monkeypatch):
    lookup = respx.get("https://truthsocial.com/api/v1/accounts/lookup").mock(
        return_value=httpx.Response(200, json=json.loads((FX / "truthsocial_account_lookup.json").read_text()))
    )
    statuses = respx.get("https://truthsocial.com/api/v1/accounts/12345/statuses").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json=json.loads((FX / "truthsocial_statuses.json").read_text())),
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
```

**Step 3:** Run — fail.

**Step 4:** Implement `src/macropulse/providers/truthsocial.py`:

```python
from __future__ import annotations

from datetime import datetime
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
            except Exception as exc:  # keep provider resilient
                log.warning("truthsocial.fetch_failed", handle=handle, error=str(exc))
                continue
            for s in statuses:
                posts.append(self._to_post(handle, s))
        return posts

    async def _resolve_id(self, handle: str) -> str:
        if handle in self._id_cache:
            return self._id_cache[handle]
        data = await self._request("GET", f"{_BASE}/api/v1/accounts/lookup", params={"acct": handle})
        acct_id = str(data["id"])
        self._id_cache[handle] = acct_id
        return acct_id

    async def _statuses(self, acct_id: str) -> list[dict]:
        return await self._request(
            "GET",
            f"{_BASE}/api/v1/accounts/{acct_id}/statuses",
            params={"limit": 40, "exclude_replies": "false", "exclude_reblogs": "false"},
        )

    async def _request(self, method: str, url: str, **kwargs) -> list | dict:
        try:
            async for attempt in AsyncRetrying(
                reraise=True,
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential(multiplier=1, min=self._backoff_min, max=self._backoff_max),
                retry=retry_if_exception_type((_Transient, httpx.TransportError)),
            ):
                with attempt:
                    resp = await self._client.request(method, url, **kwargs)
                    if resp.status_code == 404:
                        # bust cache for ID lookups
                        if "/accounts/lookup" not in url:
                            self._id_cache.clear()
                        resp.raise_for_status()
                    if resp.status_code == 429 or 500 <= resp.status_code < 600:
                        raise _Transient(f"{resp.status_code} on {url}")
                    resp.raise_for_status()
                    return resp.json()
        except RetryError as e:
            raise e.last_attempt.exception() or e
        raise RuntimeError("unreachable")

    @staticmethod
    def _strip_html(html: str) -> str:
        return HTMLParser(html).text(separator=" ").strip()

    def _to_post(self, handle: str, status: dict) -> Post:
        return Post(
            id=f"truthsocial:{status['id']}",
            author=handle,
            source="truthsocial",
            content=self._strip_html(status.get("content") or ""),
            url=status.get("url", ""),
            created_at=_parse_iso(status["created_at"]),
        )


def _parse_iso(value: str) -> datetime:
    # Mastodon returns e.g. "2026-04-23T17:00:00.000Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
```

**Step 5:** `pytest tests/test_providers_truthsocial.py -v` → PASS.

**Step 6:** Commit

```bash
git add src/macropulse/providers/truthsocial.py tests/fixtures/truthsocial_account_lookup.json tests/fixtures/truthsocial_statuses.json tests/test_providers_truthsocial.py
git commit -m "feat(providers): TruthSocialProvider with handle→id cache + retries"
```

---

## Task 9: `providers/nitter.py`

**Files:**
- Create: `src/macropulse/providers/nitter.py`
- Create: `tests/fixtures/nitter_rss.xml`
- Create: `tests/test_providers_nitter.py`

**Step 1:** Fixture `tests/fixtures/nitter_rss.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>realDonaldTrump / @realDonaldTrump</title>
    <link>https://nitter.privacydev.net/realDonaldTrump</link>
    <description>Twitter feed for: @realDonaldTrump.</description>
    <item>
      <title>shoot and kill — horrible devastating war</title>
      <description>&lt;p&gt;They will shoot and kill innocents. Retaliation imminent.&lt;/p&gt;</description>
      <pubDate>Thu, 23 Apr 2026 17:00:00 GMT</pubDate>
      <guid>https://nitter.privacydev.net/realDonaldTrump/status/987654321#m</guid>
      <link>https://nitter.privacydev.net/realDonaldTrump/status/987654321#m</link>
    </item>
  </channel>
</rss>
```

**Step 2:** Tests:

```python
from pathlib import Path

import httpx
import pytest
import respx

from macropulse.providers.nitter import NitterProvider

FX = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
@respx.mock
async def test_rotates_instances_on_502():
    rss = (FX / "nitter_rss.xml").read_text()
    bad = respx.get("https://nitter.net/realDonaldTrump/rss").mock(
        return_value=httpx.Response(502)
    )
    good = respx.get("https://nitter.privacydev.net/realDonaldTrump/rss").mock(
        return_value=httpx.Response(200, text=rss, headers={"Content-Type": "application/rss+xml"})
    )
    async with NitterProvider(
        instances=["https://nitter.net", "https://nitter.privacydev.net"],
        handles=["realDonaldTrump"],
        timeout=5.0,
        max_attempts=1,  # don't retry within an instance; just rotate
    ) as p:
        posts = await p.fetch()
    assert bad.called
    assert good.called
    assert len(posts) == 1
    assert posts[0].id.startswith("nitter:")
    assert posts[0].author == "realDonaldTrump"
    assert "shoot and kill" in posts[0].content.lower()


@pytest.mark.asyncio
@respx.mock
async def test_all_instances_fail_returns_empty():
    respx.get("https://nitter.net/realDonaldTrump/rss").mock(return_value=httpx.Response(502))
    respx.get("https://nitter.privacydev.net/realDonaldTrump/rss").mock(
        return_value=httpx.Response(503)
    )
    async with NitterProvider(
        instances=["https://nitter.net", "https://nitter.privacydev.net"],
        handles=["realDonaldTrump"],
        timeout=5.0,
        max_attempts=1,
    ) as p:
        posts = await p.fetch()
    assert posts == []
```

**Step 3:** Run — fail.

**Step 4:** Implement `src/macropulse/providers/nitter.py`:

```python
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime
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
            raise e.last_attempt.exception() or e
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
                    created = created.replace(tzinfo=datetime.now().astimezone().tzinfo)
            except (TypeError, ValueError):
                continue
            posts.append(
                Post(
                    id=f"nitter:{guid}",
                    author=handle,
                    source="nitter",
                    content=HTMLParser(desc_raw).text(separator=" ").strip(),
                    url=link,
                    created_at=created,
                )
            )
        return posts
```

**Step 5:** `pytest tests/test_providers_nitter.py -v` → PASS.

**Step 6:** Commit

```bash
git add src/macropulse/providers/nitter.py tests/fixtures/nitter_rss.xml tests/test_providers_nitter.py
git commit -m "feat(providers): NitterProvider with instance rotation"
```

---

## Task 10: `runner.py`

**Files:**
- Create: `src/macropulse/runner.py`

**Design contract:**
- `build_providers(settings) -> list[FeedProvider]` — factory that honors settings (Truth Social, Nitter).
- `run_once(providers, alerter, seen, settings, log)` — one tick. Returns number of alerts sent. Used by E2E test.
- `main(settings=None)` — initializes everything, installs SIGINT/SIGTERM handlers, loops.

**Step 1:** Implement (no separate test — the E2E test in Task 11 drives it; unit-level coverage of the loop is the E2E fixture):

```python
from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Sequence

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
        for post in result:  # type: ignore[assignment]
            alerts_sent += await _process_post(post, alerter, seen, settings, log)
    return alerts_sent


async def _process_post(
    post: Post,
    alerter: DiscordAlerter,
    seen: SeenStore,
    settings: Settings,
    log,
) -> int:
    if seen.has_seen(post.id):
        return 0
    alert = evaluate(post, settings.BEARISH_KEYWORDS, settings.SENTIMENT_THRESHOLD)
    if alert is None:
        seen.mark_seen(post.id)  # avoid re-evaluating the same non-matching post each tick
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
    settings = settings or Settings()  # type: ignore[call-arg]
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
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
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
```

**Step 2:** `ruff check . && mypy src` → clean (fix type hints if mypy complains about `log` parameter — change to `structlog.stdlib.BoundLogger` or use `Any`).

**Step 3:** Commit

```bash
git add src/macropulse/runner.py
git commit -m "feat(runner): async main loop with graceful shutdown"
```

---

## Task 11: `__main__.py` + E2E test

**Files:**
- Create: `src/macropulse/__main__.py`
- Create: `tests/test_runner_e2e.py`

**Step 1:** `src/macropulse/__main__.py`:

```python
import asyncio

from macropulse.runner import main

if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2:** E2E test `tests/test_runner_e2e.py` (covers spec §Tests → test_runner_e2e.py):

```python
from pathlib import Path

import httpx
import pytest
import respx

from macropulse.alerter import DiscordAlerter
from macropulse.config import Settings
from macropulse.dedup import SeenStore
from macropulse.providers.replay import ReplayProvider
from macropulse.runner import run_once

WEBHOOK = "https://discord.com/api/webhooks/111/abc"
FX = Path(__file__).parent / "fixtures" / "april_23_2026_post.json"


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setenv("DEDUP_DB_PATH", str(tmp_path / "seen.db"))
    monkeypatch.setenv("NITTER_HANDLES", "[]")  # keep providers out of the picture for E2E
    monkeypatch.setenv("TRUTHSOCIAL_HANDLES", "[]")
    return Settings()


@pytest.mark.asyncio
@respx.mock
async def test_april_23_reference(tmp_path, settings):
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(204))
    provider = ReplayProvider(FX)
    seen = SeenStore(tmp_path / "seen.db")
    async with DiscordAlerter(webhook_url=WEBHOOK, timeout=5.0) as alerter:
        sent_first = await run_once([provider], alerter, seen, settings)
        sent_second = await run_once([provider], alerter, seen, settings)

    assert sent_first == 1
    assert sent_second == 0
    assert route.call_count == 1
    import json

    payload = json.loads(route.calls.last.request.content.decode())
    embed = payload["embeds"][0]
    assert "shoot and kill" in embed["title"]
    sentiment_field = next(f for f in embed["fields"] if f["name"] == "Sentiment")
    assert float(sentiment_field["value"]) < -0.6
```

**Step 3:** Run the full suite:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
```

All three must exit 0. Fix issues in place — do not paper over with `# type: ignore` unless the spec forces it (e.g. structlog dynamic logger typing).

**Step 4:** Commit

```bash
git add src/macropulse/__main__.py tests/test_runner_e2e.py
git commit -m "feat: __main__ entrypoint + April 23 E2E reference test"
```

---

## Task 12: Final spec audit

**Step 1:** Grep `src/` for banned patterns:

```bash
rg -n "NotImplementedError|# TODO|# placeholder" src/ || echo "clean"
```

Expected: `clean`.

**Step 2:** Confirm every file listed in the spec's "Project Layout" exists. Fix gaps.

**Step 3:** Commit any cleanup under `chore: spec audit`.

---

## Task 13: `README.md` and `.env.example`

**Files:**
- Modify: `README.md` (currently a placeholder)
- Create/finalize: `.env.example`

**Step 1:** `README.md` must include:
- One-paragraph description + motivating April 23 scenario.
- Quickstart (Python 3.11, `.venv`, `pip install -e ".[dev]"`, copy `.env.example` → `.env`, `python -m macropulse`).
- `pytest -q`, `ruff check .`, `mypy src` commands.
- How to tune `BEARISH_KEYWORDS` and `SENTIMENT_THRESHOLD` (edit `.env`, lower threshold = stricter alerts, add domain-specific keywords — note VADER bias).
- How to add a new `FeedProvider` (implement `FeedProvider` Protocol from `providers/base.py`, register in `runner.build_providers`).
- Sample `systemd` unit:

```ini
[Unit]
Description=MacroPulse SPX
After=network-online.target

[Service]
Type=simple
User=macropulse
WorkingDirectory=/opt/macropulse
EnvironmentFile=/opt/macropulse/.env
ExecStart=/opt/macropulse/.venv/bin/python -m macropulse
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Step 2:** `.env.example` — one line per `Settings` field with an inline comment describing the knob. Example:

```dotenv
# Required. Discord webhook URL (https://discord.com/api/webhooks/<id>/<token>)
DISCORD_WEBHOOK_URL=

# Poll cadence. Lower = faster detection, higher = lower rate-limit risk.
POLL_INTERVAL_SECONDS=60

# VADER compound ceiling for an alert; more negative = stricter.
SENTIMENT_THRESHOLD=-0.6

# Keywords (JSON list). Any match + negative sentiment fires an alert.
BEARISH_KEYWORDS=["shoot and kill","mining","blockade","seized","devastate","no peace","missile","retaliation","oil embargo","strait closed"]

# Truth Social handles to poll.
TRUTHSOCIAL_HANDLES=["realDonaldTrump"]

# Nitter instance pool (failover order).
NITTER_INSTANCES=["https://nitter.net","https://nitter.privacydev.net"]

# Nitter handles to poll (X/Twitter accounts). Leave [] to disable.
NITTER_HANDLES=[]

# SQLite dedup DB path (parent dir auto-created).
DEDUP_DB_PATH=./data/seen.db

# Drop dedup entries older than N days on startup.
DEDUP_TTL_DAYS=7

# Log verbosity: DEBUG | INFO | WARNING | ERROR
LOG_LEVEL=INFO

# httpx timeout for all outbound HTTP.
HTTP_TIMEOUT_SECONDS=15.0

# UA string sent to Truth Social / Nitter / Discord.
USER_AGENT=MacroPulseSPX/1.0
```

**Step 3:** Commit

```bash
git add README.md .env.example
git commit -m "docs: README + .env.example"
```

---

## Task 14: Acceptance gate

Run all three checks — each must exit 0:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
```

Manual spot check: `python -m macropulse` boots (DISCORD_WEBHOOK_URL required; set a throwaway webhook or let it crash on validation to confirm config loads).

If all green, the plan is done. Final tag/commit:

```bash
git log --oneline
```

Should show a linear, sensible progression from scaffold → E2E passing. No fixup commits required.
