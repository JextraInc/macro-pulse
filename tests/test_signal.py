from datetime import UTC, datetime

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
        created_at=datetime(2026, 4, 23, tzinfo=UTC),
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
