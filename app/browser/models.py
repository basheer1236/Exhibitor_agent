from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class BrowserRenderResult:
    """Represents the rendered output and metadata of a browser navigation session."""
    requested_url: str
    final_url: str
    status_code: Optional[int] = None
    rendered_html: str = ""
    requests_made: int = 0
    redirects_followed: int = 0
    content_length: int = 0
    duration: float = 0.0
    errors: list[str] = field(default_factory=list)
    intercepted_apis: list[tuple] = field(default_factory=list)

    @property
    def content(self) -> str:
        """Alias for rendered_html to match FetchResponse interface."""
        return self.rendered_html

    @property
    def ok(self) -> bool:
        """Whether the navigation completed successfully."""
        if self.status_code is None:
            return len(self.rendered_html) > 0 and len(self.errors) == 0
        return 200 <= self.status_code < 400
