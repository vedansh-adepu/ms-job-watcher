#!/usr/bin/env python3
"""
Watchdog: checks that each pipeline has had a recent successful run.
Runs daily via GitHub-native schedule (independent of cron-job.org PAT).
Emails [Watcher ALERT] if any pipeline is stale.

API errors are skipped (we don't know the state — don't false-alarm).
"No successful runs found" is treated as stale (pipeline has never recovered).

Required env vars:
  GITHUB_TOKEN           auto-provided by Actions (github.token)
  GITHUB_REPOSITORY      auto-provided by Actions (owner/repo)
  EMAIL_USER             Gmail account
  EMAIL_APP_PASSWORD     Gmail app password
  ALERT_TO_EMAIL         recipient address
"""
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from urllib.error import URLError
from urllib.request import Request, urlopen

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")
ALERT_TO_EMAIL = os.getenv("ALERT_TO_EMAIL", "")

# (workflow_file, pipeline_name, stale_threshold_minutes)
# Threshold = 2× expected cadence: main fires every 10 min, boards* every 30 min.
PIPELINES = [
    ("watcher.yml",  "main",    20),
    ("boards.yml",   "boards",  60),
    ("boards2.yml",  "boards2", 60),
    ("boards3.yml",  "boards3", 60),
]

_API_ERROR = float("inf")    # could not query — skip
_NEVER_RAN = float("inf") - 1  # API responded but zero successful runs


def _gh_get(path: str) -> dict:
    url = f"https://api.github.com{path}"
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _last_success_age_min(workflow_file: str) -> float:
    """
    Returns minutes since last successful run.
    _API_ERROR if the API could not be reached (don't alert).
    _NEVER_RAN if the API responded but found zero successful runs (alert).
    """
    try:
        data = _gh_get(
            f"/repos/{GITHUB_REPOSITORY}/actions/workflows/{workflow_file}"
            f"/runs?status=success&per_page=1"
        )
    except (URLError, OSError, Exception) as e:
        print(f"[ERROR] API query failed for {workflow_file}: {e}", file=sys.stderr)
        return _API_ERROR
    runs = data.get("workflow_runs", [])
    if not runs:
        return _NEVER_RAN
    updated_at = runs[0].get("updated_at", "")
    if not updated_at:
        return _NEVER_RAN
    try:
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() / 60
    except Exception:
        return _NEVER_RAN


def _send_alert(stale_rows: list) -> None:
    subject = f"[Watcher ALERT] Stale pipeline(s): {', '.join(r['name'] for r in stale_rows)}"
    lines = [
        "The following pipelines have not had a successful run within their expected cadence:",
        "",
    ]
    for r in stale_rows:
        age = r["age"]
        if age == _NEVER_RAN:
            age_str = "never (no successful runs found)"
        else:
            age_str = f"{age:.0f} min ago"
        lines.append(
            f"  {r['name']:12}  last success: {age_str}  (threshold: {r['threshold']} min)"
        )
    lines += [
        "",
        "Most likely causes:",
        "  1. cron-job.org PAT expired or job disabled",
        "  2. Repeated run failures stalling the pipeline",
        "  3. GitHub Actions billing limit or outage",
        "",
        f"Check: https://github.com/{GITHUB_REPOSITORY}/actions",
    ]
    body = "\n".join(lines)

    if not (EMAIL_USER and EMAIL_APP_PASSWORD and ALERT_TO_EMAIL):
        print(f"[WARN] Email env vars missing — would have sent:\nSubject: {subject}\n\n{body}")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = ALERT_TO_EMAIL
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(EMAIL_USER, (EMAIL_APP_PASSWORD or "").replace(" ", ""))
        server.send_message(msg)


def main() -> None:
    if not GITHUB_REPOSITORY:
        print("[ERROR] GITHUB_REPOSITORY not set", file=sys.stderr)
        sys.exit(1)
    if not GITHUB_TOKEN:
        print("[ERROR] GITHUB_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    print(f"[watchdog] {now_str}  repo={GITHUB_REPOSITORY}")

    stale_rows = []
    for workflow_file, name, threshold in PIPELINES:
        age = _last_success_age_min(workflow_file)
        if age == _API_ERROR:
            print(f"  {name:12}  [SKIP] API error — state unknown")
            continue
        if age == _NEVER_RAN:
            age_label = "never"
            is_stale = True
        else:
            age_label = f"{age:.1f} min"
            is_stale = age > threshold
        status = "STALE" if is_stale else "ok"
        print(f"  {name:12}  last_success={age_label}  threshold={threshold}m  [{status}]")
        if is_stale:
            stale_rows.append({"name": name, "age": age, "threshold": threshold})

    if stale_rows:
        print(f"\n[ALERT] {len(stale_rows)} stale pipeline(s): {', '.join(r['name'] for r in stale_rows)}")
        try:
            _send_alert(stale_rows)
            print("[ALERT] Email sent.")
        except Exception as e:
            print(f"[ERROR] Failed to send alert: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("\n[OK] All pipelines healthy.")


if __name__ == "__main__":
    main()
