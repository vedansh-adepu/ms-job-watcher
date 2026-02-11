import json
import os
import argparse
import ssl
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone
from typing import Dict, List, Set

import requests

# ---- CONFIG (you can tweak later) ----

MS_ENDPOINT = "https://apply.careers.microsoft.com/api/pcsx/search"

MS_PARAMS = {
    "domain": "microsoft.com",
    "query": "",
    "location": "United States, Multiple Locations, Multiple Locations",
    "start": 0,
    "sort_by": "timestamp",
    "filter_include_remote": 1,
    "filter_seniority": ["Entry", "Mid-Level"],  # you can change/remove later
}

# ---- AMAZON CONFIG ----
AMZ_ENDPOINT = "https://www.amazon.jobs/en/search.json"

AMZ_PARAMS = {
    "category[]": ["machine-learning-science", "software-development"],
    "schedule_type_id[]": ["Full-Time"],
    "normalized_country_code[]": ["USA"],
    "radius": "100000km",
    "offset": 0,
    "result_limit": 10,
    "sort": "recent",
    # Optional geo/context fields (harmless if present)
    "latitude": 38.89036,
    "longitude": -77.03196,
    "loc_query": "united states",
    "base_query": "",
}

SUPPORTED_SOURCES = ["microsoft", "amazon"]

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

STATE_PATH = "state/seen.json"

# ---- EMAIL ENV VARS ----
# For local testing you can export them or use a .env method later.
EMAIL_USER = os.getenv("EMAIL_USER")          # sender gmail
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")  # gmail app password
ALERT_TO_EMAIL = os.getenv("ALERT_TO_EMAIL")  # receiver email


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


def title_matches(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in INCLUDE_TITLE_KEYWORDS)


