# ms-job-watcher — Architecture Reference

> Auto-generated from code audit on 2026-06-01. Re-run audit if watcher.py changes substantially.

---

## 1. Repo map

### Source code `[code]`
| File | Tag | Notes |
|---|---|---|
| `watcher.py` | [code] | Single-file core — 2,115 lines, all logic |
| `check_ashby_boards.py` | [code] | One-off discovery: probes a hardcoded SLUGS list against Ashby API, appends found boards to `ashby_new_boards.csv`. Not imported by watcher.py. |
| `migrate_workday_seen_keys.py` | [code] | One-time migration: back-fills `req:` keys for old Workday `url:` entries in seen state. No longer needed after it was run. Dead utility. |
| `verify_ashby.py` | [code] | Standalone verifier for Ashby boards CSV. Not imported by watcher.py. |
| `verify_greenhouse.py` | [code] | Standalone verifier for Greenhouse boards CSV. Not imported by watcher.py. |
| `verify_lever.py` | [code] | Standalone verifier for Lever boards CSV. Not imported by watcher.py. |
| `verify_smartrecruiters.py` | [code] | Standalone verifier for SmartRecruiters boards CSV. Not imported by watcher.py. |
| `verify_workday.py` | [code] | Standalone verifier for Workday boards CSV. Not imported by watcher.py. |
| `requirements.txt` | [config] | `requests`, `urllib3` only. |

### Scripts `[code]`
| File | Tag | Notes |
|---|---|---|
| `scripts/show_emailed.py` | [code] | CLI: union view of `state/emailed_*.json`. Options: `--since N` (days), `--pipeline X`, `--bucket yes\|maybe`, `--tail N`. Stdlib only. |
| `scripts/watchdog.py` | [code] | CLI: queries GitHub Actions API for each pipeline's last successful run; emails `[Watcher ALERT]` if any is stale. Stdlib only (no pip install). |

### GitHub Actions `[workflow]`
| File | Tag |
|---|---|
| `.github/workflows/watcher.yml` | [workflow] — main sources, every ~10 min (cron-job.org dispatch) |
| `.github/workflows/boards.yml` | [workflow] — ATS board sweep (1,200 boards), every ~30 min |
| `.github/workflows/boards2.yml` | [workflow] — GH+Lever shard (6,166 boards), every ~30 min |
| `.github/workflows/boards3.yml` | [workflow] — Workday shard A (2,325 boards), every ~30 min |
| `.github/workflows/watchdog.yml` | [workflow] — daily staleness check at 09:00 UTC, GitHub-native schedule (no cron-job.org PAT) |

### Config `[config]`
| File | Tag |
|---|---|
| `.gitignore` | [config] |
| `CLAUDE.md` | [doc] |
| `docs/STATE.md` | [doc] |
| `docs/ARCHITECTURE.md` | [doc] — this file |

### Live state files `[state]`
All in `state/`. See Section 5 for full detail.

| File | Tag | Role |
|---|---|---|
| `state/seen.json` | [state] | Main pipeline seen job IDs (6,357 entries) |
| `state/seen_boards.json` | [state] | Boards pipeline seen job IDs (41,881 entries) |
| `state/boards_seen.json` | [state] | Board IDs that have been bootstrapped (1,211 entries) |
| `state/boards_dead.json` | [state] | Permanently-dead board IDs (937 entries, 921 orphaned) |
| `state/boards_dead_details.json` | [state] | Per-board dead metadata (first/last seen, error, HTTP status) |
| `state/boards_cursor.json` | [state] | Current cursor position in the boards CSV (600) |
| `state/local_*.json` | [state] | Local dev state — gitignored, never committed |

### Runtime CSV (loaded by watcher.py) `[data-live]`
| File | Tag | Rows | Notes |
|---|---|---|---|
| `data/boards/JOB_BOARDS_PURE_WORKING_SUPPORTED_round2.csv` | [data-live] | 1,200 | **The only CSV loaded at runtime.** Resolved by `BOARDS_CSV` env var (boards.yml hardcodes this path), falling back to the first candidate that exists in `resolve_default_boards_csv()`. |

### Historical / staging CSVs — NOT loaded at runtime `[data-historical]`
These are pipeline artifacts from board curation. `watcher.py` never loads any of them directly. The `.gitignore` explicitly excludes several from being committed.

**In `data/boards/`:**
| File | Rows | Notes |
|---|---|---|
| `JOB_BOARDS_OK_PRODUCTION.csv` | 2,282 | Older production list; superseded. Gitignored. |
| `JOB_BOARDS_OK_PRODUCTION_MINUS_DEAD_round2.csv` | 1,381 | Intermediate; gitignored. |
| `JOB_BOARDS_PURE_WORKING_round2.csv` | 1,071 | Candidate fallback in resolution chain; not reached because the SUPPORTED variant loads first. |
| `JOB_BOARDS_NOT_WORKING_round2.csv` | 1,211 | Boards that failed during curation. |
| `DEAD_BOARDS.csv` / `DEAD_BOARDS_round2.csv` | ~901 each | Dead-board exports from curation. Gitignored. |
| `FAILED_BOARDS_workday_*.csv` | 149–310 | Workday probe failures. Gitignored. |
| `OK_BOARDS_workday_probe.csv` | 76 | Workday probe successes. Gitignored. |
| `JOB_BOARDS_NEEDS_HUMAN_PRODUCTION.csv` | 4 | Boards needing manual review. Gitignored. |
| `workday_tiny.csv` | 12 | Local test fixture. Gitignored. |
| `workday_debug/` | dir | Local Workday debug output. Gitignored. |

