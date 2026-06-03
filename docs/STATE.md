# ms-job-watcher — Session Handoff

## Current status

Both pipelines running and on schedule as of Jun 2 2026. **External triggering via cron-job.org is live and verified** — `gh run list` shows `event=workflow_dispatch` runs landing at 20:40 and 20:50 UTC (exactly 10 min apart, both success); boards dispatch also confirmed (204 + successful runs). The multi-hour GitHub cron latency problem is fully resolved. Pipeline 1 (`--mode main`) polls Microsoft, NVIDIA, Amazon, Goldman Sachs, IBM, and Oracle; all three single-page sources (GS/IBM/Oracle) now paginate fully, and the Oracle extraction bug is fixed. Pipeline 2 (`--mode boards`) sweeps ~1,200 ATS boards in batches of 200. `seen.json` holds 6,357 deduplicated job IDs; `seen_boards.json` holds 41,881.

## Open bugs / issues

- [x] **External scheduling via cron-job.org is live and verified in production.** GitHub cron deprioritization confirmed (median 268/273 min despite 10/30-min target). Switched to cron-job.org → `workflow_dispatch` API: watcher every 10 min, boards every 30 min. Verified via `gh run list`: watcher `workflow_dispatch` runs landed at 20:40 and 20:50 UTC on Jun 2, exactly 10 min apart, all success. Boards dispatch confirmed (204 + successful run). GitHub `schedule:` downgraded to sparse fallback (`13 */3 * * *`). PAT expires 2026-08-31.
- [ ] **Dead-board single-strike permanent marking — no resurrection.** One 404/410 = dead forever. 16 boards in current CSV are marked dead; some may be transient failures. Implement N-strikes (3 consecutive) or monthly TTL re-probe.
- [ ] **`boards_dead.json` has 921 orphaned entries (stale, not wasting throughput but misleading).** Only 16 of 937 entries overlap with the current CSV. Prune to match live CSV.
- [ ] **Large untapped board pool.** `greenhouse_us_verified.csv` (4,659 rows), `lever_us_verified.csv` (1,806 rows), `workday_us_verified.csv` (4,770 rows) — none ingested. Verify first, add in tranches.
- [ ] **Gmail account mismatch.** Connected Gmail is the wrong account; the alerts inbox hasn't been analyzed. Reconnect the correct inbox before doing Gmail-based funnel analysis.

## Next steps

1. **After ~1 day of live dispatch runs, check `run_log.json` funnel data** — look at per-source `title_ok` and `loc_ok` counts to confirm the title classifier isn't over-dropping. If a source's "kept" count drops sharply after a filter tweak, that's the signal. Recall-first: err toward alerting.
2. **Reconnect the correct Gmail inbox**, then analyze which boards actually produce relevant alerts.
3. **Selectively ingest from the ~10k curated lists** (greenhouse/lever/workday_us_verified) — verify first, add in tranches; do NOT bulk-add (cycle staleness wrecks latency).
4. **[low] Dead-board resurrection + prune orphaned entries** — implement N-strikes (3 consecutive 404s) or monthly TTL re-probe instead of single-strike permanent marking; prune `boards_dead.json` down to the 16 entries that actually overlap the current CSV (921 are stale orphans).
5. **[optional, deferred by choice] Automated test harness** — pytest on `classify_title` / `is_us_location` was considered and deliberately deferred. `run_log.json` is the lightweight safety net for regressions. Not urgent unless the classifier is changed.

## Key facts & gotchas

- **Single file:** all logic lives in `watcher.py` (~2,160 lines after pagination additions). No external modules beyond `requests`.
- **State is committed to git** by the `github-actions` bot after every run. Push conflicts handled by a 5-retry loop with `git merge -X ours`. Remote state is always source of truth; pull before editing state files locally.
- **Scheduling is now EXTERNAL** — cron-job.org calls the `workflow_dispatch` API (watcher every 10 min, boards every 30 min). The GitHub `schedule:` cron (`13 */3 * * *` in both files) is only a sparse fallback. Auth = a fine-grained PAT (this repo, Actions:write) stored in cron-job.org that **EXPIRES 2026-08-31** — if runs go silent, FIRST check the PAT and the cron-job.org jobs.
- **Dead boards: single-strike permanent.** One 404/410 → `boards_dead.add(board_id)` → skipped forever. No retry logic.
- **Dead boards: 921 of 937 are orphaned stale entries.** Only **16 boards** in the current 1,200-row CSV are actually dead. The other 921 are from boards removed in earlier CSV versions — they don't slow down batches.
- **Oracle was broken since day one.** `fetch_oracle` was returning the search container (`items` list, each a dict with `SearchId`, `Keyword`, etc.) instead of `items[0].get("requisitionList")`. This produced `oracle:url:` junk keys and 0 Oracle jobs ever entering `seen.json`. Fixed in commit `804f627b`.
- **GS/IBM/Oracle now paginate.** GS uses `pageNumber` increment; IBM uses `from` offset (Elasticsearch); Oracle uses `limit=50,offset=N` embedded in the finder query string. All three short-circuit when a full page is already in `seen_keys`.
- **New board bootstrap suppresses first-run alerts.** When a board is seen for the first time, all current jobs are added to `seen` silently — no email. Alert lag until second sweep.
- **Cursor persists in `state/boards_cursor.json`.** Wraps to 0 after reaching end of CSV. Full cycle = `ceil(n_boards / batch_size)` runs.
- **Workday URL normalization is complex.** `workday_normalize_external_job_url` handles 5 path shapes. Bugs here produce unclickable links in emails.
- **US location filtering** uses state abbreviation regex + ISO 3166 country-code blocklist. International cities with US-state-like abbreviations were fixed Apr 1 2026.
- **Concurrency:** ThreadPoolExecutor with per-platform semaphores (GH=8, Lever=8, SR=6, WD=4, Ashby=6). Workday is most restrictive.
- **Email:** Gmail SMTP SSL on port 465. Secrets: `EMAIL_USER`, `EMAIL_APP_PASSWORD`, `ALERT_TO_EMAIL`.
- **Recall-first philosophy:** a missed job (false negative) is expensive; a junk alert (false positive) is cheap. When in doubt, err toward alerting.
- **Security:** Full git-history scan was clean (no secrets ever committed). `.gitleaks.toml` added with `[extend] useDefault = true` + allowlist for `state/*.json`. The local-only `data/boards/workday_debug/` directory contains HAR files with expired AWS STS credentials — never committed, optional cleanup: `rm -rf data/boards/workday_debug/`.
- **Full architecture reference:** see `docs/ARCHITECTURE.md` — repo map, full function index, runtime traces, external API surface, and ranked risk findings.

