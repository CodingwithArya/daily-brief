from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Item:
    title: str
    url: str
    source: str
    section: str
    kind: str = "article"          # article | video | podcast | paper | event
    published: datetime | None = None
    summary: str = ""
    duration_min: int | None = None
    popularity: float = 0.0        # HN points, HF upvotes, ... (normalized later)
    weight: float = 1.0            # source weight
    extra: dict = field(default_factory=dict)
    score: float = 0.0
    why: str = ""                  # optional LLM "why it matters"
    also_sections: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        key = (self.url or self.title).split("?utm")[0].rstrip("/").lower()
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def age_hours(self, now: datetime | None = None) -> float:
        if not self.published:
            return 24.0
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.published).total_seconds() / 3600)
