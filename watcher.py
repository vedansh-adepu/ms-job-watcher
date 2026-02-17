import json
import os
import argparse
import ssl
import smtplib
import csv
import re
from urllib.parse import urlparse
LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")  # en-US, fr-CA, etc.

def _parse_workday_board(board_url: str):
    u = urlparse(board_url)
    host = u.netloc
    tenant = host.split(".")[0]
    origin = f"{u.scheme}://{host}"

    segs = [s for s in (u.path or "").split("/") if s]
    if not segs:
        raise ValueError(f"Workday board_url has no path: {board_url}")

    # If /en-US/<site>, drop the locale segment
    if len(segs) >= 2 and LOCALE_RE.match(segs[0]):
        site = segs[1]
    else:
        site = segs[0]

    return origin, tenant, site

def _workday_cxs_endpoints(board_url: str):
    origin, tenant, site = _parse_workday_board(board_url)
    approot = f"{origin}/wday/cxs/{tenant}/{site}/approot"
    jobs = f"{origin}/wday/cxs/{tenant}/{site}/jobs"
    return approot, jobs

def _is_workday_app_error(text: str) -> bool:
    return "<wml:Application_Error" in (text or "")
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

# State file paths
STATE_PATH = "state/seen.json"
BOARDS_CURSOR_PATH = "state/boards_cursor.json"
BOARDS_SEEN_PATH = "state/boards_seen.json"
BOARDS_DEAD_PATH = "state/boards_dead.json"
BOARDS_DEAD_DETAILS_PATH = "state/boards_dead_details.json"

