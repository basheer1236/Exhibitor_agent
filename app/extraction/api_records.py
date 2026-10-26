from typing import Any

from app.extraction.normalizer import normalize_api_record
from app.models.exhibitor import ExhibitorRecord


COMMON_COLLECTION_KEYS = [
    "data",
    "results",
    "hits",
    "items",
    "records",
    "exhibitors",
    "companies",
]


NON_EXHIBITOR_SCHEMA_TYPES = {
    "listitem", "breadcrumblist", "website", "event",
    "postaladdress", "contactpoint", "geocoordinates",
    "webpage", "imageobject", "readaction", "searchaction",
}

NON_EXHIBITOR_KEYS = {
    "breadcrumb", "breadcrumbs", "breadcrumb_schema", "itemlistelement",
    "jumplabels", "jump_labels", "filters", "facets", "categories", "tags",
    "aggregations", "sort_options", "sortoptions", "pagination", "labels",
}


def _is_exhibitor_dict_list(dicts: list[dict]) -> bool:
    if not dicts:
        return False
    # If all items are schema.org navigation/metadata types (like ListItem in BreadcrumbList)
    if all(str(d.get("@type", "")).lower() in NON_EXHIBITOR_SCHEMA_TYPES for d in dicts):
        return False
    # If all items only contain jump label / filter metadata fields
    filter_only_keys = {"hasresults", "label", "id", "sortkey", "count", "selected"}
    if all(set(k.lower() for k in d.keys()).issubset(filter_only_keys) for d in dicts):
        return False
    return True


def find_record_collection(
    payload: Any,
) -> list[dict]:

    # Case 1:
    # API directly returns a list
    if isinstance(payload, list):
        direct = [
            item
            for item in payload
            if isinstance(item, dict)
        ]
        if direct and _is_exhibitor_dict_list(direct):
            return direct

        # Handle nested lists / multi-result datasets: e.g. [[{...}, {...}], []]
        candidate_lists = [
            sub for sub in payload
            if isinstance(sub, list) and any(isinstance(x, dict) for x in sub)
        ]
        if candidate_lists:
            best_list = max(candidate_lists, key=lambda s: sum(1 for x in s if isinstance(x, dict)))
            res = [item for item in best_list if isinstance(item, dict)]
            if _is_exhibitor_dict_list(res):
                return res

    # Case 2:
    # API returns an object containing a list
    if isinstance(payload, dict):

        for key in COMMON_COLLECTION_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                dicts = [item for item in value if isinstance(item, dict)]
                if dicts and _is_exhibitor_dict_list(dicts):
                    return dicts

        # Check common wrapper keys (e.g. payload["result"]["hits"])
        for wrap_key in ["result", "data", "response", "content", "payload"]:
            sub = payload.get(wrap_key)
            if isinstance(sub, dict):
                for key in COMMON_COLLECTION_KEYS:
                    value = sub.get(key)
                    if isinstance(value, list):
                        dicts = [item for item in value if isinstance(item, dict)]
                        if dicts and _is_exhibitor_dict_list(dicts):
                            return dicts

        # Fallback: inspect any key containing a list of dicts
        for key, value in payload.items():
            if str(key).lower() in NON_EXHIBITOR_KEYS:
                continue
            if isinstance(value, list):
                dicts = [item for item in value if isinstance(item, dict)]
                if dicts and _is_exhibitor_dict_list(dicts):
                    return dicts
            elif isinstance(value, dict):
                sub_dicts = find_record_collection(value)
                if sub_dicts:
                    return sub_dicts

        # Case 3: API returns a single exhibitor object (e.g. detail endpoint)
        EXHIBITOR_FIELD_SIGNALS = {
            "company_name", "companyname", "company", "exhibitor_name",
            "exhibitorname", "exhibitor", "urn", "stall_number", "stallnumber",
            "booth_number", "boothnumber", "hall_number", "hallnumber",
        }
        for candidate in [payload.get("data"), payload.get("item"), payload.get("result"), payload]:
            if isinstance(candidate, dict):
                cand_keys = {k.lower() for k in candidate.keys()}
                if any(k in cand_keys for k in EXHIBITOR_FIELD_SIGNALS):
                    return [candidate]

    return []


def extract_api_records(
    payload: Any,
    source_url: str,
) -> list[ExhibitorRecord]:

    records = find_record_collection(payload)

    return [
        normalize_api_record(
            record,
            source_url,
        )
        for record in records
    ]