**At repo root (all historical staging, none loaded at runtime):**
| File | Rows | Columns | Notes |
|---|---|---|---|
| `ashby_new_boards.csv` | 170 | company, platform, board_url | Discovery output from `check_ashby_boards.py`. Not ingested into live CSV yet. |
| `ashby_us_verified.csv` | 49 | company, platform, board_url | Curated Ashby list. Not ingested. |
| `board_health_check.csv` | 905 | company, platform, board_url, http_status, job_count, result, error | Health-check output from `verify_*.py`. |
| `fortune-500-companies-2025.csv` | 499 | rank, company, industry, revenue, employees, headquarters, state | Source list for discovering boards. Not loaded by watcher.py. |
| `greenhouse_new_boards.csv` | 107 | company_name, platform, board_url | Discovery output. Not ingested. |
| `greenhouse_us_verified.csv` | 4,659 | company_name, platform, board_url | Large curated Greenhouse list. Not ingested. |
| `job_boards_NEEDS_HUMAN.csv` | 4 | company_name, candidate_board_url, blocker_reason, … | Manual review queue. Gitignored. |
| `job_boards_ok_with_meta.csv` | 355 | company_name, platform, board_url, ok, status_code, … | Early curation output. Gitignored. |
| `job_boards_ok_with_meta_phase1_clean*.csv` | 1,370–1,424 | company_name, platform, board_url, country_focus, notes | Intermediate pipeline stages. Gitignored. |
| `lever_new_boards.csv` | 30 | company_name, platform, board_url | Discovery output. Not ingested. |
| `lever_us_verified.csv` | 1,806 | company_name, platform, board_url | Large curated Lever list. Not ingested. |
| `smartrecruiters_us_verified.csv` | 210 | company, platform, board_url | Curated SR list. Not ingested. |
| `workday_us_verified.csv` | 4,770 | company, platform, board_url | Large curated Workday list. Not ingested. |

> **Root CSV verdict:** No root-level CSV is loaded at runtime. They are all staging artifacts. The ~11,000+ rows across these files represent a large pool of candidate boards that have never been ingested into the live CSV — a major untapped coverage opportunity.

---

## 2. watcher.py function index

### Workday URL helpers (lines 29–67)
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `_canon_locale` | `(seg: str) -> str` | Normalizes locale strings (en-us → en-US) | None |
| `_parse_workday_board` | `(board_url: str) -> Tuple[str, str, str]` | Extracts (origin, tenant, site) from a Workday URL | None |
| `_workday_cxs_endpoints` | `(board_url: str) -> Tuple[str, str]` | Returns (approot_url, jobs_url) for CXS API | None |
| `_is_workday_app_error` | `(text: str) -> bool` | Detects Workday XML error responses | None |

### CSV resolution (lines 84–119)
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `resolve_default_boards_csv` | `() -> str` | Walks candidate paths (env var first) and returns first existing CSV | None |

### Title filtering (lines 230–277)
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `_norm_title` | `(title: str) -> str` | Lowercases + collapses whitespace | None |
| `classify_title` | `(title: str) -> str` | Returns `"yes"` / `"maybe"` / `"no"` based on phrase lists | None |
| `title_matches` | `(title: str) -> bool` | Returns `True` for `yes` or `maybe` | None |

**Classifier rules (evaluated in order):**
1. Hard-exclude regexes first: `\bintern\b`, `\binternship\b`, `\bco[- ]?op\b`, `\bapprentice\b` → `"no"`
2. Hard-exclude phrases: "qa", "product manager", "sales", "marketing", "recruiter", "support engineer", etc. → `"no"` (SDET exemption for QA-family phrases)
3. Soft-exclude phrases ("devops", "ops", "automation") + strong include → `"maybe"`; + only weak → `"no"`
4. Seniority tokens ("senior", "staff", "principal", "lead", "architect", "director", "fellow") → `"maybe"`
5. Strong include phrase → `"yes"`; weak only → `"maybe"`

### HTTP session pooling (lines 316–349)
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `_make_session` | `() -> requests.Session` | Creates a session with retry adapter (3 retries, backoff 0.4s, respects 429 Retry-After, status_forcelist: 429/500/502/503/504) | None |
| `_get_session` | `(bucket: str = "default") -> requests.Session` | Returns thread-local session for a named bucket (one per thread per platform) | None |

### State I/O (lines 543–692)

**Schema validation (added 2026-07-08):** All loaders distinguish parse errors from schema errors. JSON parse failure on `seen_ids` → `[FATAL]` + exit 1 (empty fallback = re-alert flood). Parse failure on `cursor`/`boards_seen`/`boards_dead` → `[ERROR]` + safe default (these recover next run; transient rebase truncation). Wrong schema type on any loader → `[FATAL]` + exit 1 with file/expected/actual in message.

| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `_atomic_write_json` | `(path, payload, indent)` | Writes JSON atomically via tempfile + `os.replace` | **Writes file** |
| `load_seen_ids` | `(path: str) -> Set[str]` | Reads `seen_ids` list. Parse fail → fatal exit. Schema wrong → fatal exit. | Reads file |
| `save_seen_ids` | `(path: str, seen_ids: Set[str])` | Persists sorted seen_ids set | **Writes file** |
| `load_boards_cursor` | `(path: str) -> int` | Reads integer cursor. Parse fail → `[ERROR]` + return 0. Schema wrong → fatal exit. | Reads file |
| `save_boards_cursor` | `(path: str, cursor: int)` | Persists cursor | **Writes file** |
| `load_boards_seen` | `(path: str) -> Set[str]` | Reads bootstrapped board ID set. Parse fail → `[ERROR]` + return ∅. Schema wrong → fatal exit. | Reads file |
| `save_boards_seen` | `(path: str, boards_seen: Set[str])` | Persists board ID set | **Writes file** |
| `load_boards_dead` | `(path: str) -> Set[str]` | Reads dead board ID set. Parse fail → `[ERROR]` + return ∅. Schema wrong → fatal exit. | Reads file |
| `save_boards_dead` | `(path: str, boards_dead: Set[str])` | Persists dead board ID set | **Writes file** |
| `load_dead_details` | `(path: str) -> Dict` | Reads dead board detail records | Reads file |
| `save_dead_details` | `(path: str, dead_details: Dict)` | Persists dead board detail records | **Writes file** |
| `upsert_dead_detail` | `(dead_details, *, board_id, platform, company, board_url, status, error)` | Updates a single dead board record in-memory | None (caller must save) |
| `export_dead_boards_csv` | `(dead_details, out_path: str)` | Writes dead board CSV report | **Writes file** (only if `--export-dead-csv` passed) |

### Tracking layer — health monitoring & job storage (added 2026-07-08)

| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `_send_plain_email` | `(subject, body) -> None` | Sends plain-text alert via Gmail SMTP SSL port 465. Used for `[Watcher ALERT]` messages — distinct subject prefix from job digest emails so it can't get lost. | **Network: SMTP** |
| `_load_source_health` | `(pipeline: str) -> Dict[str, int]` | Reads `state/source_health_{pipeline}.json`; returns `{source: consecutive_zeros}`. Parse errors reset to `{}` with `[ERROR]` (health-counter loss is acceptable; pipeline must keep running). | Reads file |
| `_save_source_health` | `(pipeline: str, zeros: Dict[str, int])` | Persists consecutive-zero counters atomically. | **Writes file** |
| `_check_source_health` | `(per_source, pipeline, no_email, dry_run) -> None` | Main-mode alarm: for each SUPPORTED_SOURCE, increments zero counter if `fetched==0` (errors skip the counter), resets to 0 otherwise. Sends one aggregate `[Watcher ALERT]` email if any source ≥3 consecutive zeros. | **Network: SMTP** (conditional), **Writes file** |
| `_append_emailed_records` | `(yes_jobs, maybe_jobs, ts_utc) -> None` | Appends one record per emailed job to `state/emailed_{PIPELINE_NAME}.json`. Schema: `{job_id, pipeline, platform, company, title, location, posted, url, bucket, first_seen_utc, emailed_utc}`. No-ops if `PIPELINE_NAME` unset. | **Writes file** |
| `_append_run_log` | `(record: Dict) -> None` | Appends to `state/run_log.json` (bounded 1,000 entries). Parse failure → `[ERROR]` + `[]` fallback; wrong schema type → fatal exit. **Note:** shared across all pipelines — concurrent bot pushes cause lost-update races; boards2 showed 0 entries despite 273 actual runs. Use `gh run list` not run_log.json for cadence verification. | **Writes file** |

### Boards CSV (lines 698–738)
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `load_boards_csv` | `(path: str) -> List[Dict]` | Loads CSV, filters blank/unsupported rows, deduplicates by (platform, url) | Reads file |

### Location filtering (lines 741–828)
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `make_location` | `(parts: List[str]) -> str` | Joins non-empty parts with ", " | None |
| `is_us_location` | `(location: str) -> bool` | Heuristic US detection: checks for "United States", "usa", "\bus\b", state abbreviations after commas. Rejects ISO country codes and full country names. | None |

### Eightfold (Microsoft + NVIDIA) — lines 834–916
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `eightfold_key_from_pos` | `(source, pos) -> str` | Builds stable key: `"{source}:{id}"` or `"{source}:url:{url}"` | None |
| `fetch_eightfold_positions` | `(source, seen_keys, max_positions) -> List` | Paginates Eightfold REST API. Early-stops if a full page is already in `seen_keys`. Cap: 300 positions. | **Network: GET** |
| `normalize_eightfold_position` | `(source, pos) -> Dict` | Returns standard `{key, company, title, location, posted, url}` dict | None |

### Amazon — lines 922–994
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `amazon_key_from_job` | `(job) -> str` | Builds key: `"amazon:{id}"` or `"amazon:url:{url}"` | None |
| `fetch_amazon_positions` | `(seen_keys, max_positions) -> List` | Paginates Amazon Jobs REST API. Early-stops if full page already seen. Cap: 300. | **Network: GET** |
| `normalize_amazon_job` | `(job) -> Dict` | Returns standard dict | None |

### Goldman Sachs — lines 1000–1032
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `gs_key_from_item` | `(item) -> str` | Builds key: `"goldman_sachs:{roleId}"` | None |
| `fetch_goldman_sachs` | `(seen_keys, max_positions) -> List` | Paginates GraphQL via `pageNumber` increment. Stops on empty page, partial page, all-in-seen, or cap 200. | **Network: POST** |
| `normalize_goldman_item` | `(item) -> Dict` | Returns standard dict | None |

### IBM — lines 1038–1094
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `ibm_key_from_hit` | `(hit) -> str` | Builds key: `"ibm:{_id}"` or `"ibm:url:{url}"` | None |
| `fetch_ibm` | `(seen_keys, max_positions) -> List` | Paginates via `from` offset (Elasticsearch). Stops on empty page, partial page, all-in-seen, or cap 200. Retries once without `aggs` field on HTTP 400. | **Network: POST** |
| `normalize_ibm_hit` | `(hit) -> Dict` | Returns standard dict | None |

### Oracle — lines 1100–1139
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `oracle_key_from_req` | `(req) -> str` | Builds key: `"oracle:{requisitionId}"` or `"oracle:url:{url}"` | None |
| `fetch_oracle` | `(seen_keys, max_positions) -> List` | Paginates via `limit=50,offset=N` embedded in finder query string. Extracts jobs from `items[0]["requisitionList"]`. Stops on empty page, partial page, all-in-seen, or cap 200. | **Network: GET** |
| `normalize_oracle_req` | `(req) -> Dict` | Returns standard dict | None |

### Workday boards — lines 1148–1354
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `workday_tenant_from_host` | `(host) -> str` | Extracts subdomain as tenant | None |
| `workday_site_from_board_url` | `(board_url) -> str` | Extracts site path segment, skipping locale | None |
| `workday_locale_from_board_url` | `(board_url) -> str` | Extracts or defaults locale to `en-US` | None |
| `workday_normalize_external_job_url` | `(board_url, external) -> str` | Reconstructs full job URL from partial paths (`/job/...` → `/en-US/{site}/job/...`) — handles 5 path shapes | None |
| `workday_board_id` | `(board_url) -> str` | Returns stable board ID: `"workday:{tenant}:{site}"` | None |
| `workday_key_from_post` | `(tenant, site, post, url) -> str` | Builds `"workday:{tenant}:{site}:{jobPostingId}"` or falls back to req-number regex or URL | None |
| `fetch_workday_jobs` | `(board_url, max_positions, timeout) -> List` | GETs approot (boot), then paginates CXS jobs POST. Cap: 500. Raises on XML error or non-JSON response. | **Network: GET + POST** |
| `normalize_workday_post` | `(company_name, board_url, post) -> Dict` | Returns standard dict, uses `workday_normalize_external_job_url` for URL | None |

