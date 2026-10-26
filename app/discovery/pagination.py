import math
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs


PAGINATION_PARAMETERS = [
    "page",
    "p",
    "pageNumber",
    "pageIndex",
    "offset",
    "start",
]

PATH_KEYWORD_REGEX = re.compile(
    r"(.*?/(?:page|p|pg|pagination)[/-])(\d+)(/?.*)",
    re.IGNORECASE
)


def detect_pagination(
    url: str,
    links: list[str],
    html: Optional[str] = None,
) -> dict:

    signals = []

    # Check the current URL
    parsed = urlparse(url)
    base_host = parsed.netloc.lower()
    curr_path = parsed.path.rstrip("/")

    query_params = parse_qs(
        parsed.query
    )

    for parameter in PAGINATION_PARAMETERS:
        if parameter in query_params:
            vals = query_params[parameter]
            if any(v.isdigit() for v in vals):
                sig = {
                    "type": "query_parameter",
                    "parameter": parameter
                }
                if parameter == "offset":
                    sig["step"] = 50
                signals.append(sig)

    # Path-based pagination tracking
    path_pagination_groups: dict[str, list[tuple[int, str]]] = {}

    # Check if the current URL itself already has path pagination
    m_curr = PATH_KEYWORD_REGEX.search(parsed.path)
    if m_curr:
        prefix, num_str, suffix = m_curr.group(1), m_curr.group(2), m_curr.group(3)
        pattern = f"{prefix}{{page}}{suffix}"
        path_pagination_groups[pattern] = [(int(num_str), url)]

    # Check links on the page (only same-host or relative links)
    for link in links:
        parsed_link = urlparse(link)
        link_host = parsed_link.netloc.lower()

        # Skip links belonging to external domains (e.g. tracking links, search engines, sponsors)
        if link_host and base_host and link_host != base_host:
            continue

        link_query_params = parse_qs(
            parsed_link.query
        )

        for parameter in PAGINATION_PARAMETERS:
            if parameter in link_query_params:
                vals = link_query_params[parameter]
                # Pagination parameter value must be numeric
                if any(v.isdigit() for v in vals):
                    signals.append({
                        "type": "link_parameter",
                        "parameter": parameter,
                        "url": link
                    })

        # Check path-based pagination
        link_path = parsed_link.path
        m = PATH_KEYWORD_REGEX.search(link_path)
        if m:
            prefix, num_str, suffix = m.group(1), m.group(2), m.group(3)
            pattern = f"{prefix}{{page}}{suffix}"
            page_num = int(num_str)
            if pattern not in path_pagination_groups:
                path_pagination_groups[pattern] = []
            path_pagination_groups[pattern].append((page_num, link))
        elif curr_path and curr_path != "/":
            m2 = re.match(re.escape(curr_path) + r"/(\d+)(/?.*)", link_path)
            if m2:
                num_str, suffix = m2.group(1), m2.group(2)
                pattern = f"{curr_path}/{{page}}{suffix}"
                page_num = int(num_str)
                if pattern not in path_pagination_groups:
                    path_pagination_groups[pattern] = []
                path_pagination_groups[pattern].append((page_num, link))

    # Check embedded JavaScript / HTML pagination configurations (e.g. A2Z Events, jsPaginator, ajaxParams)
    if html:
        limit_val = None
        offset_val = None
        m_ajax = re.search(r"ajaxParams\s*=\s*\{([^}]+)\}", html, re.DOTALL)
        if m_ajax:
            block = m_ajax.group(1)
            m_lim = re.search(r"['\"]limit['\"]\s*:\s*['\"]?(\d+)['\"]?", block)
            if m_lim:
                limit_val = int(m_lim.group(1))
            m_off = re.search(r"['\"]offset['\"]\s*:\s*['\"]?(\d+)['\"]?", block)
            if m_off:
                offset_val = int(m_off.group(1))

        # Check total count from jsPaginator call or total count indicators
        total_count = None
        m_pag = re.search(r"jsPaginator\s*\([^;]+?,\s*(\d+)\s*\)", html)
        if m_pag:
            total_count = int(m_pag.group(1))
        else:
            m_tot = re.search(r"['\"]?(?:total(?:_items|_count|Count|Records)?|count)['\"]?\s*[:=]\s*['\"]?(\d+)['\"]?", html)
            if m_tot:
                val = int(m_tot.group(1))
                if val > 50:
                    total_count = val

        if limit_val or offset_val:
            step = limit_val or offset_val or 50
            total_pages = math.ceil(total_count / step) if total_count and step else None
            existing_offset = next((s for s in signals if s.get("parameter") == "offset"), None)
            if existing_offset:
                existing_offset["step"] = step
                if total_pages:
                    existing_offset["total_pages"] = total_pages
                if total_count:
                    existing_offset["total_count"] = total_count
            else:
                signals.append({
                    "type": "query_parameter",
                    "parameter": "offset",
                    "step": step,
                    "total_count": total_count,
                    "total_pages": total_pages,
                })

    for pattern, items in path_pagination_groups.items():
        page_nums = [item[0] for item in items]
        max_page = max(page_nums) if page_nums else None
        sample_url = items[0][1] if items else None
        signals.append({
            "type": "path_parameter",
            "parameter": f"path:{pattern}",
            "path_pattern": pattern,
            "max_page": max_page,
            "total_pages": max_page,
            "url": sample_url,
        })

    return {
        "has_pagination": len(signals) > 0,
        "signals": signals
    }
