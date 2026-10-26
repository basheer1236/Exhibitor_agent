from typing import List, Optional

from pydantic import BaseModel, Field


class ApiCandidate(BaseModel):
    url: str
    method: str = "GET"
    source: Optional[str] = None


class SiteProfile(BaseModel):
    url: str

    status_code: Optional[int] = None
    content_type: Optional[str] = None
    initial_content: Optional[str] = None

    is_html: bool = False
    is_json: bool = False

    # Iframe detection
    has_iframe: bool = False
    iframe_urls: List[str] = Field(default_factory=list)

    # API detection
    has_api_candidates: bool = False
    api_candidates: List[ApiCandidate] = Field(
        default_factory=list
    )

    # Pagination detection
    has_pagination: bool = False
    pagination_signals: List[dict] = Field(
        default_factory=list
    )

    # Directory structure
    has_search: bool = False
    has_detail_links: bool = False
    detail_links: List[str] = Field(
        default_factory=list
    )

    # Repeated HTML structures
    has_repeated_elements: bool = False
    repeated_elements: List[dict] = Field(
        default_factory=list
    )

    # Browser fallback
    javascript_required: bool = False

    # General classification
    site_type: Optional[str] = None