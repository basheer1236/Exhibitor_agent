from typing import Optional
from app.models.exhibitor import ExhibitorRecord
from app.extraction.normalizer import (
    normalize_company_name,
    normalize_url,
    normalize_string,
)


def _get_identity_keys(record: ExhibitorRecord):
    """
    Extract normalized identity keys in priority order:
    1. exhibitor_id
    2. normalized detail_url
    3. normalized website + normalized company_name
    4. exact normalized company_name (fallback)
    """
    eid = normalize_string(record.exhibitor_id)
    if eid:
        eid = eid.lower()

    durl = normalize_url(record.detail_url) if record.detail_url else None
    if durl:
        durl = durl.rstrip("/").lower()

    norm_name = normalize_company_name(record.company_name).lower()
    
    web = normalize_url(record.website) if record.website else None
    web_key = (web.rstrip("/").lower(), norm_name) if web and norm_name else None

    fallback_name = norm_name if norm_name else None

    return eid, durl, web_key, fallback_name


def deduplicate_with_stats(
    records: list[ExhibitorRecord],
) -> tuple[list[ExhibitorRecord], int]:
    """
    Deduplicate records using deterministic identity priority:
    1. exhibitor_id
    2. normalized detail_url
    3. normalized website + company_name
    4. exact normalized company_name as a weaker fallback

    Returns (unique_records, duplicates_removed).
    """
    seen_ids = set()
    seen_detail_urls = set()
    seen_web_names = set()
    seen_fallback_names = set()

    unique_records = []
    duplicates_removed = 0

    for record in records:
        eid, durl, web_key, fallback_name = _get_identity_keys(record)

        is_duplicate = False

        # Priority 1: exhibitor_id
        if eid and eid in seen_ids:
            is_duplicate = True

        # Priority 2: normalized detail_url
        elif durl and durl in seen_detail_urls:
            is_duplicate = True

        # Priority 3: normalized website + company_name
        elif web_key and web_key in seen_web_names:
            is_duplicate = True

        # Priority 4: exact normalized company_name as a weaker fallback
        elif not eid and not durl and not web_key and fallback_name and fallback_name in seen_fallback_names:
            is_duplicate = True

        if is_duplicate:
            duplicates_removed += 1
            continue

        # Register identities for the new unique record
        if eid:
            seen_ids.add(eid)
        if durl:
            seen_detail_urls.add(durl)
        if web_key:
            seen_web_names.add(web_key)
        if not eid and not durl and not web_key and fallback_name:
            seen_fallback_names.add(fallback_name)

        unique_records.append(record)

    return unique_records, duplicates_removed


def deduplicate_records(
    records: list[ExhibitorRecord],
) -> list[ExhibitorRecord]:
    """Backwards-compatible interface returning only unique records."""
    unique_records, _ = deduplicate_with_stats(records)
    return unique_records