### SmartRecruiters — lines 1360–1437
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `smartrecruiters_company_from_board_url` | `(board_url) -> str` | Extracts company slug from path | None |
| `smartrecruiters_board_id` | `(board_url) -> str` | Returns `"smartrecruiters:{slug}"` | None |
| `smartrecruiters_key_from_post` | `(company_slug, post) -> str` | Builds `"smartrecruiters:{slug}:{id}"` | None |
| `fetch_smartrecruiters_jobs` | `(board_url, max_positions, timeout) -> List` | Paginates SmartRecruiters public API. Cap: 500. | **Network: GET** |
| `normalize_smartrecruiters_post` | `(company_name, board_url, post) -> Dict` | Returns standard dict | None |

### Greenhouse + Lever — lines 1443–1512
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `greenhouse_slug_from_board_url` | `(board_url) -> str` | Extracts slug from path | None |
| `lever_slug_from_board_url` | `(board_url) -> str` | Extracts slug from path | None |
| `gh_key` | `(company_slug, job_id) -> str` | Returns `"greenhouse:{slug}:{id}"` | None |
| `lever_key` | `(company_slug, job_id) -> str` | Returns `"lever:{slug}:{id}"` | None |
| `fetch_greenhouse_jobs` | `(company_slug, timeout) -> List` | Single GET to Greenhouse boards API — **no pagination** (API returns all jobs at once) | **Network: GET** |
| `fetch_lever_jobs` | `(company_slug, timeout) -> List` | Single GET to Lever postings API — **no pagination** (API returns all jobs at once) | **Network: GET** |
| `normalize_greenhouse_job` | `(company_name, company_slug, job) -> Dict` | Returns standard dict | None |
| `normalize_lever_job` | `(company_name, company_slug, job) -> Dict` | Returns standard dict | None |

### Ashby — lines 1528–1572
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `ashby_slug_from_board_url` | `(board_url) -> str` | Extracts slug from path | None |
| `ashby_key` | `(company_slug, job_id) -> str` | Returns `"ashby:{slug}:{id}"` | None |
| `fetch_ashby_jobs` | `(company_slug, timeout) -> List` | Single GraphQL POST — **no pagination** (returns all postings at once). Synthesizes a fake 404 HTTPError if board returns `null`. | **Network: POST** |
| `normalize_ashby_job` | `(company_name, company_slug, job) -> Dict` | Returns standard dict. URL built as `https://jobs.ashbyhq.com/{slug}/{id}` | None |

### Boards orchestration — lines 1578–1786
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `_board_id_for` | `(platform, board_url) -> Tuple[str, str]` | Dispatches to per-platform board ID + slug extraction | None |
| `_is_dead_http_status` | `(status) -> bool` | Returns True for 404 or 410 | None |
| `_process_single_board` | `(b, boards_seen, dead_boards, timeout, suppress_new_boards) -> Tuple` | Fetches + normalizes one board. Skips if in dead_boards. Handles bootstrap suppression for new boards. Catches HTTPError (404/410 → dead) and all other exceptions (logged as warnings). | **Network: GET/POST** per platform |
| `run_boards_sweep` | `(seen, boards_seen, dead_boards, dead_details, boards_csv, batch_size, timeout, workers, suppress_new_boards) -> Tuple` | Loads CSV, slices batch by cursor, dispatches `_process_single_board` via `ThreadPoolExecutor`, collects results, filters by title + US location. Returns matched jobs, new keys, errors, next cursor, bootstrap data. | **Network (concurrent)**, prints PERF summary |

### Email — lines 1792–1833
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `send_email_digest` | `(yes_jobs, maybe_jobs, subject_prefix) -> None` | Sends plain-text email via Gmail SMTP SSL (port 465). Subject: `"[prefix] N yes + M maybe (Company1, …)"`. Body: YES bucket then MAYBE bucket, each with company, title, location, posted, URL. | **Network: SMTP** |

### Main pipeline — lines 1839–1958
| Function | Signature | Purpose | Side effects |
|---|---|---|---|
| `safe_call` | `(label, fn) -> Tuple[result, error]` | Wraps a callable; returns (result, None) or (None, error_string) | None |
| `main` | `(test_email, no_email, dry_run) -> None` | Orchestrates all 6 main-source fetches, classifies, deduplicates, sends digest, saves state. | **Network**, **file writes** |

---

## 3. Runtime traces

### `--mode main` (watcher.yml, every ~20 min)

```
1. load_seen_ids(STATE_PATH="state/seen.json")          → Set[str] (6,357 IDs)

2. Fetch all 6 sources in sequence (NOT concurrent):
   a. fetch_eightfold_positions("microsoft", seen_keys=seen)  → GET https://apply.careers.microsoft.com/api/pcsx/search
      paginate until: no results | cap 300 | full page already in seen
   b. fetch_eightfold_positions("nvidia", seen_keys=seen)     → GET https://nvidia.eightfold.ai/api/pcsx/search
      paginate until: no results | cap 300 | full page already in seen
   c. fetch_amazon_positions(seen_keys=seen)                  → GET https://www.amazon.jobs/en/search.json
      paginate until: no results | cap 300 | full page already in seen
   d. fetch_goldman_sachs(seen_keys=seen)                     → POST https://api-higher.gs.com/gateway/api/v1/graphql
      paginate via pageNumber increment until: empty | partial page | all-in-seen | cap 200
   e. fetch_ibm(seen_keys=seen)                               → POST https://www-api.ibm.com/search/api/v2
      paginate via `from` offset (Elasticsearch) until: empty | partial page | all-in-seen | cap 200
   f. fetch_oracle(seen_keys=seen)                            → GET https://eeho.fa.us2.oraclecloud.com/hcmRestApi/...
      paginate via limit=50,offset=N in finder string; extracts items[0]["requisitionList"] until: empty | partial | all-in-seen | cap 200
   Each wrapped in safe_call() → source failure is logged but doesn't abort.

3. normalize all raw results → List[{key, company, title, location, posted, url}]

4. classify_title(title) for each → "yes" / "maybe" / "no"

5. ── EMAIL SUPPRESSED IF: ──────────────────────────────────────────────────
   a. BOOTSTRAP: any source has NO keys in seen → add all its keys silently,
      save seen.json, skip email for that source this run.
   b. --test-email: sends sample (2 yes + 1 maybe) with [TEST] prefix, returns.
   c. --no-email: logs count but skips SMTP.
   d. No new jobs (new_keys = latest_keys - seen is empty).

6. new_yes  = [j for j in yes_matched  if j.key in new_keys]
   new_maybe = [j for j in maybe_matched if j.key in new_keys]
   If non-empty → send_email_digest(new_yes, new_maybe, "[Job Alerts]")

7. seen |= latest_keys
   save_seen_ids("state/seen.json", seen)
```

