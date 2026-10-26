import time
from typing import Optional, Any
from urllib.parse import urlparse, urljoin

from app.browser.errors import (
    BrowserUnavailableError,
    BrowserNavigationError,
    BrowserTimeoutError,
    BrowserSecurityError,
    BrowserPageSizeError,
    BrowserRequestLimitError,
)
from app.browser.models import BrowserRenderResult
from app.discovery.http import DEFAULT_MAX_RESPONSE_SIZE
from app.guardrails.url import (
    validate_url,
    resolve_and_validate_host,
    SecurityError,
)
from app.models.scrape_budget import ScrapeBudget


DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 "
    "(compatible; UniversalExhibitorAgent/1.0)"
)


class BrowserClient:
    """
    Controlled headless Chromium rendering client using Playwright.
    Enforces SSRF validation on initial navigation, redirects, and subresources,
    request budgets, page size ceilings, and deterministic cleanup.
    """

    def __init__(
        self,
        headless: bool = True,
        user_agent: str = DEFAULT_BROWSER_USER_AGENT,
    ):
        self.headless = headless
        self.user_agent = user_agent

    def render(
        self,
        url: str,
        budget: Optional[ScrapeBudget] = None,
        max_size_bytes: int = DEFAULT_MAX_RESPONSE_SIZE,
    ) -> BrowserRenderResult:
        """
        Render a web page in headless Chromium and return the hydrated DOM.
        """
        start_time = time.perf_counter()

        if budget is None:
            budget = ScrapeBudget()

        # --------------------------------------------------
        # 1. Security validation on initial URL
        # --------------------------------------------------
        try:
            validated_url = validate_url(url)
            parsed = urlparse(validated_url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            resolve_and_validate_host(parsed.hostname, port)
        except SecurityError as exc:
            raise BrowserSecurityError(f"Initial navigation URL blocked: {exc}") from exc

        # --------------------------------------------------
        # 2. Lazy load Playwright
        # --------------------------------------------------
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
        except ImportError as exc:
            raise BrowserUnavailableError(
                "Playwright is not installed. Install with 'pip install playwright' "
                "and 'python -m playwright install chromium'."
            ) from exc

        playwright_ctx = None
        browser = None
        context = None
        page = None

        errors: list[str] = []
        intercepted_apis: list[tuple] = []
        requests_made = 0
        redirects_followed = 0
        status_code = None
        final_url = validated_url
        security_violation: Optional[BrowserSecurityError] = None

        max_requests = budget.max_requests
        if max_requests is not None and max_requests <= 0:
            raise BrowserRequestLimitError("Browser request budget is zero or exhausted.")

        try:
            try:
                playwright_ctx = sync_playwright().start()
                browser = playwright_ctx.chromium.launch(
                    headless=self.headless,
                    args=["--disable-dev-shm-usage", "--no-sandbox"],
                )
            except Exception as exc:
                raise BrowserUnavailableError(
                    f"Failed to launch Chromium browser: {exc}"
                ) from exc

            context = browser.new_context(
                user_agent=self.user_agent,
                ignore_https_errors=False,
            )
            page = context.new_page()

            # --------------------------------------------------
            # 3. Request interception & routing (SSRF + budget)
            # --------------------------------------------------
            def handle_route(route):
                nonlocal requests_made, security_violation
                req = route.request
                req_url = req.url
                res_type = req.resource_type

                # Resource control: block heavy non-essential media
                if res_type in {"image", "media", "font"}:
                    try:
                        route.abort("blockedbyclient")
                    except Exception:
                        pass
                    return

                # Allow internal browser schemes
                parsed_req = urlparse(req_url)
                if parsed_req.scheme in {"data", "about", "blob"}:
                    try:
                        route.continue_()
                    except Exception:
                        pass
                    return

                # Security validation on subresources and redirect navigation
                try:
                    validate_url(req_url)
                    sub_port = parsed_req.port or (443 if parsed_req.scheme == "https" else 80)
                    resolve_and_validate_host(parsed_req.hostname, sub_port)
                except Exception as sec_exc:
                    if req.is_navigation_request():
                        security_violation = BrowserSecurityError(
                            f"Navigation to unsafe destination blocked: {req_url} ({sec_exc})"
                        )
                    errors.append(f"Blocked unsafe subresource: {req_url} ({sec_exc})")
                    try:
                        route.abort("blockedbyclient")
                    except Exception:
                        pass
                    return

                # Enforce request budget
                if max_requests is not None and requests_made >= max_requests:
                    errors.append(f"Browser request budget exhausted ({max_requests})")
                    try:
                        route.abort("blockedbyclient")
                    except Exception:
                        pass
                    return

                requests_made += 1
                try:
                    route.continue_()
                except Exception:
                    pass

            page.route("**/*", handle_route)

            # --------------------------------------------------
            # 4. Response & Redirect tracking
            # --------------------------------------------------
            def handle_response(response):
                nonlocal status_code, final_url, redirects_followed, security_violation
                try:
                    if response.request.is_navigation_request():
                        status_code = response.status
                        final_url = response.url

                        if response.status in {301, 302, 303, 307, 308}:
                            redirects_followed += 1
                            loc = response.headers.get("location")
                            if loc:
                                target = urljoin(response.url, loc)
                                try:
                                    validate_url(target)
                                    p = urlparse(target)
                                    resolve_and_validate_host(p.hostname, p.port or 80)
                                except Exception as redir_exc:
                                    security_violation = BrowserSecurityError(
                                        f"Redirect to unsafe destination blocked: {target} ({redir_exc})"
                                    )
                    elif response.request.resource_type in {"xhr", "fetch"} and response.status == 200:
                        ct = response.headers.get("content-type", "").lower()
                        if "json" in ct:
                            try:
                                json_data = response.json()
                                req_hdrs = {}
                                try:
                                    req_hdrs = dict(response.request.headers)
                                except Exception:
                                    pass
                                intercepted_apis.append((response.url, json_data, req_hdrs))
                            except Exception:
                                pass
                except BrowserSecurityError as b_sec:
                    security_violation = b_sec
                except Exception:
                    pass

            page.on("response", handle_response)

            # --------------------------------------------------
            # 5. Bounded navigation
            # --------------------------------------------------
            timeout_ms = int(budget.timeout_seconds * 1000)

            try:
                page.goto(
                    validated_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                if security_violation:
                    raise security_violation
                # Controlled wait for client-side JS framework rendering / SPA hydration
                try:
                    idle_timeout = min(8000, timeout_ms)
                    page.wait_for_load_state("networkidle", timeout=idle_timeout)
                except Exception:
                    pass

                # If directory elements are not yet present and no exhibitor API intercepted, wait for directory selectors
                has_api = any(
                    "buyer" in u.lower() or "exhibitor" in u.lower() or "member" in u.lower() or "company" in u.lower()
                    for u, *rest in intercepted_apis
                )
                if not has_api:
                    for selector in ["article", "[class*='card']", "[class*='exhibitor']", "table tr:nth-child(2)"]:
                        try:
                            page.wait_for_selector(selector, timeout=4000)
                            break
                        except Exception:
                            pass

                # Short settle wait
                page.wait_for_timeout(min(1000, timeout_ms))
            except PlaywrightTimeoutError as exc:
                if security_violation:
                    raise security_violation
                raise BrowserTimeoutError(
                    f"Browser navigation timed out after {budget.timeout_seconds}s: {exc}"
                ) from exc
            except BrowserSecurityError:
                raise
            except Exception as exc:
                if security_violation:
                    raise security_violation
                err_msg = str(exc)
                if "timeout" in err_msg.lower():
                    raise BrowserTimeoutError(
                        f"Browser navigation timed out after {budget.timeout_seconds}s: {exc}"
                    ) from exc
                raise BrowserNavigationError(
                    f"Browser navigation failed for {validated_url}: {exc}"
                ) from exc

            # --------------------------------------------------
            # 6. Rendered HTML retrieval & Page size limit
            # --------------------------------------------------
            rendered_html = page.content()
            content_length = len(rendered_html.encode("utf-8", errors="replace"))

            if content_length > max_size_bytes:
                raise BrowserPageSizeError(
                    f"Rendered document size ({content_length} bytes) exceeds limit of {max_size_bytes} bytes"
                )

            final_url = page.url or final_url
            duration = time.perf_counter() - start_time

            return BrowserRenderResult(
                requested_url=url,
                final_url=final_url,
                status_code=status_code,
                rendered_html=rendered_html,
                requests_made=requests_made,
                redirects_followed=redirects_followed,
                content_length=content_length,
                duration=duration,
                errors=errors,
                intercepted_apis=intercepted_apis,
            )

        finally:
            # --------------------------------------------------
            # 7. Guaranteed lifecycle cleanup
            # --------------------------------------------------
            if page:
                try:
                    page.close()
                except Exception:
                    pass
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright_ctx:
                try:
                    playwright_ctx.stop()
                except Exception:
                    pass
