from typing import Optional
from pydantic import BaseModel


class ScrapeBudget(BaseModel):

    max_pages: int = 100

    max_requests: int = 200

    max_records: int = 10000

    timeout_seconds: int = 30

    enrich_details: bool = False

    max_detail_requests: Optional[int] = None