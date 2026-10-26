from app.guardrails.url import SecurityError


class BrowserError(Exception):
    """Base exception for all browser layer errors."""
    pass


class BrowserUnavailableError(BrowserError):
    """Raised when Playwright or Chromium binary is not installed or available."""
    pass


class BrowserNavigationError(BrowserError):
    """Raised when browser navigation fails."""
    pass


class BrowserTimeoutError(BrowserError):
    """Raised when browser navigation or rendering exceeds timeout."""
    pass


class BrowserSecurityError(BrowserError, SecurityError):
    """Raised when a browser navigation, redirect, or subresource violates security policy."""
    pass


class BrowserRequestLimitError(BrowserError):
    """Raised when browser requests exceed the configured request budget."""
    pass


class BrowserPageSizeError(BrowserError):
    """Raised when rendered HTML size exceeds the configured maximum limit."""
    pass