def fetch_microsoft_positions() -> List[Dict]:
    headers = {
        "accept": "application/json",
        "user-agent": "Mozilla/5.0",
    }

    all_positions: List[Dict] = []
    start = 0

    # Eightfold search endpoints sometimes return 10/20/25/50 per page.
    # We avoid assuming a fixed page size. Instead, we increment by the
    # number of results returned each time and stop only when we get 0.
    safety_cap = 5000  # hard limit to prevent infinite loops

    while True:
        params = dict(MS_PARAMS)
        params["start"] = start

        r = requests.get(MS_ENDPOINT, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()

        positions = data.get("data", {}).get("positions", []) or []
        if not positions:
            break

        all_positions.extend(positions)

        # Advance by how many we actually received (no fixed step)
        start += len(positions)

        # Safety cap so we never loop forever
        if start >= safety_cap:
            break

    return all_positions


def fetch_amazon_positions() -> List[Dict]:
    headers = {
        "accept": "application/json",
        "user-agent": "Mozilla/5.0",
    }

    all_jobs: List[Dict] = []
    offset = 0
    limit = int(AMZ_PARAMS.get("result_limit", 10))
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

        offset += len(jobs)
        if offset >= safety_cap:
            break

    return all_jobs

def normalize_position(pos: Dict) -> Dict:
    # Stable key: company + id
    job_id = str(pos.get("id", ""))
    key = f"microsoft:{job_id}" if job_id else f"microsoft:url:{pos.get('applyUrl') or pos.get('positionUrl') or ''}"

    title = pos.get("name") or pos.get("title") or "Unknown Title"
    # locations sometimes is list of strings; standardizedLocations is often nicer
    loc = ""
    if isinstance(pos.get("standardizedLocations"), list) and pos["standardizedLocations"]:
        loc = pos["standardizedLocations"][0]
    elif isinstance(pos.get("locations"), list) and pos["locations"]:
        loc = pos["locations"][0]
    else:
        loc = "Unknown Location"

    posted_ts = pos.get("postedTs")  # often epoch seconds
    posted_str = ""
    if isinstance(posted_ts, (int, float)):
        posted_str = datetime.fromtimestamp(posted_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    url = pos.get("positionUrl") or pos.get("applyUrl") or "https://apply.careers.microsoft.com/careers"

    # Eightfold sometimes returns relative paths like "/careers/job/...".
    # Make them fully qualified so links are clickable in email.
    if isinstance(url, str) and url.startswith("/"):
        url = "https://apply.careers.microsoft.com" + url

    return {
        "key": key,
        "company": "Microsoft",
        "title": title,
        "location": loc,
        "posted": posted_str,
        "url": url,
    }


def normalize_amazon_job(job: Dict) -> Dict:
    # Try several common id fields
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

    key = f"amazon:{job_id}" if job_id else f"amazon:url:{job.get('url') or job.get('job_path') or ''}"

    title = job.get("title") or job.get("job_title") or job.get("name") or "Unknown Title"

    loc = (
        job.get("location")
        or job.get("normalized_location")
        or job.get("city")
        or job.get("primary_location")
        or "Unknown Location"
    )

    # Amazon often provides a posted_date string; keep as-is if present.
    posted_str = (
        job.get("posted_date")
        or job.get("postedDate")
        or job.get("posted")
        or ""
    )

    url = job.get("url") or job.get("job_path") or job.get("jobPath") or ""
    if isinstance(url, str) and url.startswith("/"):
        url = "https://www.amazon.jobs" + url
    if not url:
        url = "https://www.amazon.jobs/en/search"

    return {
        "key": key,
        "company": "Amazon",
        "title": title,
        "location": str(loc),
        "posted": str(posted_str),
        "url": url,
    }


def send_email_digest(new_jobs: List[Dict], subject_prefix: str = "[Job Alerts]") -> None:
    if not (EMAIL_USER and EMAIL_APP_PASSWORD and ALERT_TO_EMAIL):
        raise RuntimeError(
            "Missing EMAIL_USER / EMAIL_APP_PASSWORD / ALERT_TO_EMAIL env vars."
        )

    companies = sorted({j.get("company", "") for j in new_jobs if j.get("company")})
    company_str = ", ".join(companies) if companies else "Jobs"
    subject = f"{subject_prefix} {len(new_jobs)} new posting(s) ({company_str})"
    lines = []
    lines.append(f"Found {len(new_jobs)} new posting(s) ({company_str}):\n")

    for j in new_jobs:
        lines.append(
            f"- [{j['company']}] {j['title']} | {j['location']}" + (f" | {j['posted']}" if j.get("posted") else "")
        )
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


def main(test_email: bool = False) -> None:
    seen = load_seen_ids(STATE_PATH)

    ms_positions = fetch_microsoft_positions()
    print(f"[DEBUG] Fetched {len(ms_positions)} positions from Microsoft endpoint.")
    ms_norm = [normalize_position(p) for p in ms_positions]

    amz_positions = fetch_amazon_positions()
    print(f"[DEBUG] Fetched {len(amz_positions)} jobs from Amazon endpoint.")
    amz_norm = [normalize_amazon_job(j) for j in amz_positions]

    normalized = ms_norm + amz_norm

    # Match on titles (loose include)
    matched = [j for j in normalized if title_matches(j["title"])]

    # Test mode: send a small sample email to verify SMTP works.
    # This does NOT modify seen_ids.
    if test_email:
        sample = matched[:3]
        if not sample:
            raise RuntimeError("No matching jobs found to send in test email.")
        send_email_digest(sample, subject_prefix="[TEST Job Alerts]")
        print(f"[TEST] Sent a test email with {len(sample)} job(s) to {ALERT_TO_EMAIL}.")
        return

    latest_keys = {j["key"] for j in matched}
    new_keys = latest_keys - seen

    # Per-source bootstrap: if a source has never been seen before, don't email
    # all existing matches from that source on the first run after adding it.
    bootstrap_sources: Set[str] = set()
    for src in SUPPORTED_SOURCES:
        if not any(k.startswith(f"{src}:") for k in seen):
            bootstrap_sources.add(src)

    if bootstrap_sources:
        # Add those keys to seen immediately (quiet bootstrap)
        for src in bootstrap_sources:
            src_keys = {k for k in latest_keys if k.startswith(f"{src}:")}
            seen |= src_keys
        save_seen_ids(STATE_PATH, seen)
        print(f"[BOOTSTRAP] Initialized sources: {', '.join(sorted(bootstrap_sources))}. No email for these sources this run.")

        # Recompute new_keys after bootstrapping
        new_keys = latest_keys - seen

    # Bootstrap mode (first run): save state, do NOT email
    if not os.path.exists(STATE_PATH):
        save_seen_ids(STATE_PATH, latest_keys)
        print(f"[BOOTSTRAP] Saved {len(latest_keys)} seen_ids. No email sent.")
        return

    new_jobs = [j for j in matched if j["key"] in new_keys]

    if new_jobs:
        # one email per run (digest)
        send_email_digest(new_jobs)
        print(f"[ALERT] Sent digest for {len(new_jobs)} new job(s).")
    else:
        print("[OK] No new jobs.")

    # Update state regardless
    seen |= latest_keys
    save_seen_ids(STATE_PATH, seen)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Microsoft job watcher")
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send a test email using the latest 1-3 matching jobs (does not change seen_ids).",
    )
    args = parser.parse_args()

    main(test_email=args.test_email)