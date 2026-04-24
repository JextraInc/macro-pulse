from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

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


def test_alert_rejects_out_of_range_compound_score():
    post = Post(
        id="truthsocial:1",
        author="realDonaldTrump",
        source="truthsocial",
        content="hello",
        url="https://t.example/1",
        created_at=datetime(2026, 4, 23, 17, 0, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        Alert(post=post, matched_keyword="hello", compound_score=1.5)
    with pytest.raises(ValidationError):
        Alert(post=post, matched_keyword="hello", compound_score=-1.5)
