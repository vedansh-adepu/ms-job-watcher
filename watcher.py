import json
import os
import argparse
import ssl
import smtplib
import csv
import re
from email.mime.text import MIMEText
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple

import requests

# -----------------------------
# High-level design
# -----------------------------
# Each company is a "source" (adapter) that knows how to:
#   1) fetch raw jobs from an endpoint
#   2) normalize them into a common shape
# We then:
#   - filter by title keywords
#   - diff vs seen_ids (state)
#   - send one digest email per run
#   - update state

STATE_PATH = "state/seen.json"
BOARDS_CURSOR_PATH = "state/boards_cursor.json"

# ---- EMAIL ENV VARS ----
EMAIL_USER = os.getenv("EMAIL_USER")  # sender gmail
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")  # gmail app password
ALERT_TO_EMAIL = os.getenv("ALERT_TO_EMAIL")  # receiver email

# We match loosely so we don't accidentally miss roles.
INCLUDE_TITLE_KEYWORDS = [
    "software engineer",
    "software development engineer",
    "sde",
    "backend",
    "full stack",
    "platform",
    "machine learning engineer",
    "ml engineer",
    "data engineer",
    "applied scientist",
    "analytics engineer",
]

# ---- Polling caps (because we run frequently) ----
MAX_EIGHTFOLD_JOBS_PER_RUN = int(os.getenv("MAX_EIGHTFOLD_JOBS_PER_RUN", "300"))
MAX_AMZ_JOBS_PER_RUN = int(os.getenv("MAX_AMZ_JOBS_PER_RUN", "300"))
MAX_ORACLE_JOBS_PER_RUN = int(os.getenv("MAX_ORACLE_JOBS_PER_RUN", "200"))
MAX_IBM_JOBS_PER_RUN = int(os.getenv("MAX_IBM_JOBS_PER_RUN", "200"))
MAX_GS_JOBS_PER_RUN = int(os.getenv("MAX_GS_JOBS_PER_RUN", "200"))

# Amazon supports a larger page size; using 50 reduces request count.
AMZ_RESULT_LIMIT = int(os.getenv("AMZ_RESULT_LIMIT", "50"))

# -----------------------------
# Source configuration
# -----------------------------

# A) Eightfold PCS sources (Microsoft + NVIDIA)
EIGHTFOLD_SOURCES: Dict[str, Dict[str, Any]] = {
    "microsoft": {
        "company": "Microsoft",
        "endpoint": "https://apply.careers.microsoft.com/api/pcsx/search",
        "base_url": "https://apply.careers.microsoft.com",
        "default_search_url": "https://apply.careers.microsoft.com/careers",
        "params": {
            "domain": "microsoft.com",
            "query": "",
            "location": "United States, Multiple Locations, Multiple Locations",
            "start": 0,
            "sort_by": "timestamp",
            "filter_include_remote": 1,
            # You said you're OK including senior/staff in fetch results and filtering later.
            # Keeping these is fine; remove if you want everything.
            "filter_seniority": ["Entry", "Mid-Level"],
        },
    },
    "nvidia": {
        "company": "NVIDIA",
        "endpoint": "https://nvidia.eightfold.ai/api/pcsx/search",
        "base_url": "https://nvidia.eightfold.ai",
        "default_search_url": "https://nvidia.eightfold.ai/careers",
        "params": {
            "domain": "nvidia.com",
            "query": "",
            "location": "united states",
            "start": 0,
            "sort_by": "timestamp",
            "filter_include_remote": 1,
            "filter_job_category": "engineering",
            "filter_job_type": "regular employee",
            "filter_time_type": "full time",
            # repeated query params; requests supports list
            "filter_hiring_title": [
                "Software Engineer",
                "Machine Learning Engineer",
                "Artificial Intelligence",
                "machine learning",
                "software development engineer",
                "data",
            ],
        },
    },
}

# B) Amazon (GET JSON)
AMZ_ENDPOINT = "https://www.amazon.jobs/en/search.json"
AMZ_PARAMS = {
    "category[]": ["machine-learning-science", "software-development"],
    "schedule_type_id[]": ["Full-Time"],
    "normalized_country_code[]": ["USA"],
    "radius": "100000km",
    "offset": 0,
    "result_limit": 10,
    "sort": "recent",
    # optional geo/context fields
    "latitude": 38.89036,
    "longitude": -77.03196,
    "loc_query": "united states",
    "base_query": "",
}

