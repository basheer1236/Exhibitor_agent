from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.exhibitor import ExhibitorRecord
from app.models.extraction_quality import ExtractionQuality


class ScrapeResult(BaseModel):
    source_url: str
    final_url: str | None = None

    success: bool = False

    strategy: str | None = None

    records: list[ExhibitorRecord] = Field(
        default_factory=list
    )

    pages_scraped: int = 0
    requests_made: int = 0

    duplicates_removed: int = 0
    browser_used: bool = False
    fallback_reason: Optional[str] = None
    duration_seconds: float = 0.0

    quality_status: Optional[str] = None
    quality_score: float = 0.0
    quality: Optional[ExtractionQuality] = None

    errors: list[str] = Field(
        default_factory=list
    )

    started_at: datetime
    finished_at: datetime | None = None