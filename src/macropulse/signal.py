import re
from functools import lru_cache

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore[import-untyped]

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
