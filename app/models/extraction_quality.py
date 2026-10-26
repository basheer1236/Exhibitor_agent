from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class QualityStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    LOW_QUALITY = "LOW_QUALITY"
    FAILED = "FAILED"


class ExtractionQuality(BaseModel):
    records_extracted: int = 0
    valid_records: int = 0
    invalid_records: int = 0
    duplicates_removed: int = 0

    company_name_coverage: float = 0.0
    detail_url_coverage: float = 0.0
    website_coverage: float = 0.0
    email_coverage: float = 0.0

    pagination_completeness: float = 1.0
    quality_score: float = 0.0
    quality_status: QualityStatus = QualityStatus.FAILED

    status_reason: Optional[str] = None
    validation_issues: list[str] = Field(default_factory=list)
