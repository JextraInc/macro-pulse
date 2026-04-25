from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PostSource = Literal["truthsocial", "nitter", "replay"]
Direction = Literal["bullish", "bearish"]


class Post(BaseModel):
    id: str
    author: str
    source: PostSource
    content: str
    url: str
    created_at: datetime


class Alert(BaseModel):
    post: Post
    matched_keyword: str
    compound_score: float = Field(..., le=1.0, ge=-1.0)
    # Direction defaults to "bearish" for backward compat with the original
    # `evaluate(...)` API, which only fired on bearish posts. The newer
    # `evaluate_directional(...)` sets this explicitly based on which
    # keyword list and threshold matched.
    direction: Direction = "bearish"