### `--mode boards` (boards.yml, every ~30 min)

```
1. load_seen_ids(STATE_PATH="state/seen_boards.json")   → Set[str] (41,881 IDs)
   load_boards_seen("state/boards_seen.json")           → Set[str] (1,211 board IDs)
   load_boards_dead("state/boards_dead.json")           → Set[str] (937 board IDs — 921 orphaned)
   load_dead_details("state/boards_dead_details.json")  → Dict
   load_boards_cursor("state/boards_cursor.json")       → int (600)

2. load_boards_csv("data/boards/JOB_BOARDS_PURE_WORKING_SUPPORTED_round2.csv")
   → 1,200 rows filtered + deduped by (platform, url)
   filter: keep only BOARDS_SUPPORTED_PLATFORMS = (greenhouse, lever, smartrecruiters, workday, ashby)

3. Cursor slice:
   start = cursor % 1200  →  600
   end   = min(600 + 200, 1200)  →  800
   batch = boards[600:800]        →  200 boards

4. ThreadPoolExecutor(max_workers=12) dispatches _process_single_board for all 200:
   Per board:
     a. board_id = _board_id_for(platform, board_url)
     b. if board_id in dead_boards → return immediately (skip — ~16 boards currently)
     c. acquire _PLATFORM_SEMAPHORES[platform] (GH=8, Lever=8, SR=6, WD=4, Ashby=6)
     d. Fetch jobs:
        greenhouse  → GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
        lever       → GET https://jobs.lever.co/v0/postings/{slug}?mode=json
        smartrecr.  → GET https://api.smartrecruiters.com/v1/companies/{slug}/postings (paginated, limit=100)
        ashby       → POST https://jobs.ashbyhq.com/api/non-user-graphql (GraphQL, all at once)
        workday     → GET {approot} (boot cookie) + POST {cxs}/jobs (paginated, limit=20)
     e. normalize → List[{key, company, title, location, posted, url}]

     ── BOOTSTRAP SUPPRESSION ──────────────────────────────────────────────
     f. if board_id NOT in boards_seen AND suppress_new_boards=True:
           bootstrap_keys = all matched job keys from this board
           norm_jobs = []  ← no jobs emitted this run
           boards_seen.add(board_id)
        (first-ever sweep of a board: jobs absorbed silently into seen, no alert)

     g. HTTPError 404/410 → board_id added to dead_boards (PERMANENT, single-strike)
        other exceptions  → logged as warning, board not marked dead

5. After all futures complete:
   matched = [j for j in all_norm_jobs if title_matches(j.title) AND is_us_location(j.location)]
   latest_keys = {j.key for j in matched}

   ── EMAIL SUPPRESSED IF: ────────────────────────────────────────────────
   a. STATE_PATH ("state/seen_boards.json") does not exist yet → bootstrap, save, no email.
   b. --test-email → sends sample (2 yes + 1 maybe) with [TEST Boards Alerts] prefix.
   c. --no-email → logs count, skips SMTP.
   d. new_keys = latest_keys - seen is empty.

6. new_yes   = [j for j in matched if classify_title(j.title)=="yes"  and j.key in new_keys]
   new_maybe = [j for j in matched if classify_title(j.title)=="maybe" and j.key in new_keys]
   If non-empty → send_email_digest(new_yes, new_maybe, "[Boards Alerts]")

7. seen |= latest_keys ∪ bootstrap_keys
   save_seen_ids("state/seen_boards.json", seen)
   save_boards_cursor("state/boards_cursor.json", new_cursor=800)
   save_boards_seen("state/boards_seen.json", boards_seen)
   save_boards_dead("state/boards_dead.json", dead_boards)
   save_dead_details("state/boards_dead_details.json", dead_details)

8. GitHub Actions step commits: git add state/*.json → git commit "Update boards state"
   Push with 5-retry loop using `git merge -X ours` to resolve concurrent bot conflicts.
```

---

## 4. External surface

### Main pipeline sources

| Source | Method | Endpoint | Auth | Filters in request | Semaphore / Cap |
|---|---|---|---|---|---|
| Microsoft | GET | `https://apply.careers.microsoft.com/api/pcsx/search` | None (public) | US location, Entry+Mid-Level seniority, sorted by timestamp | None / 300 positions |
| NVIDIA | GET | `https://nvidia.eightfold.ai/api/pcsx/search` | None (public) | US location, full-time, regular employee, engineering job categories | None / 300 positions |
| Amazon | GET | `https://www.amazon.jobs/en/search.json` | None (public) | USA, ML+SWE categories, full-time, sorted recent | None / 300 jobs, page size 50 |
| Goldman Sachs | POST GraphQL | `https://api-higher.gs.com/gateway/api/v1/graphql` | None (public) | NYC/Boston/DC/SF/McLean locations, Software Engineering function, Early+Professional career | None / cap 200, page size 20, paginated by pageNumber |
| IBM | POST JSON | `https://www-api.ibm.com/search/api/v2` | None (public) | Software Engineering + Data & Analytics, Entry Level, United States | None / cap 200, page size 30, paginated by `from` offset |
| Oracle | GET | `https://eeho.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions` | None (public) | US location, tech categories, 0-2 years exp, posted last 7 days | None / cap 200, page size 50, paginated via limit/offset in finder string |

### Boards pipeline ATS APIs

