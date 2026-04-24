from datetime import UTC, datetime

from macropulse.models import Alert, Post


def test_post_roundtrips():
    post = Post(
        id="truthsocial:1",
        author="realDonaldTrump",
        source="truthsocial",
        content="hello",
        url="https://truthsocial.com/@realDonaldTrump/1",
        created_at=datetime(2026, 4, 23, 17, 0, tzinfo=UTC),
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
        created_at=datetime(2026, 4, 23, 17, 0, tzinfo=UTC),
    )
    alert = Alert(post=post, matched_keyword="hello", compound_score=-0.75)
    assert alert.post.id == "truthsocial:1"
    assert alert.compound_score == -0.75
