from datetime import UTC, datetime, timedelta

from macropulse.dedup import SeenStore


def test_roundtrip_memory():
    store = SeenStore(":memory:")
    assert store.has_seen("a") is False
    store.mark_seen("a")
    assert store.has_seen("a") is True


def test_prune_removes_old_rows():
    store = SeenStore(":memory:")
    old = datetime.now(UTC) - timedelta(days=30)
    fresh = datetime.now(UTC)
    store._insert_with_ts("old", old)
    store._insert_with_ts("fresh", fresh)
    store.prune(ttl_days=7)
    assert store.has_seen("old") is False
    assert store.has_seen("fresh") is True


def test_dedup_creates_file(tmp_path):
    db = tmp_path / "nested" / "seen.db"
    store = SeenStore(db)
    store.mark_seen("a")
    assert db.exists()
    store.close()
