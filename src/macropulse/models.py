from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PostSource = Literal["truthsocial", "nitter", "replay"]


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