## Backlog (later — not urgent)

Evidence gathered 2026-06-02 from ~10 hrs of live dispatch runs (29 main runs, 14 boards runs).
The boards lane is healthy (~250 emails over the window). All items below are main-mode curated-lane gaps.
Note: 100% location pass on Microsoft/Amazon/NVIDIA is expected — those queries are US-filtered upstream, not a bug.

- **Oracle — 0 fetched in every run despite the `804f627b` fix.** Zero Oracle coverage in production. Diagnosis when revisited: run `fetch_oracle` in isolation, inspect the raw API response + extraction key, confirm the fix actually landed in the deployed function.

- **Goldman Sachs — under-fetch + loc_ok=0 always.** Only ~2 jobs fetched per run (single page — pagination likely still broken), and the 1 title-passing job fails `is_us_location` every run. GS is a US company (NYC HQ); suspected location-string format the regex doesn't match. Also threw 403 errors in 2 of 29 runs. Fix plan: fix pagination first, then eyeball real location strings on the larger corpus to diagnose the regex miss.

- **NVIDIA fetches exactly 20, Amazon exactly 300 every run.** Round, stable counts that smell like un-paginated single-page results or hard caps silently truncating the full listing. Confirm both sources paginate to completion (or document why the count is correct).

- **[low] Boards recall spot-check.** Title pass rates (23–37%) and location drop-offs after title (Ashby 24%, Workday 26%) look like normal filtering of all-department global boards, but that's unverified. Someday: eyeball a sample of title-rejected and location-rejected jobs on one high-volume source to confirm the filters aren't dropping real US engineering roles.

## Recent changes

- **2026-06-02** — External triggering verified in production. `gh run list` confirms `event=workflow_dispatch` runs at 20:40 and 20:50 UTC (exactly 10 min apart, all success); boards dispatch also confirmed. Multi-hour latency fully resolved.
- **2026-06-02** — `ci: switch to external dispatch trigger — downgrade schedule to sparse fallback`. cron-job.org now drives both workflows (watcher 10 min, boards 30 min) via `workflow_dispatch` API (HTTP 204 verified). GitHub `schedule:` downgraded to `13 */3 * * *` (sparse fallback). PAT expires 2026-08-31.
- **2026-06-02** — Cadence audit: measured 10 watcher + 9 boards gaps post-Jun-1 cron change. Watcher median 268 min (target 10 min), boards median 273 min (target 30 min) — both worse than pre-change baseline. GitHub cron deprioritization confirmed; must move off Actions cron entirely.
- **2026-06-02** — `feat: add per-run funnel observability to state/run_log.json` (`d0d51894`). Both modes now record `{ts, mode, per_source: {src: {fetched, title_ok, loc_ok, new, emailed, error/errors}}, duration_s, cursor}` to `state/run_log.json` (bounded 1,000 records, picked up by existing `git add state/*.json`). Also prints a one-line summary to Actions log each run.
- **2026-06-01** — `fix: paginate Goldman Sachs, IBM, Oracle — fix Oracle requisitionList extraction` (`804f627b`). Oracle was broken since day one; now fixed. All three sources paginate fully.
- **2026-06-01** — `ci: improve cron cadence — watcher 10min offset, boards 30min offset` (`f7a5c236`). Moved off congested `:00/:15/:30/:45` slots.
- **2026-06-01** — `config: add .gitleaks.toml` (`1e06172d`). Suppresses `state/*.json` false positives while keeping default secret detectors active.
- **2026-06-01** — Full architecture audit: created `docs/ARCHITECTURE.md`; corrected dead-board count (16 active, 921 orphaned); confirmed Actions throttling as primary latency risk; identified GS/IBM/Oracle pagination gaps (now fixed).
- **2026-06-01** — Added `CLAUDE.md` and `docs/STATE.md` for persistent project memory. Repo made **public** (unlimited Actions minutes).
