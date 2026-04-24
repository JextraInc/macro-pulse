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
