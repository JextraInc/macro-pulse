# MacroPulse SPX

Real-time bearish-signal detector for political and geopolitical social media.
Polls Truth Social (Mastodon-fork API) and X (via Nitter RSS) for posts from
configured handles, flags bearish escalation triggers using VADER sentiment
analysis plus a configurable keyword list, and fires formatted Discord alerts
with seconds-to-minutes latency.

**Motivating scenario:** On April 23, 2026 at ~1:00 PM EDT, a "shoot and kill"
post on Truth Social plus Iranian retaliation threats drove a 1% SPY drop within
minutes. This service is built to reliably catch events like that in real time.

## Quickstart

Requires Python 3.11+. Uses `pip` + `requirements.txt` (the spec's `uv` fallback
path — `uv` isn't required).

```bash
git clone <repo> macropulse-spx && cd macropulse-spx
python3.11 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"

cp .env.example .env
# Edit .env — DISCORD_WEBHOOK_URL is required.

.venv/bin/python -m macropulse
```

Structured JSON logs stream to stdout. The SQLite dedup database is created on
startup at the path in `DEDUP_DB_PATH` (defaults to `./data/seen.db`).

## Development

```bash
.venv/bin/pytest -q          # 27 tests
.venv/bin/ruff check .       # lint
.venv/bin/mypy src           # strict type check
```

Tests use `respx` to mock Truth Social, Nitter, and Discord — no live network
calls in CI. The `tests/test_runner_e2e.py` test reproduces the April 23, 2026
scenario end-to-end (fixture → signal → alert embed → dedup).

## Configuration

All settings live in `.env` (see `.env.example` for every field with inline
comments). The two knobs you'll tune most:

- **`BEARISH_KEYWORDS`** — JSON list. Match is case-insensitive; multi-word
  keywords use substring matching, single-word keywords use word boundaries
  (so `"mining"` won't match `"examining"`). On multiple matches, the first
  keyword in list order wins (deterministic). Add domain-specific escalation
  vocabulary: `"tariffs"`, `"sanctions"`, `"strait of hormuz"`, etc.
- **`SENTIMENT_THRESHOLD`** — VADER compound ceiling for an alert. Default
  `-0.6`. More negative = stricter. If you're getting noisy alerts, drop to
  `-0.75`. If you're missing real events, raise to `-0.45`. VADER scores the
  original post content (not lowercased).

A post triggers an alert only if it matches a keyword AND its VADER compound
score is strictly below the threshold.

## Adding a new feed provider

1. Implement the `FeedProvider` Protocol from
   [src/macropulse/providers/base.py](src/macropulse/providers/base.py):

   ```python
   class FeedProvider(Protocol):
       name: str
       async def fetch(self) -> list[Post]: ...
   ```

2. Place the file under `src/macropulse/providers/yours.py`. Build `Post`
   objects with source-unique IDs (e.g. `f"bluesky:{cid}"`) so dedup works
   across providers.
3. Wire it into `build_providers()` in
   [src/macropulse/runner.py](src/macropulse/runner.py), gated on the relevant
   `Settings` fields.
4. Add a test file `tests/test_providers_yours.py` with `respx`-mocked
   responses. Mirror the pattern in `tests/test_providers_truthsocial.py`.

Providers are expected to be resilient: a provider raising from `fetch()` must
not kill the runner loop. The built-in providers return `[]` on full failure
and log a warning.

## 24/7 operation with systemd

Save as `/etc/systemd/system/macropulse.service`:

```ini
[Unit]
Description=MacroPulse SPX
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=macropulse
WorkingDirectory=/opt/macropulse
EnvironmentFile=/opt/macropulse/.env
ExecStart=/opt/macropulse/.venv/bin/python -m macropulse
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now macropulse
journalctl -u macropulse -f     # tail structured JSON logs
```

## Architecture

```
┌──────────────┐   ┌──────────────┐
│ TruthSocial  │   │   Nitter     │
│  Mastodon    │   │   RSS pool   │
│     API      │   │ (rotating)   │
└──────┬───────┘   └──────┬───────┘
       │                   │
       │   async gather    │
       └────────┬──────────┘
                ▼
         ┌────────────┐    ┌──────────┐
         │   Signal   │───▶│ SeenStore│
         │ (VADER +   │    │ (SQLite) │
         │  keywords) │    └──────────┘
         └──────┬─────┘
                ▼
         ┌────────────┐
         │  Discord   │
         │  Alerter   │
         │ (tenacity) │
         └────────────┘
```

Each tick (`POLL_INTERVAL_SECONDS`, default 60s), the runner fans out provider
fetches in parallel, deduplicates by post ID, evaluates each new post, and
fires an alert if both the keyword match and sentiment check pass. Discord
failures back off exponentially and honor `Retry-After`; successful sends mark
the post as seen. Provider outages, Discord 429s, or network errors do not
kill the loop. SIGINT / SIGTERM shuts down gracefully.

## Embedding into another service

MacroPulse SPX is built as both a standalone daemon (`python -m macropulse`)
and a library. The pieces below are stable, side-effect-free, and safe to
import from another scheduler (e.g. APScheduler in a host service).

### Public API surface

| Symbol | Module | What it does |
|---|---|---|
| `Post` / `Alert` | `macropulse.models` | Pydantic v2 data models. `Post.id` is the dedup key; format `f"{source}:{native_id}"`. |
| `evaluate(post, keywords, threshold)` | `macropulse.signal` | Pure function. Returns `Alert \| None`. Runs VADER once. |
| `FeedProvider` Protocol | `macropulse.providers.base` | `name: str` + `async fetch() -> list[Post]`. |
| `TruthSocialProvider` | `macropulse.providers.truthsocial` | Real Mastodon-fork client with handle→id cache, tenacity retries, HTML stripping. Async context manager. |
| `NitterProvider` | `macropulse.providers.nitter` | RSS-over-Nitter with instance rotation. Returns `[]` on full-pool failure. |
| `ReplayProvider` | `macropulse.providers.replay` | One-shot file replay. Use for backtests or seeding integration tests. |
| `SeenStore` | `macropulse.dedup` | SQLite-backed `has_seen` / `mark_seen` / `prune`. Pass `":memory:"` or any `Path`. |
| `DiscordAlerter` | `macropulse.alerter` | Async webhook sender with rate limiter + Retry-After-aware backoff. Embeds-style payload. |
| `run_once(providers, alerter, seen, settings)` | `macropulse.runner` | One pipeline tick. Returns alert count. Survives Discord/provider failures. |
| `build_providers(settings)` | `macropulse.runner` | Factory honoring `Settings`. Skip if you build providers directly. |

### Minimal embedding example

```python
# inside another service's scheduler
from datetime import UTC, datetime
from macropulse.providers.truthsocial import TruthSocialProvider
from macropulse.signal import evaluate
from macropulse.dedup import SeenStore

KEYWORDS = ["shoot and kill", "blockade", "retaliation", "missile"]
THRESHOLD = -0.6

seen = SeenStore("./data/macropulse_seen.db")  # share or isolate per host
seen.prune(ttl_days=7)

async def tick() -> list[dict]:
    """Returns alerts as plain dicts; caller decides where to send them."""
    alerts: list[dict] = []
    async with TruthSocialProvider(handles=["realDonaldTrump"]) as p:
        for post in await p.fetch():
            if seen.has_seen(post.id):
                continue
            seen.mark_seen(post.id)
            alert = evaluate(post, KEYWORDS, THRESHOLD)
            if alert is None:
                continue
            alerts.append({
                "source": "macropulse",
                "ticker": "SPY",         # or whatever your ticker model expects
                "ts": datetime.now(UTC),
                "headline": f"Bearish trigger: {alert.matched_keyword}",
                "body": alert.post.content,
                "url": alert.post.url,
                "compound": alert.compound_score,
            })
    return alerts
```

Wrap that in your existing job runner, hand the dicts to your alert dispatcher,
and you've integrated. No daemon mode, no separate Discord webhook, no
parallel SQLite.

### Knobs to align across services

- **Dedup ownership** — pick one SQLite file. If the dashboard is the system
  of record, point `SeenStore` at *its* `data/` dir; or skip `SeenStore`
  entirely and dedup against your own `alerts_log` table by `Post.id`.
- **Discord styling** — `DiscordAlerter` posts an embed; the dashboard's
  `alerts/discord.py` posts plain markdown / file attachments. If you want
  one channel, pick one shape and bypass the other (use `evaluate()` only and
  hand the result to the dashboard's dispatcher).
- **Logging** — MacroPulse uses `structlog` JSON to stdout; the dashboard
  uses `loguru`. When embedding, you generally want the host's logger.
  Either (a) call `macropulse.logging.configure_logging(...)` from `__main__`
  only and don't call it from library code (the providers grab their logger
  via `get_logger(__name__)` at import time, so they'll get whatever
  structlog config is in effect), or (b) shim by adding a `loguru` handler
  that consumes structlog output. Pure-library callers should not need to
  configure logging at all.
- **Settings duplication** — `macropulse.config.Settings` and the
  dashboard's pydantic-settings both want `DISCORD_WEBHOOK_URL`. If
  embedding, instantiate `TruthSocialProvider` / `NitterProvider` directly
  with explicit args from your host config; don't load `macropulse.Settings`
  at all.

### What stays in `macropulse-spx`

- `runner.main()` and `__main__.py` — only relevant if you run as a separate
  daemon. Skip when embedding.
- `DiscordAlerter` — useful standalone; redundant if your host already has
  a Discord sink.
- `Settings` / `.env` loading — unused when embedding.

The pieces worth importing are `providers/`, `signal.py`, `dedup.py`, and
`models.py`. Everything else is glue that the host can replace.

### Side-by-side daemon mode (zero code changes)

If you just want the alerts in a Discord channel without touching the
dashboard, run macropulse as its own systemd service pointed at a separate
webhook URL (the dashboard's webhooks live in its `.env`; create a new
webhook in the same Discord channel, or in a dedicated `#macropulse-alerts`
channel). The two services will not collide — different SQLite paths,
different processes, different webhooks.