# C) Goldman Sachs (GraphQL POST)
GS_ENDPOINT = "https://api-higher.gs.com/gateway/api/v1/graphql"
GS_HEADERS_MIN = {
    "accept": "application/json",
    "content-type": "application/json",
    # origin/referer can matter for some setups; keep them minimal and stable
    "origin": "https://higher.gs.com",
    "referer": "https://higher.gs.com/",
    "user-agent": "Mozilla/5.0",
}
GS_PAYLOAD: Dict[str, Any] = {
    "operationName": "GetRoles",
    "variables": {
        "searchQueryInput": {
            "page": {"pageSize": 20, "pageNumber": 0},
            "sort": {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"},
            "filters": [
                {
                    "filterCategoryType": "EXPERIENCE_LEVEL",
                    "filters": [
                        {"filter": "Support", "subFilters": []},
                        {"filter": "Seasonal", "subFilters": []},
                        {"filter": "Associate", "subFilters": []},
                    ],
                },
                {
                    "filterCategoryType": "JOB_FUNCTION",
                    "filters": [{"filter": "Software Engineering", "subFilters": []}],
                },
                {
                    "filterCategoryType": "LOCATION",
                    "filters": [
                        {
                            "filter": "United States",
                            "subFilters": [
                                {
                                    "filter": "California",
                                    "subFilters": [
                                        {"filter": "Menlo Park", "subFilters": []},
                                        {"filter": "Newport Beach", "subFilters": []},
                                        {"filter": "San Francisco", "subFilters": []},
                                    ],
                                },
                                {"filter": "Delaware", "subFilters": [{"filter": "Wilmington", "subFilters": []}]},
                                {"filter": "District of Columbia", "subFilters": [{"filter": "Washington", "subFilters": []}]},
                                {"filter": "Florida", "subFilters": [{"filter": "Miami", "subFilters": []}, {"filter": "West Palm Beach", "subFilters": []}]},
                                {"filter": "Georgia", "subFilters": [{"filter": "Atlanta", "subFilters": []}]},
                                {"filter": "Illinois", "subFilters": [{"filter": "Chicago", "subFilters": []}]},
                                {"filter": "Maryland", "subFilters": [{"filter": "Baltimore", "subFilters": []}]},
                                {"filter": "Massachusetts", "subFilters": [{"filter": "Boston", "subFilters": []}]},
                                {"filter": "Michigan", "subFilters": [{"filter": "Detroit", "subFilters": []}]},
                                {"filter": "New Jersey", "subFilters": [{"filter": "Jersey City", "subFilters": []}]},
                                {"filter": "New York", "subFilters": [{"filter": "Albany", "subFilters": []}, {"filter": "New York", "subFilters": []}]},
                                {"filter": "Pennsylvania", "subFilters": [{"filter": "Philadelphia", "subFilters": []}, {"filter": "Pittsburgh", "subFilters": []}]},
                                {"filter": "Texas", "subFilters": [{"filter": "Dallas", "subFilters": []}, {"filter": "Houston", "subFilters": []}, {"filter": "Irving", "subFilters": []}, {"filter": "Richardson", "subFilters": []}]},
                                {"filter": "Utah", "subFilters": [{"filter": "Draper", "subFilters": []}, {"filter": "Salt Lake City", "subFilters": []}]},
                                {"filter": "Washington", "subFilters": [{"filter": "Seattle", "subFilters": []}]},
                            ],
                        }
                    ],
                },
            ],
            "experiences": ["EARLY_CAREER", "PROFESSIONAL"],
            "searchTerm": "",
        }
    },
    "query": "query GetRoles($searchQueryInput: RoleSearchQueryInput!) {\n  roleSearch(searchQueryInput: $searchQueryInput) {\n    totalCount\n    items {\n      roleId\n      corporateTitle\n      jobTitle\n      jobFunction\n      locations {\n        primary\n        state\n        country\n        city\n        __typename\n      }\n      status\n      division\n      skills\n      jobType {\n        code\n        description\n        __typename\n      }\n      externalSource {\n        sourceId\n        __typename\n      }\n      __typename\n    }\n    __typename\n  }\n}",
}

# D) IBM (POST JSON)
IBM_ENDPOINT = "https://www-api.ibm.com/search/api/v2"
IBM_HEADERS_MIN = {
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://www.ibm.com",
    "referer": "https://www.ibm.com/",
    "user-agent": "Mozilla/5.0",
}
IBM_PAYLOAD: Dict[str, Any] = {
    "appId": "careers",
    "scopes": ["careers2"],
    "query": {"bool": {"must": []}},
    "post_filter": {
        "bool": {
            "must": [
                {
                    "bool": {
                        "should": [
                            {"term": {"field_keyword_08": "Software Engineering"}},
                            {"term": {"field_keyword_08": "Data & Analytics"}},
                        ]
                    }
                },
                {"term": {"field_keyword_18": "Entry Level"}},
                {"term": {"field_keyword_05": "United States"}},
            ]
        }
    },
    "aggs": {
        "field_keyword_172": {
            "filter": {
                "bool": {
                    "must": [
                        {
                            "bool": {
                                "should": [
                                    {"term": {"field_keyword_08": "Software Engineering"}},
                                    {"term": {"field_keyword_08": "Data & Analytics"}},
                                ]
                            }
                        },
                        {"term": {"field_keyword_18": "Entry Level"}},
                        {"term": {"field_keyword_05": "United States"}},
                    ]
                }
            },
            "aggs": {
                "field_keyword_17": {"terms": {"field": "field_keyword_17", "size": 6}},
                "field_keyword_17_count": {"cardinality": {"field": "field_keyword_17"}},
            },
        },
        "field_keyword_083": {
            "filter": {
                "bool": {
                    "must": [
                        {"term": {"field_keyword_18": "Entry Level"}},
                        {"term": {"field_keyword_05": "United States"}},
                    ]
                }
            },
            "aggs": {
                "field_keyword_08": {"terms": {"field": "field_keyword_08", "size": 6}},
                "field_keyword_08_count": {"cardinality": {"field": "field_keyword_08"}},
            },
        },
        "field_keyword_184": {
            "filter": {
                "bool": {
                    "must": [
                        {
                            "bool": {
                                "should": [
                                    {"term": {"field_keyword_08": "Software Engineering"}},
                                    {"term": {"field_keyword_08": "Data & Analytics"}},
                                ]
                            }
                        },
                        {"term": {"field_keyword_05": "United States"}},
                    ]
                }
            },
            "aggs": {
                "field_keyword_18": {"terms": {"field": "field_keyword_18", "size": 6}},
                "field_keyword_18_count": {"cardinality": {"field": "field_keyword_18"}},
            },
        },
        "field_keyword_055": {
            "filter": {
                "bool": {
                    "must": [
                        {
                            "bool": {
                                "should": [
                                    {"term": {"field_keyword_08": "Software Engineering"}},
                                    {"term": {"field_keyword_08": "Data & Analytics"}},
                                ]
                            }
                        },
                        {"term": {"field_keyword_18": "Entry Level"}},
                    ]
                }
            },
            "aggs": {
                "field_keyword_05": {"terms": {"field": "field_keyword_05", "size": 1000}},
                "field_keyword_05_count": {"cardinality": {"field": "field_keyword_05"}},
            },
        },
    },
    "size": 30,
    "sort": [{"dcdate": "desc"}, {"_score": "desc"}],
    "lang": "zz",
    "localeSelector": {},
    "sm": {"query": "", "lang": "zz"},
    "_source": [
        "_id",
        "title",
        "url",
        "description",
        "language",
        "entitled",
        "field_keyword_17",
        "field_keyword_08",
        "field_keyword_18",
        "field_keyword_19",
    ],
}

# E) Oracle (GET)
# Keep it as a raw URL because Oracle uses a long finder=... query string.
ORACLE_URL = (
    "https://eeho.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/"
    "recruitingCEJobRequisitions"
    "?onlyData=true"
    "&expand=requisitionList.workLocation,requisitionList.otherWorkLocations,"
    "requisitionList.secondaryLocations,flexFieldsFacet.values,"
    "requisitionList.requisitionFlexFields"
    "&finder=findReqs;siteNumber=CX_45001,"
    "facetsList=LOCATIONS%3BWORK_LOCATIONS%3BWORKPLACE_TYPES%3BTITLES%3BCATEGORIES%3B"
    "ORGANIZATIONS%3BPOSTING_DATES%3BFLEX_FIELDS,"
    "limit=14,lastSelectedFacet=AttributeChar13,"
    "locationId=300000000149325,"
    "selectedCategoriesFacet=300000001559315%3B300000001917356,"
    "selectedFlexFieldsFacets=%22AttributeChar6%7C0%20to%202%2B%20years%22,"
    "selectedLocationsFacet=300000000149325,"
    "selectedPostingDatesFacet=7,sortBy=POSTING_DATES_DESC"
)
ORACLE_HEADERS_MIN = {
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://careers.oracle.com",
    "referer": "https://careers.oracle.com/",
    "user-agent": "Mozilla/5.0",
}

# Which sources we currently run.
SUPPORTED_SOURCES = ["microsoft", "amazon", "nvidia", "goldman_sachs", "ibm", "oracle"]


# -----------------------------
# State helpers
# -----------------------------

def load_seen_ids(path: str) -> Set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("seen_ids", []))