| Platform | Method | Endpoint pattern | Auth | Pagination | Semaphore |
|---|---|---|---|---|---|
| Greenhouse | GET | `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` | None (public) | None needed — single response contains all jobs | 8 concurrent |
| Lever | GET | `https://jobs.lever.co/v0/postings/{slug}?mode=json` | None (public) | None needed — single array response | 8 concurrent |
| SmartRecruiters | GET | `https://api.smartrecruiters.com/v1/companies/{slug}/postings` | None (public) | offset/limit=100, up to 500 | 6 concurrent |
| Workday | GET+POST | `{origin}/wday/cxs/{tenant}/{site}/approot` + `…/jobs` | None (public) | limit=20, offset, up to 500 (CXS clamps limit at 20 regardless of requested value) | 4 concurrent |
| Ashby | POST GraphQL | `https://jobs.ashbyhq.com/api/non-user-graphql` | None (public) | None needed — returns all postings | 6 concurrent |

**Email sending:** Gmail SMTP SSL, port 465. Auth via `EMAIL_USER` + `EMAIL_APP_PASSWORD` (app password, not account password).

### Boards platform fetch cost & change-detection (measured 2026-06-02)

| Platform | API calls / board | HTTP verb | Change-detection path | Notes |
|---|---|---|---|---|
| Greenhouse | **1 GET** | GET | **Easy — ETag/304** conditional request; server returns 304 if board unchanged → zero payload | Single response, no pagination |
| Lever | **1 GET** | GET | **Easy — ETag/304** same as Greenhouse | Single array response, no pagination |
| SmartRecruiters | **1–5 GETs** | GET | **Partial** — response likely contains `totalFound`; read it on page 1 and bail early if count unchanged | limit=100, max 5 pages to hit 500-job cap |
| Ashby | **1 POST** | POST (GraphQL) | **None via HTTP** — POST prevents ETag/304; no timestamps in current query (`id title locationName workplaceType employmentType`); would need app-level ID-set caching | Single payload, no pagination |
| Workday | **1 GET (boot) + N POSTs** | GET + POST | **None via HTTP** — jobs endpoint is POST; boot GET could theoretically cache but isn't the bottleneck; no total-count field currently read; would need app-level count caching | N = ceil(total\_jobs/20); max 25 POSTs + 1 boot = 26 calls on a 500-job board |

**Throughput & timeout headroom (200-board/run baseline, measured from 14 boards runs):**
- Duration range: 68–126s; average ~95s; p90 ~116s; **timeout budget used: ~14% of 900s**.
- Throughput: ~2.1 boards/sec average; ~18 boards/sec on batches with zero Workday boards.
- Workday is the pace-setter: semaphore=4, 4–26 calls/board. The batch 1000–1200 (0 WD boards) ran in 11.2s; batches with 20–25 WD boards take 100–126s.
- At batch_size=200 and 5k total boards: per-run time unchanged (~95s); full-cycle latency would grow to ~12.5 hours (25 runs × 30 min) vs. current ~3 hours.

**Workday production cost (boards3, measured 2026-07-09):**
- **Avg wall time: 75.2s/board** at WD_SEM=4 — **1.86× slower than the sizing probe** (40.5s/board). The sizing report's `batch_size=50` recommendation contradicted its own p90 math. Any future Workday shard must start at `batch_size=20`.
- **Per-board API cost:** avg 21.5 calls/board; p90 = 26 calls (= 1 boot GET + 25 POSTs); p90 wall time = 107.7s/board.
- **CXS pagination:** `limit` is clamped at 20 per page by the server regardless of the requested value. A 500-job board requires 25 POSTs + 1 boot GET = 26 total calls.
- **boards3 at batch_size=20:** p90 estimate = 20 × 107.7 / 4 = 539s (9.0 min, 60% of 15-min budget). Full cycle: 2,325 ÷ 20 = 117 runs × 30 min = **58.5h ≈ 2.4 days**.
- **Duration guard:** `[WARN]` printed if any boards-mode run exceeds 720s (12 min). Boards3 is the only pipeline expected to approach this.

---

## 5. Data & state files

### Live runtime CSV

**`data/boards/JOB_BOARDS_PURE_WORKING_SUPPORTED_round2.csv`**
- Columns: `company_name`, `platform`, `board_url`, `country_focus`, `notes`
- Rows: 1,200 (after dedup by (platform, url))
- Platform breakdown: Greenhouse 532 · SmartRecruiters 322 · Ashby 175 · Lever 94 · Workday 77
- Load criteria in `load_boards_csv`: skip rows where `ok` column exists and is not `true/1/yes`; skip blank company/platform/url; dedup by (platform, url.rstrip("/"))
- **Not gitignored** — committed and versioned

### State JSON files

