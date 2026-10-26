from typing import Optional
from app.models.exhibitor import ExhibitorRecord
from app.models.extraction_quality import ExtractionQuality, QualityStatus
from app.quality.validator import (
    validate_record,
    is_valid_url_structure,
    is_valid_website_structure,
    is_valid_email_structure,
)


def evaluate_extraction_quality(
    records: list[ExhibitorRecord],
    duplicates_removed: int = 0,
    pages_scraped: int = 0,
    expected_pages: Optional[int] = None,
    expected_records: Optional[int] = None,
    budget_limited: bool = False,
    scrape_error: Optional[str] = None,
) -> ExtractionQuality:
    """
    Deterministic evaluation of extraction quality, completeness, and status.

    Scoring formula:
    - Valid records ratio: 40%
    - Company name coverage: 25%
    - Contact/Detail coverage: 15% (maximum of detail_url or website coverage)
    - Pagination completeness: 20%
    Total quality score is bounded between 0.00 and 1.00.
    """
    total_records = len(records)
    all_issues = []

    if total_records == 0:
        status_reason = scrape_error or "No records were extracted."
        return ExtractionQuality(
            records_extracted=0,
            valid_records=0,
            invalid_records=0,
            duplicates_removed=duplicates_removed,
            company_name_coverage=0.0,
            detail_url_coverage=0.0,
            website_coverage=0.0,
            email_coverage=0.0,
            pagination_completeness=0.0 if (expected_pages and expected_pages > 0) else 1.0,
            quality_score=0.0,
            quality_status=QualityStatus.FAILED,
            status_reason=status_reason,
            validation_issues=all_issues,
        )

    valid_count = 0
    invalid_count = 0
    names_present = 0
    detail_urls_present = 0
    websites_present = 0
    emails_present = 0

    for rec in records:
        is_valid, issues = validate_record(rec)
        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1
            all_issues.extend(issues)

        name_str = rec.company_name.strip() if rec.company_name else ""
        if name_str and len(name_str) >= 2 and name_str.lower() not in {"unknown", "n/a", "none", "null"}:
            names_present += 1
        if rec.detail_url and is_valid_url_structure(rec.detail_url):
            detail_urls_present += 1
        if rec.website and is_valid_website_structure(rec.website):
            websites_present += 1
        if rec.email and is_valid_email_structure(rec.email):
            emails_present += 1

    company_name_coverage = round(names_present / total_records, 4)
    detail_url_coverage = round(detail_urls_present / total_records, 4)
    website_coverage = round(websites_present / total_records, 4)
    email_coverage = round(emails_present / total_records, 4)

    # ----------------------------------------------------
    # Pagination Completeness
    # ----------------------------------------------------
    if expected_pages and expected_pages > 0:
        pagination_completeness = round(min(1.0, pages_scraped / expected_pages), 4)
    elif expected_records and expected_records > 0:
        pagination_completeness = round(min(1.0, total_records / expected_records), 4)
    elif budget_limited:
        pagination_completeness = round(min(0.8, pages_scraped / max(1, pages_scraped + 1)), 4)
    else:
        pagination_completeness = 1.0

    # ----------------------------------------------------
    # Anomaly / Duplicate / Yield Penalties
    # ----------------------------------------------------
    total_raw_records = total_records + duplicates_removed
    duplicate_ratio = duplicates_removed / total_raw_records if total_raw_records > 0 else 0.0

    duplicate_penalty = 0.0
    if duplicates_removed >= 5 and duplicate_ratio > 0.50:
        duplicate_penalty = round(min(0.35, duplicate_ratio * 0.40), 2)
        all_issues.append(
            f"High duplicate contamination: {duplicates_removed} duplicates removed "
            f"({round(duplicate_ratio * 100, 1)}% duplicate ratio), indicating possible repeated-page loop."
        )

    yield_penalty = 0.0
    if pages_scraped >= 5 and total_records <= 2:
        yield_penalty = 0.30
        all_issues.append(
            f"Suspiciously low record yield: {total_records} record(s) extracted across {pages_scraped} pages."
        )
    elif pages_scraped >= 10 and (total_records / pages_scraped) < 0.2:
        yield_penalty = 0.20
        all_issues.append(
            f"Low record yield: {total_records} records extracted across {pages_scraped} pages."
        )

    # ----------------------------------------------------
    # Quality Score (0.0 to 1.0)
    # ----------------------------------------------------
    valid_ratio = valid_count / total_records
    contact_coverage = max(detail_url_coverage, website_coverage)

    base_score = (
        valid_ratio * 0.40
        + company_name_coverage * 0.25
        + contact_coverage * 0.15
        + pagination_completeness * 0.20
    )
    quality_score = round(max(0.0, min(1.0, base_score - duplicate_penalty - yield_penalty)), 2)

    # ----------------------------------------------------
    # Quality Status Determination
    # ----------------------------------------------------
    has_critical_anomaly = (
        (duplicate_penalty >= 0.25 and yield_penalty > 0)
        or (pages_scraped >= 5 and total_records <= 2 and duplicates_removed >= 5)
    )

    is_partial = (
        budget_limited
        or (expected_pages is not None and pages_scraped < expected_pages)
        or (expected_records is not None and total_records < expected_records)
    )

    # Scale partial quality score so it does not misleadingly appear as high as a complete scrape (> 0.75)
    if is_partial and quality_score >= 0.50:
        scaled_score = round(0.50 + (base_score - duplicate_penalty - yield_penalty) * 0.25 * pagination_completeness, 2)
        quality_score = max(0.50, min(0.75, scaled_score))

    if has_critical_anomaly:
        quality_status = QualityStatus.LOW_QUALITY
        status_reason = (
            f"Suspicious extraction: {total_records} record(s) extracted across "
            f"{pages_scraped} pages with {duplicates_removed} duplicates removed."
        )
    elif is_partial and valid_count > 0 and quality_score >= 0.50:
        quality_status = QualityStatus.PARTIAL
        if budget_limited:
            status_reason = "Extraction terminated early due to scrape budget limits."
        else:
            status_reason = f"Scraped {pages_scraped} pages of {expected_pages} expected pages."
    elif total_records > 0 and (
        valid_count == 0
        or quality_score < 0.50
        or company_name_coverage < 0.60
        or valid_ratio < 0.50
        or duplicate_penalty > 0.25
    ):
        quality_status = QualityStatus.LOW_QUALITY
        status_reason = "Records extracted have missing essential fields, structural errors, or high duplicate contamination."
    elif is_partial:
        quality_status = QualityStatus.PARTIAL
        status_reason = "Extraction terminated early due to bounded limits."
    elif valid_count == 0:
        quality_status = QualityStatus.FAILED
        status_reason = "No structurally valid records extracted."
    else:
        quality_status = QualityStatus.COMPLETE
        status_reason = "Extraction completed successfully and records passed structural validation."

    return ExtractionQuality(
        records_extracted=total_records,
        valid_records=valid_count,
        invalid_records=invalid_count,
        duplicates_removed=duplicates_removed,
        company_name_coverage=company_name_coverage,
        detail_url_coverage=detail_url_coverage,
        website_coverage=website_coverage,
        email_coverage=email_coverage,
        pagination_completeness=pagination_completeness,
        quality_score=quality_score,
        quality_status=quality_status,
        status_reason=status_reason,
        validation_issues=all_issues[:50],  # Cap issues sample
    )