def save_seen_ids(path: str, seen_ids: Set[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "seen_ids": sorted(seen_ids),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


# -----------------------------
# Boards cursor helpers
# -----------------------------

def load_boards_cursor(path: str) -> int:
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cur = int(data.get("cursor", 0))
        return max(cur, 0)
    except Exception:
        return 0


def save_boards_cursor(path: str, cursor: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "cursor": int(max(cursor, 0)),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def title_matches(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in INCLUDE_TITLE_KEYWORDS)


# -----------------------------
# Normalized job shape helpers
# -----------------------------

def load_boards_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Boards CSV not found: {path}")

    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            company = (r.get("company_name") or r.get("company") or "").strip()
            platform = (r.get("platform") or "").strip().lower()
            url = (r.get("board_url") or r.get("url") or "").strip()
            ok_val = (r.get("ok") or "").strip().lower()

            # Allow either the original 3-col CSV OR the *_with_meta.csv with ok/status columns.
            if ok_val and ok_val not in ("true", "1", "yes"):
                continue

            if not company or not platform or not url:
                continue

            if platform not in ("greenhouse", "lever"):
                continue

            rows.append({"company": company, "platform": platform, "board_url": url})

    # Deduplicate by (platform, board_url)
    seen: Set[Tuple[str, str]] = set()
    deduped: List[Dict[str, str]] = []
    for r in rows:
        k = (r["platform"], r["board_url"].rstrip("/"))
        if k in seen:
            continue
        seen.add(k)
        r["board_url"] = r["board_url"].rstrip("/")
        deduped.append(r)

    return deduped

def make_location(parts: List[str]) -> str:
    clean = [p.strip() for p in parts if p and str(p).strip()]
    return ", ".join(clean) if clean else "Unknown Location"


# -----------------------------
# Location helpers (Boards mode)
# -----------------------------

US_STATE_ABBRS = {
    "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in","ia","ks","ky","la",
    "me","md","ma","mi","mn","ms","mo","mt","ne","nv","nh","nj","nm","ny","nc","nd","oh","ok",
    "or","pa","ri","sc","sd","tn","tx","ut","vt","va","wa","wv","wi","wy","dc",
}


def is_us_location(location: str) -> bool:
    """Return True if a location string strongly indicates a US-based role.

    This is used primarily for Greenhouse/Lever boards where postings can span many countries.
    We intentionally require a positive US signal; ambiguous 'Remote' without 'US' is excluded.
    """
    loc = (location or "").strip().lower()
    if not loc or loc == "unknown location":
        return False

    # Strong country signals
    if "united states" in loc or "u.s." in loc:
        return True

    # USA token (avoid matching 'aus' etc.)
    if re.search(r"\busa\b", loc):
        return True

    # Remote explicitly tied to US
    if "remote" in loc and (
        re.search(r"\bus\b", loc)
        or "united states" in loc
        or re.search(r"\busa\b", loc)
    ):
        return True

    # DC explicit
    if "washington, dc" in loc or "district of columbia" in loc:
        return True

    # City, ST pattern like ", CA" or ", va"
    m = re.search(r",\s*([a-z]{2})(\b|[^a-z])", loc)
    if m and m.group(1) in US_STATE_ABBRS:
        return True

    return False


# -----------------------------
# Eightfold (Microsoft + NVIDIA)
# -----------------------------

def eightfold_key_from_pos(source: str, pos: Dict[str, Any]) -> str:
    job_id = str(pos.get("id", ""))
    if job_id:
        return f"{source}:{job_id}"
    url = pos.get("applyUrl") or pos.get("positionUrl") or ""
    return f"{source}:url:{url}"


def fetch_eightfold_positions(
    source: str,
    seen_keys: Set[str] | None = None,
    max_positions: int = MAX_EIGHTFOLD_JOBS_PER_RUN,
) -> List[Dict[str, Any]]:
    cfg = EIGHTFOLD_SOURCES[source]
    endpoint = cfg["endpoint"]
    headers = {"accept": "application/json", "user-agent": "Mozilla/5.0"}

    all_positions: List[Dict[str, Any]] = []
    start = 0
    safety_cap = 5000

    while True:
        params = dict(cfg["params"])
        params["start"] = start

        r = requests.get(endpoint, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()

        positions = data.get("data", {}).get("positions", []) or []
        if not positions:
            break

        all_positions.extend(positions)

        if len(all_positions) >= max_positions:
            all_positions = all_positions[:max_positions]
            break

        if seen_keys is not None and start > 0:
            page_keys = {eightfold_key_from_pos(source, p) for p in positions}
            if page_keys and page_keys.issubset(seen_keys):
                break

        start += len(positions)
        if start >= safety_cap:
            break

    return all_positions


def normalize_eightfold_position(source: str, pos: Dict[str, Any]) -> Dict[str, str]:
    cfg = EIGHTFOLD_SOURCES[source]

    key = eightfold_key_from_pos(source, pos)
    title = pos.get("name") or pos.get("title") or "Unknown Title"

    if isinstance(pos.get("standardizedLocations"), list) and pos["standardizedLocations"]:
        loc = pos["standardizedLocations"][0]
    elif isinstance(pos.get("locations"), list) and pos["locations"]:
        loc = pos["locations"][0]
    else:
        loc = "Unknown Location"

    posted_ts = pos.get("postedTs")
    posted_str = ""
    if isinstance(posted_ts, (int, float)):
        posted_str = datetime.fromtimestamp(posted_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    url = pos.get("positionUrl") or pos.get("applyUrl") or cfg["default_search_url"]
    if isinstance(url, str) and url.startswith("/"):
        url = cfg["base_url"] + url

    return {
        "key": key,
        "company": cfg["company"],
        "title": str(title),
        "location": str(loc),
        "posted": str(posted_str),
        "url": str(url),
    }


# -----------------------------
# Amazon
# -----------------------------

def amazon_key_from_job(job: Dict[str, Any]) -> str:
    job_id = (
        job.get("id")
        or job.get("job_id")
        or job.get("jobId")
        or job.get("id_icims")
        or job.get("icims_id")
        or job.get("requisition_id")
        or ""
    )
    job_id = str(job_id)
    if job_id:
        return f"amazon:{job_id}"
    url = job.get("url") or job.get("job_path") or ""
    return f"amazon:url:{url}"


def fetch_amazon_positions(
    seen_keys: Set[str] | None = None,
    max_positions: int = MAX_AMZ_JOBS_PER_RUN,
) -> List[Dict[str, Any]]:
    headers = {"accept": "application/json", "user-agent": "Mozilla/5.0"}

    all_jobs: List[Dict[str, Any]] = []
    offset = 0
    limit = AMZ_RESULT_LIMIT
    safety_cap = 5000

    while True:
        params = dict(AMZ_PARAMS)
        params["offset"] = offset
        params["result_limit"] = limit

        r = requests.get(AMZ_ENDPOINT, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()

        jobs = data.get("jobs") or data.get("results") or data.get("job_results") or []
        if not jobs:
            break

        all_jobs.extend(jobs)

        if len(all_jobs) >= max_positions:
            all_jobs = all_jobs[:max_positions]
            break

        if seen_keys is not None and offset > 0:
            page_keys = {amazon_key_from_job(j) for j in jobs}
            if page_keys and page_keys.issubset(seen_keys):
                break

        offset += len(jobs)
        if offset >= safety_cap:
            break

    return all_jobs


def normalize_amazon_job(job: Dict[str, Any]) -> Dict[str, str]:
    key = amazon_key_from_job(job)

    title = job.get("title") or job.get("job_title") or job.get("name") or "Unknown Title"

    loc = (
        job.get("location")
        or job.get("normalized_location")
        or job.get("city")
        or job.get("primary_location")
        or "Unknown Location"
    )

    posted_str = job.get("posted_date") or job.get("postedDate") or job.get("posted") or ""

    url = job.get("url") or job.get("job_path") or job.get("jobPath") or ""
    if isinstance(url, str) and url.startswith("/"):
        url = "https://www.amazon.jobs" + url
    if not url:
        url = "https://www.amazon.jobs/en/search"

    return {
        "key": str(key),
        "company": "Amazon",
        "title": str(title),
        "location": str(loc),
        "posted": str(posted_str),
        "url": str(url),
    }


# -----------------------------
# Goldman Sachs (GraphQL)
# -----------------------------

def gs_key_from_item(item: Dict[str, Any]) -> str:
    role_id = str(item.get("roleId", ""))
    if role_id:
        return f"goldman_sachs:{role_id}"
    return f"goldman_sachs:url:{item.get('externalSource', {}).get('sourceId', '')}"


def fetch_goldman_sachs(
    seen_keys: Set[str] | None = None,
    max_positions: int = MAX_GS_JOBS_PER_RUN,
) -> List[Dict[str, Any]]:
    # We only fetch page 0 for now (pageSize=20). That's enough for frequent polling.
    # If you later need pagination, we can increment pageNumber until max_positions.
    payload = dict(GS_PAYLOAD)
    r = requests.post(GS_ENDPOINT, headers=GS_HEADERS_MIN, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()

    items = (
        data.get("data", {})
        .get("roleSearch", {})
        .get("items", [])
        or []
    )

    if max_positions and len(items) > max_positions:
        items = items[:max_positions]

    return items


def normalize_goldman_item(item: Dict[str, Any]) -> Dict[str, str]:
    key = gs_key_from_item(item)

    title = item.get("jobTitle") or item.get("corporateTitle") or "Unknown Title"

    # Locations is a list; use primary if present.
    locs = item.get("locations") or []
    loc = "Unknown Location"
    if isinstance(locs, list) and locs:
        # prefer primary field, else city/state/country
        first = locs[0] if isinstance(locs[0], dict) else {}
        loc = first.get("primary") or make_location([first.get("city"), first.get("state"), first.get("country")])

    # The query doesn't include posted date; leave blank.
    posted_str = ""

    role_id = str(item.get("roleId", ""))
    # NOTE: This URL pattern is inferred; if GS changes routing we can adjust.
    url = f"https://higher.gs.com/roles/{role_id}" if role_id else "https://higher.gs.com/results"

    return {
        "key": str(key),
        "company": "Goldman Sachs",
        "title": str(title),
        "location": str(loc),
        "posted": str(posted_str),
        "url": str(url),
    }


# -----------------------------
# IBM (POST JSON)
# -----------------------------

def ibm_key_from_hit(hit: Dict[str, Any]) -> str:
    # IBM returns ES-like hits with _id.
    _id = hit.get("_id") or hit.get("id") or ""
    _id = str(_id)
    if _id:
        return f"ibm:{_id}"
    url = (hit.get("_source") or {}).get("url") or hit.get("url") or ""
    return f"ibm:url:{url}"


def fetch_ibm(
    seen_keys: Set[str] | None = None,
    max_positions: int = MAX_IBM_JOBS_PER_RUN,
) -> List[Dict[str, Any]]:
    payload = dict(IBM_PAYLOAD)
    r = requests.post(IBM_ENDPOINT, headers=IBM_HEADERS_MIN, json=payload, timeout=30)
    if r.status_code >= 400:
        # Helpful when IBM changes schema; shows the server error message in logs.
        print(f"[DEBUG] IBM HTTP {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    data = r.json()

    # Typical structure: { "results": [ ... ] } OR { "hits": {"hits": [...] } }
    results = data.get("results")
    if isinstance(results, list):
        hits = results
    else:
        hits = (data.get("hits", {}) or {}).get("hits", []) or []

    if max_positions and len(hits) > max_positions:
        hits = hits[:max_positions]

    return hits


def normalize_ibm_hit(hit: Dict[str, Any]) -> Dict[str, str]:
    key = ibm_key_from_hit(hit)

    src = hit.get("_source") if isinstance(hit.get("_source"), dict) else hit

    title = src.get("title") or "Unknown Title"
    url = src.get("url") or ""
    if url and isinstance(url, str) and url.startswith("/"):
        url = "https://www.ibm.com" + url
    if not url:
        url = "https://www.ibm.com/careers/search"

    # IBM sometimes has a date field; keep if present.
    posted_str = src.get("dcdate") or ""

    # Try to read location fields if present; otherwise fallback.
    loc = src.get("field_keyword_17")
    if isinstance(loc, list) and loc:
        loc_str = str(loc[0])
    elif isinstance(loc, str) and loc:
        loc_str = loc
    else:
        loc_str = "United States"

    return {
        "key": str(key),
        "company": "IBM",
        "title": str(title),
        "location": str(loc_str),
        "posted": str(posted_str),
        "url": str(url),
    }


# -----------------------------
# Oracle (GET)
# -----------------------------

def oracle_key_from_req(req: Dict[str, Any]) -> str:
    # Oracle typically has requisitionId / RequisitionNumber-ish fields.
    rid = req.get("requisitionId") or req.get("RequisitionId") or req.get("id") or req.get("Id") or ""
    rid = str(rid)
    if rid:
        return f"oracle:{rid}"
    url = req.get("ExternalApplyLink") or req.get("applyUrl") or req.get("externalApplyUrl") or ""
    return f"oracle:url:{url}"


def fetch_oracle(
    seen_keys: Set[str] | None = None,
    max_positions: int = MAX_ORACLE_JOBS_PER_RUN,
) -> List[Dict[str, Any]]:
    r = requests.get(ORACLE_URL, headers=ORACLE_HEADERS_MIN, timeout=30)
    r.raise_for_status()
    data = r.json()

    # Oracle commonly returns { "items": [...] } or { "requisitionList": [...] }
    items = data.get("items")
    if isinstance(items, list):
        reqs = items
    else:
        reqs = data.get("requisitionList") or []

    if max_positions and len(reqs) > max_positions:
        reqs = reqs[:max_positions]

    return reqs


def normalize_oracle_req(req: Dict[str, Any]) -> Dict[str, str]:
    key = oracle_key_from_req(req)

    title = (
        req.get("Title")
        or req.get("title")
        or req.get("requisitionTitle")
        or req.get("requisitionName")
        or "Unknown Title"
    )

    # Try to construct a readable location.
    loc_parts: List[str] = []
    wl = req.get("workLocation")
    if isinstance(wl, dict):
        loc_parts.extend([wl.get("city"), wl.get("state"), wl.get("country")])
    loc = make_location(loc_parts) if loc_parts else "United States"

    posted_str = (
        req.get("PostedDate")
        or req.get("postedDate")
        or req.get("postingDate")
        or ""
    )

    url = (
        req.get("ExternalApplyLink")
        or req.get("externalApplyUrl")
        or req.get("applyUrl")
        or ""
    )
    if not url:
        url = "https://careers.oracle.com/jobs/#en/sites/jobsearch"

    return {
        "key": str(key),
        "company": "Oracle",
        "title": str(title),
        "location": str(loc),
        "posted": str(posted_str),
        "url": str(url),
    }


# -----------------------------
# Greenhouse + Lever (Boards mode)
# -----------------------------

def greenhouse_slug_from_board_url(board_url: str) -> str:
    # supports:
    #  - https://boards.greenhouse.io/<slug>
    #  - https://job-boards.greenhouse.io/<slug>
    parts = (board_url or "").split("/")
    return parts[-1] if parts and parts[-1] else ""


def lever_slug_from_board_url(board_url: str) -> str:
    # supports:
    #  - https://jobs.lever.co/<slug>
    parts = (board_url or "").split("/")
    return parts[-1] if parts and parts[-1] else ""


def gh_key(company_slug: str, job_id: str) -> str:
    return f"greenhouse:{company_slug}:{job_id}"


def lever_key(company_slug: str, job_id: str) -> str:
    return f"lever:{company_slug}:{job_id}"


def fetch_greenhouse_jobs(company_slug: str, timeout: int = 30) -> List[Dict[str, Any]]:
    # Public Greenhouse boards API
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs"
    r = requests.get(url, params={"content": "true"}, headers={"accept": "application/json", "user-agent": "Mozilla/5.0"}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    jobs = data.get("jobs") or []
    return jobs if isinstance(jobs, list) else []


def fetch_lever_jobs(company_slug: str, timeout: int = 30) -> List[Dict[str, Any]]:
    # Public Lever postings API
    url = f"https://jobs.lever.co/v0/postings/{company_slug}"
    r = requests.get(url, params={"mode": "json"}, headers={"accept": "application/json", "user-agent": "Mozilla/5.0"}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def normalize_greenhouse_job(company_name: str, company_slug: str, job: Dict[str, Any]) -> Dict[str, str]:
    job_id = str(job.get("id") or "")
    key = gh_key(company_slug, job_id) if job_id else f"greenhouse:{company_slug}:url:{job.get('absolute_url','')}"

    title = job.get("title") or "Unknown Title"

    # location: prefer first location name
    loc = "Unknown Location"
    loc_obj = job.get("location")
    if isinstance(loc_obj, dict) and loc_obj.get("name"):
        loc = str(loc_obj.get("name"))

    posted_str = job.get("updated_at") or job.get("created_at") or ""
    url = job.get("absolute_url") or job.get("url") or ""

    return {
        "key": str(key),
        "company": str(company_name),
        "title": str(title),
        "location": str(loc),
        "posted": str(posted_str),
        "url": str(url),
    }


def normalize_lever_job(company_name: str, company_slug: str, job: Dict[str, Any]) -> Dict[str, str]:
    job_id = str(job.get("id") or "")
    key = lever_key(company_slug, job_id) if job_id else f"lever:{company_slug}:url:{job.get('hostedUrl','')}"

    title = job.get("text") or job.get("title") or "Unknown Title"

    loc = "Unknown Location"
    categories = job.get("categories")
    if isinstance(categories, dict) and categories.get("location"):
        loc = str(categories.get("location"))

    posted_str = job.get("createdAt") or ""
    # createdAt is usually ms since epoch
    if isinstance(posted_str, (int, float)):
        posted_str = datetime.fromtimestamp(float(posted_str) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    url = job.get("hostedUrl") or job.get("applyUrl") or ""

    return {
        "key": str(key),
        "company": str(company_name),
        "title": str(title),
        "location": str(loc),
        "posted": str(posted_str),
        "url": str(url),
    }


def run_boards_sweep(
    seen: Set[str],
    boards_csv: str,
    batch_size: int,
) -> Tuple[List[Dict[str, str]], Set[str], List[str], int]:
    boards = load_boards_csv(boards_csv)
    if not boards:
        return [], set(), ["Boards CSV is empty after filtering."], 0

    cursor = load_boards_cursor(BOARDS_CURSOR_PATH)
    n = len(boards)

    # Process a slice [cursor, cursor+batch_size)
    start = cursor % n
    end = min(start + max(batch_size, 1), n)
    batch = boards[start:end]

    normalized: List[Dict[str, str]] = []
    errors: List[str] = []

    for b in batch:
        company = b["company"]
        platform = b["platform"]
        board_url = b["board_url"]

        try:
            if platform == "greenhouse":
                slug = greenhouse_slug_from_board_url(board_url)
                jobs = fetch_greenhouse_jobs(slug)
                normalized.extend([normalize_greenhouse_job(company, slug, j) for j in jobs])
            elif platform == "lever":
                slug = lever_slug_from_board_url(board_url)
                jobs = fetch_lever_jobs(slug)
                normalized.extend([normalize_lever_job(company, slug, j) for j in jobs])
        except Exception as e:
            errors.append(f"{platform} {company}: {type(e).__name__}: {e}")

    matched = [
        j for j in normalized
        if title_matches(j.get("title", "")) and is_us_location(j.get("location", ""))
    ]
    latest_keys = {j["key"] for j in matched if j.get("key")}

    # Advance cursor
    new_cursor = end if end < n else 0

    return matched, latest_keys, errors, new_cursor

def send_email_digest(new_jobs: List[Dict[str, str]], subject_prefix: str = "[Job Alerts]") -> None:
    if not (EMAIL_USER and EMAIL_APP_PASSWORD and ALERT_TO_EMAIL):
        raise RuntimeError("Missing EMAIL_USER / EMAIL_APP_PASSWORD / ALERT_TO_EMAIL env vars.")

    companies = sorted({j.get("company", "") for j in new_jobs if j.get("company")})
    company_str = ", ".join(companies) if companies else "Jobs"
    subject = f"{subject_prefix} {len(new_jobs)} new posting(s) ({company_str})"

    lines: List[str] = []
    lines.append(f"Found {len(new_jobs)} new posting(s) ({company_str}):\n")

    for j in new_jobs:
        posted = f" | {j['posted']}" if j.get("posted") else ""
        lines.append(f"- [{j['company']}] {j['title']} | {j['location']}{posted}")
        lines.append(f"  {j['url']}")
        lines.append("")

    body = "\n".join(lines)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = ALERT_TO_EMAIL

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        app_pw = (EMAIL_APP_PASSWORD or "").replace(" ", "")
        server.login(EMAIL_USER, app_pw)
        server.send_message(msg)


# -----------------------------
# Main orchestration
# -----------------------------

def safe_call(label: str, fn):
    try:
        return fn(), None
    except Exception as e:
        return None, f"{label}: {type(e).__name__}: {e}"


def main(test_email: bool = False) -> None:
    seen = load_seen_ids(STATE_PATH)

    normalized: List[Dict[str, str]] = []
    errors: List[str] = []

    # Microsoft (Eightfold)
    ms_positions, err = safe_call("Microsoft fetch", lambda: fetch_eightfold_positions("microsoft", seen_keys=seen))
    if err:
        errors.append(err)
    else:
        print(f"[DEBUG] Fetched {len(ms_positions)} positions from Microsoft endpoint.")
        normalized.extend([normalize_eightfold_position("microsoft", p) for p in (ms_positions or [])])

    # NVIDIA (Eightfold)
    nv_positions, err = safe_call("NVIDIA fetch", lambda: fetch_eightfold_positions("nvidia", seen_keys=seen))
    if err:
        errors.append(err)
    else:
        print(f"[DEBUG] Fetched {len(nv_positions)} positions from NVIDIA endpoint.")
        normalized.extend([normalize_eightfold_position("nvidia", p) for p in (nv_positions or [])])

    # Amazon
    amz_positions, err = safe_call("Amazon fetch", lambda: fetch_amazon_positions(seen_keys=seen))
    if err:
        errors.append(err)
    else:
        print(f"[DEBUG] Fetched {len(amz_positions)} jobs from Amazon endpoint.")
        normalized.extend([normalize_amazon_job(j) for j in (amz_positions or [])])

    # Goldman Sachs
    gs_items, err = safe_call("Goldman Sachs fetch", lambda: fetch_goldman_sachs(seen_keys=seen))
    if err:
        errors.append(err)
    else:
        print(f"[DEBUG] Fetched {len(gs_items)} roles from Goldman Sachs endpoint.")
        normalized.extend([normalize_goldman_item(i) for i in (gs_items or [])])

    # IBM
    ibm_hits, err = safe_call("IBM fetch", lambda: fetch_ibm(seen_keys=seen))
    if err:
        errors.append(err)
    else:
        print(f"[DEBUG] Fetched {len(ibm_hits)} roles from IBM endpoint.")
        normalized.extend([normalize_ibm_hit(h) for h in (ibm_hits or [])])

    # Oracle
    oracle_reqs, err = safe_call("Oracle fetch", lambda: fetch_oracle(seen_keys=seen))
    if err:
        errors.append(err)
    else:
        print(f"[DEBUG] Fetched {len(oracle_reqs)} requisitions from Oracle endpoint.")
        normalized.extend([normalize_oracle_req(rq) for rq in (oracle_reqs or [])])

    # Loose title match
    matched = [j for j in normalized if title_matches(j.get("title", ""))]

    # Test mode: send a small sample email to verify SMTP works.
    # This does NOT modify seen_ids.
    if test_email:
        sample = matched[:3]
        if not sample:
            raise RuntimeError("No matching jobs found to send in test email.")
        send_email_digest(sample, subject_prefix="[TEST Job Alerts]")
        print(f"[TEST] Sent a test email with {len(sample)} job(s) to {ALERT_TO_EMAIL}.")
        if errors:
            print("[WARN] Some sources failed:")
            for e in errors:
                print("  -", e)
        return

    latest_keys = {j["key"] for j in matched if j.get("key")}

    # Per-source bootstrap: if a source has never been seen before, don't email
    # all existing matches from that source on the first run after adding it.
    bootstrap_sources: Set[str] = set()
    for src in SUPPORTED_SOURCES:
        if not any(k.startswith(f"{src}:") for k in seen):
            bootstrap_sources.add(src)

    if bootstrap_sources:
        for src in bootstrap_sources:
            src_keys = {k for k in latest_keys if k.startswith(f"{src}:")}
            seen |= src_keys
        save_seen_ids(STATE_PATH, seen)
        print(
            f"[BOOTSTRAP] Initialized sources: {', '.join(sorted(bootstrap_sources))}. "
            "No email for these sources this run."
        )

    # Bootstrap mode (first ever run): save state, do NOT email
    if not os.path.exists(STATE_PATH):
        save_seen_ids(STATE_PATH, latest_keys)
        print(f"[BOOTSTRAP] Saved {len(latest_keys)} seen_ids. No email sent.")
        return

    new_keys = latest_keys - seen
    new_jobs = [j for j in matched if j.get("key") in new_keys]

    if new_jobs:
        send_email_digest(new_jobs)
        print(f"[ALERT] Sent digest for {len(new_jobs)} new job(s).")
    else:
        print("[OK] No new jobs.")

    seen |= latest_keys
    save_seen_ids(STATE_PATH, seen)

    if errors:
        print("[WARN] Some sources failed (watcher still ran):")
        for e in errors:
            print("  -", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job watcher")
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send a test email using the latest 1-3 matching jobs (does not change seen_ids).",
    )
    parser.add_argument(
        "--mode",
        default="main",
        choices=["main", "boards"],
        help="Run mode: main (existing adapters) or boards (Greenhouse/Lever sweep).",
    )
    parser.add_argument(
        "--boards-csv",
        default="job_boards_ok_with_meta.csv",
        help="Boards CSV path (must include company_name, platform, board_url).",
    )
    parser.add_argument(
        "--boards-batch-size",
        type=int,
        default=50,
        help="How many boards to process per boards run (default: 50).",
    )

    args = parser.parse_args()

    if args.mode == "boards":
        seen = load_seen_ids(STATE_PATH)
        matched, latest_keys, errors, new_cursor = run_boards_sweep(
            seen=seen,
            boards_csv=args.boards_csv,
            batch_size=args.boards_batch_size,
        )

        if args.test_email:
            sample = matched[:3]
            if not sample:
                raise RuntimeError("No matching jobs found to send in test email.")
            send_email_digest(sample, subject_prefix="[TEST Boards Alerts]")
            print(f"[TEST] Sent a test boards email with {len(sample)} job(s) to {ALERT_TO_EMAIL}.")
            if errors:
                print("[WARN] Some boards failed:")
                for e in errors:
                    print("  -", e)
            # Still save cursor so manual test walks forward
            save_boards_cursor(BOARDS_CURSOR_PATH, new_cursor)
            raise SystemExit(0)

        # First-ever boards run: bootstrap to avoid emailing historical postings
        if not os.path.exists(STATE_PATH):
            save_seen_ids(STATE_PATH, latest_keys)
            save_boards_cursor(BOARDS_CURSOR_PATH, new_cursor)
            print(f"[BOOTSTRAP] Saved {len(latest_keys)} seen_ids (boards). No email sent.")
            raise SystemExit(0)

        # Boards bootstrap: if we've never stored any greenhouse/lever keys yet,
        # don't email a massive historical dump on the first boards run.
        has_any_boards_keys = any(
            k.startswith("greenhouse:") or k.startswith("lever:")
            for k in seen
        )

        if not has_any_boards_keys:
            seen |= latest_keys
            save_seen_ids(STATE_PATH, seen)
            save_boards_cursor(BOARDS_CURSOR_PATH, new_cursor)
            print(
                f"[BOOTSTRAP] Initialized boards mode with {len(latest_keys)} seen_ids. "
                "No email sent."
            )
            raise SystemExit(0)

        new_keys = latest_keys - seen
        new_jobs = [j for j in matched if j.get("key") in new_keys]

        if new_jobs:
            send_email_digest(new_jobs, subject_prefix="[Boards Alerts]")
            print(f"[ALERT] Sent boards digest for {len(new_jobs)} new job(s).")
        else:
            print("[OK] No new boards jobs.")

        seen |= latest_keys
        save_seen_ids(STATE_PATH, seen)
        save_boards_cursor(BOARDS_CURSOR_PATH, new_cursor)

        if errors:
            print("[WARN] Some boards failed (sweep still ran):")
            for e in errors:
                print("  -", e)

    else:
        main(test_email=args.test_email)