| File | Format | Current value | Written by |
|---|---|---|---|
| `state/seen.json` | `{updated_utc, seen_ids: [...]}` | 6,357 IDs. Breakdown: amazon 2,567 · greenhouse 2,151 · microsoft 801 · nvidia 511 · lever 242 · ibm 49 · goldman_sachs 36 | `watcher.yml` (main mode) |
| `state/seen_boards.json` | `{updated_utc, seen_ids: [...]}` | 41,881 IDs from boards pipeline | `boards.yml` (boards mode, via `STATE_PATH` env override) |
| `state/boards_seen.json` | `{updated_utc, boards_seen: [...]}` | 1,211 board IDs (ashby 175 · greenhouse 542 · lever 94 · smartrecruiters 322 · workday 78) | `boards.yml` |
| `state/boards_dead.json` | `{updated_utc, boards_dead: [...]}` | 937 board IDs (greenhouse 757 · lever 132 · workday 46 · ashby 2). **921 are orphaned** — stale entries from boards removed from earlier CSV versions. Only 16 overlap with the current live CSV. | `boards.yml` |
| `state/boards_dead_details.json` | `{updated_utc, dead_details: {board_id: {first_seen_utc, last_seen_utc, platform, company, board_url, last_status, last_error}}}` | Per-dead-board metadata | `boards.yml` |
| `state/boards_cursor.json` | `{updated_utc, cursor: int}` | 600 — next batch starts at row 600 | `boards.yml` |
| `state/seen_boards2.json` | `{updated_utc, seen_ids: [...]}` | boards2 pipeline job dedup (6,166-board GH+Lever shard) | `boards2.yml` |
| `state/seen_boards3.json` | `{updated_utc, seen_ids: [...]}` | boards3 pipeline job dedup (2,325-board Workday shard A) | `boards3.yml` |
| `state/boards2_cursor.json` / `state/boards3_cursor.json` | `{updated_utc, cursor: int}` | Per-shard cursor positions | `boards2.yml` / `boards3.yml` |
| `state/boards2_seen.json` / `state/boards3_seen.json` | `{updated_utc, boards_seen: [...]}` | Per-shard bootstrapped board IDs | `boards2.yml` / `boards3.yml` |
| `state/boards2_dead.json` / `state/boards3_dead.json` | `{updated_utc, boards_dead: [...]}` | Per-shard dead board IDs | `boards2.yml` / `boards3.yml` |
| `state/emailed_{pipeline}.json` | `[{job_id, pipeline, platform, company, title, location, posted, url, bucket, first_seen_utc, emailed_utc}]` | Per-pipeline emailed job history. One file per pipeline (main, boards, boards2, boards3). Written by `_append_emailed_records`. Never truncated — append-only. | All workflows |
| `state/source_health_{pipeline}.json` | `{updated_utc, consecutive_zeros: {source: int}}` | Per-pipeline silent-source alarm counters. Main mode: key per SUPPORTED_SOURCE. Boards mode: key `"batch"`. Written after every non-dry-run. Parse/schema errors reset to `{}` with `[ERROR]` (counter loss accepted). | All workflows |
| `state/run_log.json` | `[{ts, mode, per_source: {src: {fetched, title_ok, loc_ok, new, emailed, error}}, duration_s, cursor}]` | Shared per-run funnel log, bounded to 1,000 records. **Known limitation:** shared across all pipelines; concurrent bot pushes cause lost-update races — boards2 showed 0 entries despite 273 actual runs. Use `gh run list --workflow=X.yml` for reliable cadence data, not run_log.json. | All workflows |
| `state/local_*.json` | various | Local dev only | Local scripts. Gitignored. |

---

## 6. GitHub Actions workflows

### `watcher.yml` — Job Watcher (Main Sources)

```yaml
schedule: '13 */3 * * *'   # sparse fallback only — primary trigger is cron-job.org workflow_dispatch (every 10 min)
concurrency: job-watcher-main (cancel-in-progress: false)
timeout-minutes: 15
runs-on: ubuntu-latest
python: 3.11
```

**Primary trigger:** cron-job.org → `POST /repos/{owner}/{repo}/actions/workflows/watcher.yml/dispatches` every 10 min. Auth = fine-grained PAT (Actions:write), **expires 2026-08-31**.
**Secrets used:** `EMAIL_USER`, `EMAIL_APP_PASSWORD`, `ALERT_TO_EMAIL`
**Env vars set:** `HTTP_TIMEOUT=30`
**Command:** `python watcher.py --mode main`
**State committed:** `state/seen.json` (via `git add state/*.json`)
**Commit message:** `"Update watcher state"`

### `boards.yml` — Job Boards Sweep (Broad Lane)

```yaml
schedule: '13 */3 * * *'   # sparse fallback only — primary trigger is cron-job.org workflow_dispatch (every 30 min)
concurrency: job-watcher-boards (cancel-in-progress: false)
timeout-minutes: 15
runs-on: ubuntu-latest
python: 3.11
```

**Primary trigger:** cron-job.org → `POST /repos/{owner}/{repo}/actions/workflows/boards.yml/dispatches` every 30 min. Auth = same PAT as watcher, **expires 2026-08-31**.

**Secrets used:** `EMAIL_USER`, `EMAIL_APP_PASSWORD`, `ALERT_TO_EMAIL`
**Env vars set:** `BOARDS_CSV=data/boards/JOB_BOARDS_PURE_WORKING_SUPPORTED_round2.csv`, `HTTP_TIMEOUT=15`, `STATE_PATH=state/seen_boards.json`
**Command:** `python watcher.py --mode boards --boards-batch-size 200`
**State committed:** `state/boards_cursor.json`, `state/boards_dead.json`, `state/boards_dead_details.json`, `state/boards_seen.json`, `state/seen_boards.json`
**Commit message:** `"Update boards state"`

### Push conflict handling (both workflows)
```bash
for i in 1 2 3 4 5; do
    git fetch origin main
    git merge -X ours origin/main || true   # always prefer local state on conflict
    if git push; then exit 0; fi
    sleep $((i*5))
done
```
The `git merge -X ours` strategy means the local run's state always wins on conflict. If two runs finish simultaneously and both try to push, the second push will overwrite any state committed by the first that isn't in its own state files. In practice this is fine since the two workflows write disjoint files (`seen.json` vs `seen_boards.json` et al.).

### Actual run cadence

**Pre-fix baseline (last 50 runs, up to 2026-06-01):**

| Workflow | Scheduled | Median gap | p90 gap | Max gap |
|---|---|---|---|---|
| `watcher.yml` | every 20 min | 98 min | 267 min | 353 min |
| `boards.yml` | every 30 min | 134 min | 282 min | 361 min |

**Post-cron-change, still on GitHub schedule (10 runs each, 2026-06-01 to 2026-06-02):**

| Workflow | Target | Median gap | p90 gap | Max gap | vs. target |
|---|---|---|---|---|---|
| `watcher.yml` | 10 min | 268 min | 426 min | 426 min | 27× over — worse than baseline |
| `boards.yml` | 30 min | 273 min | 444 min | 444 min | 9× over — worse than baseline |

**Root cause:** GitHub cron deprioritization is fundamental and not fixable with schedule tuning. More aggressive crons may have triggered heavier throttling.

**Fix (2026-06-02, verified):** Primary scheduling moved to cron-job.org → `workflow_dispatch` API. Confirmed via `gh run list`: watcher `workflow_dispatch` runs at 20:40 and 20:50 UTC Jun 2, exactly 10 min apart, all success. GitHub `schedule:` downgraded to sparse fallback (`13 */3 * * *`). **If runs go silent, check the cron-job.org jobs and the PAT (expires 2026-08-31) first.**

---

## 7. Findings & risks (recall-first lens)

Ranked by impact. A missed job is expensive; a junk alert is cheap.

---

### 🔴 HIGH — Could cause missed jobs