# Default boards CSV resolution (lets workflows/CLI run without remembering paths)
# Priority:
#   1) explicit env var BOARDS_CSV
#   2) round2 supported (pure + supported platforms)
#   3) round2 pure working (may include currently-unsupported platforms)
#   4) minus-dead round2
#   5) production OK list
def resolve_default_boards_csv() -> str:
    candidates = [
        (os.getenv("BOARDS_CSV") or "").strip(),
        "data/boards/JOB_BOARDS_PURE_WORKING_SUPPORTED_round2.csv",
        "data/boards/JOB_BOARDS_PURE_WORKING_round2.csv",
        "data/boards/JOB_BOARDS_OK_PRODUCTION_MINUS_DEAD_round2.csv",
        "data/boards/JOB_BOARDS_OK_PRODUCTION.csv",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    # Fallback: preserve prior behavior (caller will error if missing)
    return "data/boards/JOB_BOARDS_OK_PRODUCTION.csv"
# Boards mode currently implements adapters for these ATS platforms only.
# NOTE: The master dataset can include other ATS platforms (jobvite, icims, phenom, taleo, talentbrew, oraclecloud, etc.).
# Those rows are intentionally kept in the CSVs for future adapter work, but boards mode currently processes only
# the platforms listed above.
BOARDS_SUPPORTED_PLATFORMS = ("greenhouse", "lever", "smartrecruiters", "workday")
SMARTRECRUITERS_API_BASE = "https://api.smartrecruiters.com/v1/companies"
# ---- EMAIL ENV VARS ----
EMAIL_USER = os.getenv("EMAIL_USER")  # sender gmail
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")  # gmail app password
ALERT_TO_EMAIL = os.getenv("ALERT_TO_EMAIL")  # receiver email

# --- Title filtering (fast + "model-like") ---
# Goal: do NOT miss roles you would apply to.
# We classify titles into: yes / maybe / no.
# - yes   => strong match, low-noise (send as primary)
# - maybe => potentially relevant but noisy/ambiguous (send in a separate section)
# - no    => ignore

# Strong includes (if present and no hard-exclude): bucket = yes (unless seniority makes it maybe)
STRONG_INCLUDE_PHRASES = [
    # Core SWE
    "software engineer",
    "software developer",
    "software development engineer",
    "sde",
    "backend engineer",
    "backend developer",
    "full stack",
    "fullstack",
    "platform engineer",
    "application developer",
    "applications engineer",

    # Product development (explicitly OK)
    "product developer",
    "product development engineer",

    # ML / Applied
    "machine learning engineer",
    "ml engineer",
    "ai engineer",
    "applied scientist",
    "research engineer",
    "data scientist",

    # Data
    "data engineer",
    "analytics engineer",
    "data analyst",
    "analytics analyst",
    "product analyst",

    # Testing (explicitly OK)
    "sdet",
    "software development engineer in test",
]

# Weaker includes: typically relevant, but too broad to mark as YES by itself.
WEAK_INCLUDE_PHRASES = [
    "developer",          # requested
    "software",           # catches variants like "Software Dev" but will be MAYBE
    "engineer",           # very broad; stays MAYBE
    "analytics",          # catches "Analytics" variants
]

# Seniority/level terms: you said you want to *exclude* these from YES.
# We don't drop them entirely; we bucket them as MAYBE so we don't miss edge cases.
SENIORITY_MAYBE_TOKENS = [
    "senior",
    "sr",
    "staff",
    "principal",
    "lead",
    "architect",
    "distinguished",
    "fellow",
    "director",
]

# Hard excludes: roles you explicitly don't want.
# NOTE: We implement a couple of exceptions below (e.g., SDET is OK even if QA is present).
HARD_EXCLUDE_PHRASES = [
    # QA / testing (except SDET)
    "quality assurance",
    "qa ",
    " qa",
    "tester",
    "test engineer",
    "quality engineer",
    "validation engineer",

    # Reliability / Ops
    "site reliability",
    "sre",
    "reliability engineer",

    # Reporting-heavy analyst roles
    "reporting",

    # Non-target job families
    "product manager",
    "program manager",
    "project manager",
    "scrum master",
    "business analyst",
    "sales",
    "marketing",
    "recruiter",
    "talent acquisition",
    "customer support",
    "technical support",
    "support engineer",
]

# Regex-based hard excludes (avoid substring traps like "internal")
HARD_EXCLUDE_REGEXES = [
    r"\bintern\b",
    r"\binternship\b",
    r"\bco[- ]?op\b",
    r"\bcoop\b",
    r"\bapprentice\b",
]

# Soft excludes: not your target, but can appear in SWE titles.
# We treat these as MAYBE when paired with a strong include.
SOFT_EXCLUDE_PHRASES = [
    "devops",
    "operations",
    "ops",
    "automation",
]


def _norm_title(title: str) -> str:
    t = (title or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def classify_title(title: str) -> str:
    """Return one of: 'yes', 'maybe', 'no' based on the title only."""
    t = _norm_title(title)
    if not t:
        return "no"

    has_sdet = ("sdet" in t) or ("software development engineer in test" in t)

    # Exclude internships / co-ops (you said you're not targeting intern roles)
    for pat in HARD_EXCLUDE_REGEXES:
        if re.search(pat, t):
            return "no"

    # Hard excludes (with SDET exception)
    for bad in HARD_EXCLUDE_PHRASES:
        if bad in t:
            if has_sdet and bad in {"quality assurance", "qa ", " qa", "tester", "test engineer", "quality engineer", "validation engineer"}:
                break  # allow SDET even if QA-ish wording exists
            return "no"

    # Soft excludes: only MAYBE if otherwise relevant
    has_soft_excl = any(bad in t for bad in SOFT_EXCLUDE_PHRASES)

    # Strong include?
    strong = any(p in t for p in STRONG_INCLUDE_PHRASES)
    weak = any(p in t for p in WEAK_INCLUDE_PHRASES)

    if not (strong or weak):
        return "no"

    # Soft excludes: if the title only matched via weak signals (e.g., "engineer"), drop it.
    # If it has a strong include (e.g., "Software Engineer, DevOps"), keep it as MAYBE.
    if has_soft_excl:
        return "maybe" if strong else "no"

    # Level/seniority tokens => MAYBE
    for tok in SENIORITY_MAYBE_TOKENS:
        if re.search(rf"\b{re.escape(tok)}\b", t):
            return "maybe"

    # Weak-only match => MAYBE (broad)
    if not strong:
        return "maybe"

    return "yes"


def title_matches(title: str) -> bool:
    """Backwards-compatible helper: True if title is yes OR maybe."""
    return classify_title(title) in ("yes", "maybe")

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


def load_boards_seen(path: str) -> Set[str]:
    """Tracks which boards have been processed at least once in boards mode.

    This prevents a huge first-time alert when we encounter a board for the first time
    (we bootstrap that board by marking its current jobs as seen).
    """
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("boards_seen", []))
    except Exception:
        return set()


def save_boards_seen(path: str, boards_seen: Set[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "boards_seen": sorted(boards_seen),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


# --- Dead boards helpers ---

def load_boards_dead(path: str) -> Set[str]:
    """Boards that appear to be dead/unresolvable (e.g., Greenhouse 404).

    We skip these in future sweeps to avoid wasting time and spamming warnings.
    """
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("boards_dead", []))
    except Exception:
        return set()


def save_boards_dead(path: str, boards_dead: Set[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "boards_dead": sorted(boards_dead),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_dead_details(path: str) -> Dict[str, Dict[str, Any]]:
    """Richer metadata for dead boards keyed by board_id."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        details = data.get("dead_details") or {}
        return details if isinstance(details, dict) else {}
    except Exception:
        return {}


def save_dead_details(path: str, dead_details: Dict[str, Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "dead_details": dead_details,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def upsert_dead_detail(
    dead_details: Dict[str, Dict[str, Any]],
    *,
    board_id: str,
    platform: str,
    company: str,
    board_url: str,
    status: int | None,
    error: str,
) -> None:
    """Insert/update a dead board record."""
    now = datetime.now(timezone.utc).isoformat()
    rec = dead_details.get(board_id) or {}
    if not rec.get("first_seen_utc"):
        rec["first_seen_utc"] = now
    rec["last_seen_utc"] = now
    rec["platform"] = platform
    rec["company"] = company
    rec["board_url"] = board_url
    if status is not None:
        rec["last_status"] = int(status)
    rec["last_error"] = str(error)
    dead_details[board_id] = rec


def export_dead_boards_csv(dead_details: Dict[str, Dict[str, Any]], out_path: str) -> None:
    """Write a CSV of dead boards so you can prune the master boards CSV."""
    if not out_path:
        return
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fieldnames = [
        "board_id",
        "platform",
        "company",
        "board_url",
        "last_status",
        "last_error",
        "first_seen_utc",
        "last_seen_utc",
    ]
    rows: List[Dict[str, Any]] = []
    for board_id, rec in sorted(dead_details.items(), key=lambda x: x[0]):
        row = {"board_id": board_id}
        for k in fieldnames[1:]:
            row[k] = rec.get(k, "")
        rows.append(row)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

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

            # Keep all platforms in the CSV; unsupported ones will be skipped at runtime
            # (so we can extend adapters later without changing the dataset format).
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

    # US token (common in APIs like SmartRecruiters)
    if re.search(r"\bus\b", loc):
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
# Workday (Boards mode)
# -----------------------------

WORKDAY_HEADERS_MIN = {
    "accept": "application/json",
    "content-type": "application/json",
    "user-agent": "Mozilla/5.0",
}


def _looks_like_locale(seg: str) -> bool:
    # common: en-US, fr-FR, etc.
    return bool(re.fullmatch(r"[a-z]{2}-[A-Z]{2}", seg or ""))


def workday_tenant_from_host(host: str) -> str:
    # host like: tenant.myworkdayjobs.com OR tenant.wd5.myworkdayjobs.com
    host = (host or "").strip().lower()
    if not host:
        return ""
    return host.split(".")[0]


def workday_site_from_board_url(board_url: str) -> str:
    """Extract Workday 'site' from a board home URL.

    Examples:
      https://tenant.myworkdayjobs.com/Careers
      https://tenant.myworkdayjobs.com/en-US/Careers
      https://tenant.wd5.myworkdayjobs.com/en-US/External

    We treat the first non-locale path segment as the site.
    """
    u = urlparse((board_url or "").strip())
    parts = [p for p in (u.path or "").split("/") if p]
    if not parts:
        return ""

    # drop locale segment like en-US
    if parts and _looks_like_locale(parts[0]):
        parts = parts[1:]

    # If still empty, fail
    return parts[0] if parts else ""


def workday_board_id(board_url: str) -> str:
    u = urlparse((board_url or "").strip())
    tenant = workday_tenant_from_host(u.netloc)
    site = workday_site_from_board_url(board_url)
    if tenant and site:
        return f"workday:{tenant}:{site}"  # stable id used for dead/seen tracking
    if tenant:
        return f"workday:{tenant}:"  # fallback
    return "workday:"


def workday_key_from_post(tenant: str, site: str, post: Dict[str, Any], url: str) -> str:
    pid = str(post.get("jobPostingId") or post.get("id") or "")
    if pid:
        return f"workday:{tenant}:{site}:{pid}"
    return f"workday:{tenant}:{site}:url:{url}"


def fetch_workday_jobs(board_url: str, max_positions: int = 500, timeout: int = 30) -> List[Dict[str, Any]]:
    """Fetch jobs from Workday's CXS endpoint.

    Uses POST JSON with limit/offset.
    """
    try:
        approot_url, jobs_url = _workday_cxs_endpoints(board_url)
    except Exception:
        return []

    all_posts: List[Dict[str, Any]] = []
    offset = 0
    limit = 20
    safety_cap = 5000

    sess = requests.Session()
    headers = dict(WORKDAY_HEADERS_MIN)

    # Bootstrap once to establish cookies/session (many Workday tenants expect this)
    boot = sess.get(approot_url, timeout=timeout)
    boot.raise_for_status()

    while True:
        payload = {
            "limit": limit,
            "offset": offset,
            "searchText": "",
            "appliedFacets": {},
        }
        resp = sess.post(jobs_url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()

        # Workday sometimes returns app-errors as HTTP 200 with XML body
        if _is_workday_app_error(resp.text):
            err_snip = (resp.text or "")[:200].replace(chr(10), " ")
            raise RuntimeError(f"Workday application error: {err_snip}")

        ct = (resp.headers.get("content-type") or "").lower()
        if "json" not in ct:
            body_snip = (resp.text or "")[:200].replace(chr(10), " ")
            raise RuntimeError(f"Workday non-JSON response (ct={ct}): {body_snip}")

        data = resp.json() if resp.content else {}
        posts = data.get("jobPostings") or data.get("items") or []
        if not isinstance(posts, list) or not posts:
            break

        all_posts.extend(posts)

        if max_positions and len(all_posts) >= max_positions:
            all_posts = all_posts[:max_positions]
            break

        offset += len(posts)
        if offset >= safety_cap:
            break

    return all_posts


def normalize_workday_post(company_name: str, board_url: str, post: Dict[str, Any]) -> Dict[str, str]:
    u = urlparse((board_url or "").strip())
    host = (u.netloc or "").strip()
    tenant = workday_tenant_from_host(host)
    site = workday_site_from_board_url(board_url)

    title = post.get("title") or post.get("jobTitle") or "Unknown Title"

    loc = post.get("locationsText") or post.get("location") or "Unknown Location"
    if isinstance(loc, list):
        loc = make_location([str(x) for x in loc])
    else:
        loc = str(loc)

    posted = post.get("postedOn") or post.get("postedDate") or post.get("timePosted") or ""

    ext = post.get("externalPath") or post.get("externalUrl") or ""
    url = ""
    if isinstance(ext, str) and ext:
        if ext.startswith("http"):
            url = ext
        elif ext.startswith("/") and host:
            url = f"https://{host}{ext}"
    if not url:
        url = board_url

    key = workday_key_from_post(tenant, site, post, url)

    return {
        "key": str(key),
        "company": str(company_name),
        "title": str(title),
        "location": str(loc),
        "posted": str(posted),
        "url": str(url),
    }

# -----------------------------
# SmartRecruiters (Boards mode)
# -----------------------------

def smartrecruiters_company_from_board_url(board_url: str) -> str:
    """
    Supports:
      - https://jobs.smartrecruiters.com/<CompanySlug>
      - https://careers.smartrecruiters.com/<CompanySlug>
    Returns the <CompanySlug> part.
    """
    u = urlparse((board_url or "").strip())
    parts = [p for p in (u.path or "").split("/") if p]

    # expected: /<CompanySlug>
    if len(parts) >= 1:
        return parts[0].strip()

    return ""


def smartrecruiters_board_id(board_url: str) -> str:
    slug = smartrecruiters_company_from_board_url(board_url).lower()
    return f"smartrecruiters:{slug}" if slug else "smartrecruiters:"


def smartrecruiters_key_from_post(company_slug: str, post: Dict[str, Any]) -> str:
    pid = str(post.get("id") or post.get("ref") or "")
    if pid:
        return f"smartrecruiters:{company_slug}:{pid}"
    # fallback: use posting URL if present
    url = post.get("referrer") or post.get("applyUrl") or post.get("url") or ""
    return f"smartrecruiters:{company_slug}:url:{url}"


def fetch_smartrecruiters_jobs(board_url: str, max_positions: int = 500, timeout: int = 30) -> List[Dict[str, Any]]:
    """
    Fetch postings for a company via:
      GET https://api.smartrecruiters.com/v1/companies/{company}/postings?offset=0&limit=100
    """
    company = smartrecruiters_company_from_board_url(board_url)
    if not company:
        return []

    headers = {"accept": "application/json", "user-agent": "Mozilla/5.0"}

    all_posts: List[Dict[str, Any]] = []
    offset = 0
    limit = 100
    safety_cap = 5000

    while True:
        url = f"{SMARTRECRUITERS_API_BASE}/{company}/postings"
        params = {"offset": offset, "limit": limit}

        r = requests.get(url, params=params, headers=headers, timeout=timeout)

        # Let caller handle “dead board” classification consistently
        r.raise_for_status()

        data = r.json() if r.content else {}
        posts = data.get("content") or data.get("postings") or []
        if not isinstance(posts, list) or not posts:
            break

        all_posts.extend(posts)

        if max_positions and len(all_posts) >= max_positions:
            all_posts = all_posts[:max_positions]
            break

        offset += len(posts)
        if offset >= safety_cap:
            break

    return all_posts


def normalize_smartrecruiters_post(company_name: str, board_url: str, post: Dict[str, Any]) -> Dict[str, str]:
    company_slug = smartrecruiters_company_from_board_url(board_url) or company_name.lower().replace(" ", "")
    key = smartrecruiters_key_from_post(company_slug, post)

    title = post.get("name") or post.get("jobTitle") or "Unknown Title"

    # Location shape varies; we build a robust string
    loc_obj = post.get("location") or {}
    if isinstance(loc_obj, dict):
        city = loc_obj.get("city")
        region = loc_obj.get("region") or loc_obj.get("state")
        country = loc_obj.get("country")
        loc_str = make_location([city, region, country])
    else:
        loc_str = str(loc_obj) if loc_obj else "Unknown Location"

    posted = post.get("releasedDate") or post.get("publicationDate") or post.get("createdOn") or ""
    url = post.get("referrer") or post.get("applyUrl") or post.get("url") or ""

    # If API doesn’t give a URL, fall back to the board home
    if not url:
        url = board_url

    return {
        "key": str(key),
        "company": str(company_name),
        "title": str(title),
        "location": str(loc_str),
        "posted": str(posted),
        "url": str(url),
    }

# -----------------------------
# Greenhouse + Lever (Boards mode)
# -----------------------------

def greenhouse_slug_from_board_url(board_url: str) -> str:
    """Extract the Greenhouse board slug from a board home URL.

    Supports:
      - https://boards.greenhouse.io/<slug>
      - https://job-boards.greenhouse.io/<slug>
    """
    u = urlparse(board_url or "")
    parts = [p for p in (u.path or "").split("/") if p]
    return parts[0] if parts else ""


def lever_slug_from_board_url(board_url: str) -> str:
    """Extract the Lever board slug from a board home URL.

    Supports:
      - https://jobs.lever.co/<slug>
    """
    u = urlparse(board_url or "")
    parts = [p for p in (u.path or "").split("/") if p]
    return parts[0] if parts else ""


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
    boards_seen: Set[str],
    dead_boards: Set[str],
    dead_details: Dict[str, Dict[str, Any]],
    boards_csv: str,
    batch_size: int,
    timeout: int = 30,
) -> Tuple[List[Dict[str, str]], Set[str], List[str], int, Set[str], Set[str]]:
    boards = load_boards_csv(boards_csv)

    # Boards mode supports a subset of ATS platforms. Keep other rows in the CSV,
    # but skip them at runtime until adapters are implemented.
    # Filter upfront so the cursor walks only supported boards (no wasted runs on other ATS).
    boards = [b for b in boards if (b.get("platform") or "") in BOARDS_SUPPORTED_PLATFORMS]
    if not boards:
        return [], set(), ["No supported boards found (need greenhouse/lever/smartrecruiters rows)."], 0, set(), set()

    cursor = load_boards_cursor(BOARDS_CURSOR_PATH)
    n = len(boards)

    # Process a slice [cursor, cursor+batch_size)
    start = cursor % n
    end = min(start + max(batch_size, 1), n)
    batch = boards[start:end]

    normalized: List[Dict[str, str]] = []
    errors: List[str] = []
    bootstrap_keys: Set[str] = set()
    bootstrap_boards: Set[str] = set()

    for b in batch:
        company = b["company"]
        platform = b["platform"]
        board_url = b["board_url"]

        if platform not in BOARDS_SUPPORTED_PLATFORMS:
            continue

        try:
            if platform == "greenhouse":
                slug = greenhouse_slug_from_board_url(board_url)
                board_id = f"greenhouse:{slug}"
            elif platform == "lever":
                slug = lever_slug_from_board_url(board_url)
                board_id = f"lever:{slug}"
            elif platform == "smartrecruiters":
                slug = smartrecruiters_company_from_board_url(board_url)
                board_id = smartrecruiters_board_id(board_url)
            else:
                # workday
                slug = ""
                board_id = workday_board_id(board_url)

            if board_id in dead_boards:
                continue

            if platform == "greenhouse":
                jobs = fetch_greenhouse_jobs(slug, timeout=timeout)
                norm_jobs = [normalize_greenhouse_job(company, slug, j) for j in jobs]
            elif platform == "lever":
                jobs = fetch_lever_jobs(slug, timeout=timeout)
                norm_jobs = [normalize_lever_job(company, slug, j) for j in jobs]
            elif platform == "smartrecruiters":
                jobs = fetch_smartrecruiters_jobs(board_url, timeout=timeout)
                norm_jobs = [normalize_smartrecruiters_post(company, board_url, j) for j in jobs]
            else:
                # workday
                jobs = fetch_workday_jobs(board_url, timeout=timeout)
                norm_jobs = [normalize_workday_post(company, board_url, j) for j in jobs]

            # Per-board bootstrap: the first time we ever see a board, mark its current
            # matching jobs as seen and do NOT alert (prevents huge "new" dumps).
            if board_id not in boards_seen:
                matched_on_board = [
                    j for j in norm_jobs
                    if title_matches(j.get("title", "")) and is_us_location(j.get("location", ""))
                ]
                bootstrap_keys |= {j["key"] for j in matched_on_board if j.get("key")}
                bootstrap_boards.add(board_id)
                continue

            normalized.extend(norm_jobs)

        except Exception as e:
            # If the board itself doesn't exist anymore (GH returns 404), mark it dead and skip it next time.
            if isinstance(e, requests.HTTPError) and getattr(e, "response", None) is not None:
                try:
                    status = int(e.response.status_code)
                except Exception:
                    status = None
                if status == 404 and "board_id" in locals() and board_id:
                    dead_boards.add(board_id)
                    if platform == "smartrecruiters":
                        err_msg = "HTTP 404 (company/board not found)"
                    elif platform == "workday":
                        err_msg = "HTTP 404 (Workday API not found)"
                    else:
                        err_msg = "HTTP 404 (board not found)"
                    upsert_dead_detail(
                        dead_details,
                        board_id=board_id,
                        platform=platform,
                        company=company,
                        board_url=board_url,
                        status=status,
                        error=err_msg,
                    )
                    errors.append(f"DEAD {platform} {company}: {err_msg} -> {board_url}")
                    continue

            errors.append(f"{platform} {company}: {type(e).__name__}: {e}")

    matched = [
        j for j in normalized
        if title_matches(j.get("title", "")) and is_us_location(j.get("location", ""))
    ]
    latest_keys = {j["key"] for j in matched if j.get("key")}

    # Advance cursor
    new_cursor = end if end < n else 0

    return matched, latest_keys, errors, new_cursor, bootstrap_keys, bootstrap_boards

def send_email_digest(
    yes_jobs: List[Dict[str, str]],
    maybe_jobs: List[Dict[str, str]],
    subject_prefix: str = "[Job Alerts]",
) -> None:
    if not (EMAIL_USER and EMAIL_APP_PASSWORD and ALERT_TO_EMAIL):
        raise RuntimeError("Missing EMAIL_USER / EMAIL_APP_PASSWORD / ALERT_TO_EMAIL env vars.")

    all_jobs = (yes_jobs or []) + (maybe_jobs or [])
    companies = sorted({j.get("company", "") for j in all_jobs if j.get("company")})
    company_str = ", ".join(companies) if companies else "Jobs"

    subject = f"{subject_prefix} {len(yes_jobs)} yes + {len(maybe_jobs)} maybe ({company_str})"

    lines: List[str] = []
    lines.append(f"YES bucket: {len(yes_jobs)} job(s)\n")
    for j in yes_jobs:
        posted = f" | {j['posted']}" if j.get("posted") else ""
        lines.append(f"- [{j['company']}] {j['title']} | {j['location']}{posted}")
        lines.append(f"  {j['url']}")
        lines.append("")

    if maybe_jobs:
        lines.append("\nMAYBE bucket (review manually): " + str(len(maybe_jobs)) + " job(s)\n")
        for j in maybe_jobs:
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


def main(test_email: bool = False, no_email: bool = False, dry_run: bool = False) -> None:
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

    # Title classification buckets
    yes_matched = [j for j in normalized if classify_title(j.get("title", "")) == "yes"]
    maybe_matched = [j for j in normalized if classify_title(j.get("title", "")) == "maybe"]
    matched = yes_matched + maybe_matched

    # Test mode: send a small sample email to verify SMTP works.
    # This does NOT modify seen_ids.
    if test_email:
        sample_yes = yes_matched[:2]
        sample_maybe = maybe_matched[:1]
        if not (sample_yes or sample_maybe):
            raise RuntimeError("No matching jobs found to send in test email.")
        if no_email:
            print(f"[TEST] no-email enabled; would have sent {len(sample_yes) + len(sample_maybe)} job(s) to {ALERT_TO_EMAIL}.")
        else:
            send_email_digest(sample_yes, sample_maybe, subject_prefix="[TEST Job Alerts]")
            print(f"[TEST] Sent a test email with {len(sample_yes) + len(sample_maybe)} job(s) to {ALERT_TO_EMAIL}.")
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
        if not dry_run:
            save_seen_ids(STATE_PATH, seen)
        print(
            f"[BOOTSTRAP] Initialized sources: {', '.join(sorted(bootstrap_sources))}. "
            "No email for these sources this run."
        )

    # Bootstrap mode (first ever run): save state, do NOT email
    if not os.path.exists(STATE_PATH):
        if dry_run:
            print(f"[BOOTSTRAP] (dry-run) Would save {len(latest_keys)} seen_ids. No email sent.")
            return
        save_seen_ids(STATE_PATH, latest_keys)
        print(f"[BOOTSTRAP] Saved {len(latest_keys)} seen_ids. No email sent.")
        return

    new_keys = latest_keys - seen
    new_yes = [j for j in yes_matched if j.get("key") in new_keys]
    new_maybe = [j for j in maybe_matched if j.get("key") in new_keys]

    if new_yes or new_maybe:
        if no_email:
            print(f"[ALERT] no-email enabled; {len(new_yes)} yes + {len(new_maybe)} maybe new job(s) detected (not emailed).")
        else:
            send_email_digest(new_yes, new_maybe, subject_prefix="[Job Alerts]")
            print(f"[ALERT] Sent digest for {len(new_yes)} yes + {len(new_maybe)} maybe new job(s).")
    else:
        print("[OK] No new jobs.")

    if not dry_run:
        seen |= latest_keys
        save_seen_ids(STATE_PATH, seen)

    if errors:
        print("[WARN] Some sources failed (watcher still ran):")
        for e in errors:
            print("  -", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job watcher")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without saving any state/cursor/dead-board files (safe for testing).",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Do everything except sending email (still updates state/cursor).",
    )
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
        default=resolve_default_boards_csv(),
        help=(
            "Boards CSV path (must include company_name, platform, board_url). "
            "Default resolves via BOARDS_CSV env var or the best-available CSV under data/boards/."
        ),
    )
    parser.add_argument(
        "--boards-batch-size",
        type=int,
        default=50,
        help="How many boards to process per boards run (default: 50).",
    )
    parser.add_argument(
        "--export-dead-csv",
        default="",
        help="Optional: write a CSV report of dead boards (404) discovered so far.",
    )

    parser.add_argument(
        "--boards-timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds for boards adapters (default: 30).",
    )
    parser.add_argument(
        "--boards-run-until-wrap",
        action="store_true",
        help="In boards mode, keep running batches until cursor wraps to 0 (full sweep).",
    )
    parser.add_argument(
        "--boards-max-iterations",
        type=int,
        default=2000,
        help="Safety cap for --boards-run-until-wrap (default: 2000 iterations).",
    )

    args = parser.parse_args()

    if args.mode == "boards":
        seen = load_seen_ids(STATE_PATH)
        boards_seen = load_boards_seen(BOARDS_SEEN_PATH)
        dead_boards = load_boards_dead(BOARDS_DEAD_PATH)
        dead_details = load_dead_details(BOARDS_DEAD_DETAILS_PATH)

        # One-time CSV summary (helps confirm you're using the intended dataset)
        try:
            from collections import Counter

            _all_rows = load_boards_csv(args.boards_csv)
            _all_counts = Counter((r.get("platform") or "") for r in _all_rows)
            _supported_rows = [r for r in _all_rows if (r.get("platform") or "") in BOARDS_SUPPORTED_PLATFORMS]
            _skipped = len(_all_rows) - len(_supported_rows)

            print(
                f"[INFO] Boards CSV: {args.boards_csv} | total_rows={len(_all_rows)} | "
                f"supported_rows={len(_supported_rows)} | skipped_unsupported={_skipped}"
            )

            if _skipped:
                _unsupported_counts = Counter(
                    (r.get("platform") or "") for r in _all_rows
                    if (r.get("platform") or "") not in BOARDS_SUPPORTED_PLATFORMS
                )
                top = ", ".join(f"{k}:{v}" for k, v in _unsupported_counts.most_common(10))
                if top:
                    print(f"[INFO] Unsupported platforms in CSV (top): {top}")

        except Exception as e:
            print(f"[WARN] Could not summarize boards CSV: {type(e).__name__}: {e}")

        def run_one_boards_batch() -> int:

            matched, latest_keys, errors, new_cursor, bootstrap_keys, bootstrap_boards = run_boards_sweep(
                seen=seen,
                boards_seen=boards_seen,
                dead_boards=dead_boards,
                dead_details=dead_details,
                boards_csv=args.boards_csv,
                batch_size=args.boards_batch_size,
                timeout=args.boards_timeout,
            )

            if bootstrap_keys:
                seen.update(bootstrap_keys)
            if bootstrap_boards:
                boards_seen.update(bootstrap_boards)

            # Test email path (does not change seen_ids beyond bootstrap behavior)
            if args.test_email:
                sample_yes = [j for j in matched if classify_title(j.get("title", "")) == "yes"][:2]
                sample_maybe = [j for j in matched if classify_title(j.get("title", "")) == "maybe"][:1]
                if not (sample_yes or sample_maybe):
                    raise RuntimeError("No matching jobs found to send in test email.")
                if args.no_email:
                    print(f"[TEST] no-email enabled; would have sent {len(sample_yes) + len(sample_maybe)} job(s) to {ALERT_TO_EMAIL}.")
                else:
                    send_email_digest(sample_yes, sample_maybe, subject_prefix="[TEST Boards Alerts]")
                    print(f"[TEST] Sent a test boards email with {len(sample_yes) + len(sample_maybe)} job(s) to {ALERT_TO_EMAIL}.")
                if not args.dry_run:
                    save_boards_cursor(BOARDS_CURSOR_PATH, new_cursor)
                    save_boards_seen(BOARDS_SEEN_PATH, boards_seen)
                    save_boards_dead(BOARDS_DEAD_PATH, dead_boards)
                    save_dead_details(BOARDS_DEAD_DETAILS_PATH, dead_details)
                    export_dead_boards_csv(dead_details, args.export_dead_csv)

                if errors:
                    print("[WARN] Some boards failed:")
                    for e in errors:
                        print("  -", e)
                raise SystemExit(0)

            # First-ever boards run: bootstrap to avoid emailing historical postings
            if not os.path.exists(STATE_PATH):
                if args.dry_run:
                    print(f"[BOOTSTRAP] (dry-run) Would save {len(latest_keys)} seen_ids (boards). No email sent.")
                    raise SystemExit(0)
                save_seen_ids(STATE_PATH, latest_keys)
                save_boards_cursor(BOARDS_CURSOR_PATH, new_cursor)
                save_boards_seen(BOARDS_SEEN_PATH, boards_seen)
                save_boards_dead(BOARDS_DEAD_PATH, dead_boards)
                save_dead_details(BOARDS_DEAD_DETAILS_PATH, dead_details)
                export_dead_boards_csv(dead_details, args.export_dead_csv)
                print(f"[BOOTSTRAP] Saved {len(latest_keys)} seen_ids (boards). No email sent.")
                raise SystemExit(0)

            new_keys = latest_keys - seen
            new_yes = [j for j in matched if classify_title(j.get("title", "")) == "yes" and j.get("key") in new_keys]
            new_maybe = [j for j in matched if classify_title(j.get("title", "")) == "maybe" and j.get("key") in new_keys]

            if new_yes or new_maybe:
                if args.no_email:
                    print(f"[ALERT] no-email enabled; {len(new_yes)} yes + {len(new_maybe)} maybe new job(s) detected (not emailed).")
                else:
                    send_email_digest(new_yes, new_maybe, subject_prefix="[Boards Alerts]")
                    print(f"[ALERT] Sent boards digest for {len(new_yes)} yes + {len(new_maybe)} maybe new job(s).")
            else:
                print("[OK] No new boards jobs.")

            if not args.dry_run:
                seen.update(latest_keys)
                save_seen_ids(STATE_PATH, seen)
                save_boards_cursor(BOARDS_CURSOR_PATH, new_cursor)
                save_boards_seen(BOARDS_SEEN_PATH, boards_seen)
                save_boards_dead(BOARDS_DEAD_PATH, dead_boards)
                save_dead_details(BOARDS_DEAD_DETAILS_PATH, dead_details)
                export_dead_boards_csv(dead_details, args.export_dead_csv)

            if errors:
                print("[WARN] Some boards failed (sweep still ran):")
                for e in errors:
                    print("  -", e)

            return new_cursor

        if args.boards_run_until_wrap:
            it = 0
            try:
                while True:
                    it += 1
                    cur = run_one_boards_batch()
                    print(f"[PROGRESS] cursor_now: {cur}")
                    if cur == 0:
                        print("[DONE] cursor wrapped to 0 (full sweep completed).")
                        break
                    if it >= args.boards_max_iterations:
                        print(f"[STOP] Reached boards_max_iterations={args.boards_max_iterations} without wrap; stopping for safety.")
                        break
            except KeyboardInterrupt:
                print("\n[STOP] Interrupted by user (state saved up to last completed batch).")

        else:
            _ = run_one_boards_batch()
    else:
        main(test_email=args.test_email, no_email=args.no_email, dry_run=args.dry_run)