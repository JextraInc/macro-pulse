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


def _first_match(text_lower: str, keywords: list[str]) -> str | None:
    for kw in keywords:
        if _compiled(kw).search(text_lower):
            return kw
    return None


def evaluate(post: Post, keywords: list[str], threshold: float) -> Alert | None:
    """Return an Alert iff a keyword matches AND VADER compound < threshold.

    Bearish-only filter (legacy API). Use `evaluate_directional` to also
    surface bullish posts.

    - Multi-word keywords use substring match on lowercased content.
    - Single-word keywords use word boundaries to avoid "mining" matching "examining".
    - On multiple keyword matches, the first in list order wins (deterministic).
    """
    matched = _first_match(post.content.lower(), keywords)
    if matched is None:
        return None

    compound = _analyzer().polarity_scores(post.content)["compound"]
    if compound >= threshold:
        return None
    return Alert(
        post=post,
        matched_keyword=matched,
        compound_score=compound,
        direction="bearish",
    )


def evaluate_directional(
    post: Post,
    bullish_keywords: list[str],
    bearish_keywords: list[str],
    bullish_threshold: float = 0.5,
    bearish_threshold: float = -0.5,
) -> Alert | None:
    """Return an Alert tagged "bullish" or "bearish" if either side matches.

    A post fires bearish iff:
        any bearish keyword matches AND VADER compound <= bearish_threshold.
    A post fires bullish iff:
        any bullish keyword matches AND VADER compound >= bullish_threshold.

    Bearish wins ties — risk signals trump opportunity signals. In practice
    the two thresholds bound disjoint compound bands (e.g. -0.5 / +0.5)
    so a single post lands in at most one direction.

    Returns None if neither side fires. The alert's `direction` field is
    set to the side that matched.
    """
    text_lower = post.content.lower()
    compound = _analyzer().polarity_scores(post.content)["compound"]

    if compound <= bearish_threshold:
        matched = _first_match(text_lower, bearish_keywords)
        if matched is not None:
            return Alert(
                post=post,
                matched_keyword=matched,
                compound_score=compound,
                direction="bearish",
            )

    if compound >= bullish_threshold:
        matched = _first_match(text_lower, bullish_keywords)
        if matched is not None:
            return Alert(
                post=post,
                matched_keyword=matched,
                compound_score=compound,
                direction="bullish",
            )

    return None