**~~F1. Actions running 5× slower than scheduled~~ — RESOLVED 2026-06-02**
- **Fix:** Scheduling moved to cron-job.org → `workflow_dispatch` API (watcher 10 min, boards 30 min). Verified in production: watcher `workflow_dispatch` runs landed exactly 10 min apart Jun 2. GitHub `schedule:` is now a sparse fallback (`13 */3 * * *`). PAT expires 2026-08-31 — check it first if runs go silent.

**~~F2. Goldman Sachs, IBM, Oracle fetched with no pagination~~ — FIXED 2026-06-01 (`804f627b`)**
- **Fix:** All three now paginate. GS uses `pageNumber`; IBM uses Elasticsearch `from` offset; Oracle uses `limit=50,offset=N` in the finder query string. Oracle also had a critical extraction bug (was returning the search container instead of `requisitionList`) — fixed in the same commit. Oracle jobs will now correctly accumulate in `seen.json`.

**F3. Dead board single-strike with no resurrection**
- **Impact:** A board that returns a transient 404 (e.g., Greenhouse API blip, maintenance window, DNS hiccup) is permanently silenced. Confirmed 16 boards in the current CSV are permanently dead — some may be false-positives from transient errors.
- **Action:** Implement N-strikes (e.g., mark dead after 3 consecutive 404s) or a TTL-based resurrection (re-probe dead boards monthly).

---

### 🟡 MEDIUM — Correctness / reliability risks

**F4. Two separate `seen` files with no cross-pipeline dedup**
- `state/seen.json` (6,357 IDs, main pipeline) and `state/seen_boards.json` (41,881 IDs, boards pipeline) are completely separate. A job discovered by both pipelines for the same company would generate two email alerts.
- Currently not a problem because the 6 main-source companies (Microsoft, NVIDIA, Amazon, GS, IBM, Oracle) are not in the boards CSV. But if a company is added to both, it becomes a source of duplicate alerts.
- **Action:** Document this constraint; enforce that main-source companies are excluded from the boards CSV.

**F5. Workday URL normalization complexity**
- `workday_normalize_external_job_url` handles 5 distinct path shapes. A bug in any branch produces unclickable or wrong job links in emails, which wastes the user's time (false positive from a broken link perspective).
- Workday is also the most restrictive platform (semaphore=4) and has the most complex boot+pagination flow.
- **Action:** Add test cases covering each path shape. The `workday_tiny.csv` test fixture exists but isn't run in CI.

**F6. `is_us_location` heuristic has edge cases**
- The 2-char ISO code blocklist only fires when there are 3+ comma parts. A 2-part location like "Calgary, AB" would match state abbreviation "AB"... wait, "ab" is not in `US_STATE_ABBRS` — but "Toronto, ON" would match "on" which also isn't in the set. However, a city/state combo like "London, OR" (a real US city in Oregon) would correctly match.
- The regex `\bus\b` can false-positive on strings like "Focus on technology" (contains "us" as a substring but won't match due to `\b`). Low risk.
- **Action:** Monitor for international jobs appearing in alerts; the current false-positive rate from the Apr 1 fix appears low.

---

### 🟠 MEDIUM — Throughput / latency

**F7. 921 orphaned entries in `boards_dead.json` (non-critical but misleading)**
- The dead board set has 937 entries, but cross-referencing against the current CSV shows only **16 actually overlap with live CSV boards**. The other 921 are stale entries from boards that were removed from earlier CSV versions.
- **Runtime impact:** Minimal — orphaned entries don't slow down batch processing since those boards aren't in the batch. But the `boards_dead.json` file is misleading and inflated.
- **Action:** Prune `boards_dead.json` to only include IDs that appear in the current CSV. Can be done as a one-time cleanup script.

**F8. Full boards cycle takes ~28 hours at actual cadence**
- At median 134-min gap between boards runs, and 200 boards/batch over 1,200 boards → 6 batches/cycle → 6 × 134 min = ~13 hours theoretical. At actual throughput (accounting for gap variance), closer to 18–28 hours per full sweep.
- A job posted just after a board's batch slot passes will not be picked up for nearly a full day.
- **Action:** Combine with F1 fix (reduce quota use or upgrade plan). Also: pre-filter dead boards from the batch list so the 200 slots serve only live boards.

---

### 🟢 LOW — Tech debt / dead code

**F9. Large untapped board pool in root-level CSVs**
- `greenhouse_us_verified.csv` (4,659 rows), `lever_us_verified.csv` (1,806 rows), `workday_us_verified.csv` (4,770 rows), `smartrecruiters_us_verified.csv` (210 rows) — none ingested into the live CSV.
- If even half are valid, ingesting them would 5–10× coverage.
- **Action:** Run `verify_*.py` on these files to filter live boards, then merge into `JOB_BOARDS_PURE_WORKING_SUPPORTED_round2.csv`.

**F10. `migrate_workday_seen_keys.py` is a one-time script with no longer a purpose**
- One-time migration that back-filled `req:` keys into `seen_boards.json`. Should be archived or deleted.

**F11. `check_ashby_boards.py` hardcodes ~200 speculative slugs**
- Many entries are SaaS tools unlikely to be on Ashby (e.g., "contact-form-7", "wordpress"). The discovery script works but has high noise. The output (`ashby_new_boards.csv`, 170 rows) has not been ingested into the live CSV.

**F12. Main pipeline sources fetched sequentially**
- The 6 main-source fetches in `main()` run one after another with no concurrency. Total fetch time ≈ sum of all 6 sources. With a 15-min timeout on the Actions job, this is fine today but doesn't scale if more sources are added.
- **Action:** Wrap fetches in a `ThreadPoolExecutor` (same pattern as `run_boards_sweep`) if source count grows.

**F13. `seen_boards.json` naming confusion**
- Two files with nearly identical names serve completely different purposes:
  - `state/seen_boards.json` — 41,881 **job** IDs (boards pipeline dedup)
  - `state/boards_seen.json` — 1,211 **board** IDs (bootstrap tracking)
- Easy to confuse when reading code or debugging.
- **Action:** Rename one of them for clarity (e.g., `state/boards_jobs_seen.json` and `state/boards_bootstrapped.json`). Requires updating env var defaults and the workflow.
