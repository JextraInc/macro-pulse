import sqlite3
from datetime import UTC, datetime, timedelta
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
        self._insert_with_ts(post_id, datetime.now(UTC))

    def _insert_with_ts(self, post_id: str, ts: datetime) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_posts (id, seen_at) VALUES (?, ?)",
            (post_id, ts),
        )
        self._conn.commit()

    def prune(self, ttl_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=ttl_days)
        cur = self._conn.execute("DELETE FROM seen_posts WHERE seen_at < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()
