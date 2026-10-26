from typing import Optional
from pydantic import BaseModel, Field


class ExhibitorRecord(BaseModel):
    exhibitor_id: Optional[str] = None

    company_name: str

    description: Optional[str] = None

    website: Optional[str] = None
    email: Optional[str] = None
    emails: list[str] = Field(default_factory=list)
    phone: Optional[str] = None
    phones: list[str] = Field(default_factory=list)

    address: Optional[str] = None

    country: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None

    booth: Optional[str] = None
    hall: Optional[str] = None

    category: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)

    detail_url: Optional[str] = None

    source_url: str

    confidence: float = 1.0