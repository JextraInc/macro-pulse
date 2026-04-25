from datetime import UTC, datetime

import pytest

from macropulse.models import Post
from macropulse.signal import evaluate, evaluate_directional


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
BULLISH = ["deal", "agreement", "ceasefire", "rate cut", "breakthrough"]
BEARISH = ["missile", "blockade", "retaliation", "embargo"]


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


def test_bearish_default_direction_set_on_legacy_evaluate():
    """Existing `evaluate` is unchanged in behavior, but the Alert's
    direction field is now populated as 'bearish' (was effectively bearish
    before; now explicit)."""
    alert = evaluate(_post("blockade missile war devastating"), ["blockade"], -0.5)
    assert alert is not None
    assert alert.direction == "bearish"


# --- evaluate_directional ---


def test_directional_bearish_match_fires_with_bearish_tag():
    alert = evaluate_directional(
        _post("missile devastation, terrible attack on innocent civilians"),
        bullish_keywords=BULLISH,
        bearish_keywords=BEARISH,
        bullish_threshold=0.5,
        bearish_threshold=-0.5,
    )
    assert alert is not None
    assert alert.direction == "bearish"
    assert alert.matched_keyword == "missile"
    assert alert.compound_score <= -0.5


def test_directional_bullish_match_fires_with_bullish_tag():
    alert = evaluate_directional(
        _post("Wonderful breakthrough deal signed today, fantastic news for all!"),
        bullish_keywords=BULLISH,
        bearish_keywords=BEARISH,
        bullish_threshold=0.5,
        bearish_threshold=-0.5,
    )
    assert alert is not None
    assert alert.direction == "bullish"
    assert alert.matched_keyword in {"deal", "breakthrough"}
    assert alert.compound_score >= 0.5


def test_directional_neutral_post_returns_none():
    """A post in the neutral band (between thresholds) doesn't fire even with keyword."""
    alert = evaluate_directional(
        _post("a deal exists somewhere"),  # neutral wording, no emotional valence
        bullish_keywords=BULLISH,
        bearish_keywords=BEARISH,
    )
    assert alert is None


def test_directional_keyword_only_returns_none():
    """A keyword without enough sentiment doesn't fire either side."""
    # No keyword from either list
    alert = evaluate_directional(
        _post("Wonderful great fantastic terrific amazing extraordinary!"),
        bullish_keywords=BULLISH,
        bearish_keywords=BEARISH,
    )
    assert alert is None  # bullish sentiment but no bullish keyword


def test_directional_bearish_wins_when_both_could_match():
    """Edge case: very negative content, but post mentions both kinds of keyword.
    Bearish takes precedence (risk first).
    """
    # Use a post that has both 'deal' and 'missile' but is overall negative.
    alert = evaluate_directional(
        _post("missile retaliation, deal collapsed, devastating awful catastrophe"),
        bullish_keywords=BULLISH,
        bearish_keywords=BEARISH,
        bullish_threshold=0.5,
        bearish_threshold=-0.5,
    )
    assert alert is not None
    assert alert.direction == "bearish"


def test_directional_word_boundary_holds_on_bullish_side_too():
    alert = evaluate_directional(
        _post("Wonderful idealistic vision today, fantastic, beautiful, amazing!"),
        # 'deal' would substring-match 'idealistic' without word boundary
        bullish_keywords=["deal"],
        bearish_keywords=[],
    )
    assert alert is None


def test_directional_empty_bullish_list_only_fires_bearish():
    alert = evaluate_directional(
        _post("missile attack, awful devastation"),
        bullish_keywords=[],
        bearish_keywords=BEARISH,
    )
    assert alert is not None
    assert alert.direction == "bearish"


def test_directional_empty_bearish_list_only_fires_bullish():
    alert = evaluate_directional(
        _post("Wonderful breakthrough deal, fantastic, beautiful agreement!"),
        bullish_keywords=BULLISH,
        bearish_keywords=[],
    )
    assert alert is not None
    assert alert.direction == "bullish"
