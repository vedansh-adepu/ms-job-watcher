# ms-job-watcher — Session Handoff

## Current status

Both pipelines running as of last automated push (Jun 1 2026). Repo is **public** — Actions minutes are unlimited. Pipeline 1 (`--mode main`) polls Microsoft, NVIDIA, Amazon, Goldman Sachs, IBM, and Oracle on a 10-min offset cron; all three single-page sources (GS/IBM/Oracle) now paginate fully, and the Oracle extraction bug (was returning the search container instead of `requisitionList`) is fixed — Oracle jobs will accumulate in `seen.json` for the first time. Pipeline 2 (`--mode boards`) sweeps ~1,200 ATS boards in batches of 200 on a 30-min offset cron. `seen.json` holds 6,357 deduplicated job IDs; `seen_boards.json` holds 41,881.

## Open bugs / issues

- [ ] **Confirm cadence improvement.** After ~1 day, run `gh run list` for both workflows and measure median gap. Expect gaps close to 10/30 min now that (a) repo is public → unlimited minutes and (b) crons are offset off `:00/:15/:30/:45`. If still slow, move off GitHub Actions cron.
- [ ] **Dead-board single-strike permanent marking — no resurrection.** One 404/410 = dead forever. 16 boards in current CSV are marked dead; some may be transient failures. Implement N-strikes (3 consecutive) or monthly TTL re-probe.
- [ ] **`boards_dead.json` has 921 orphaned entries (stale, not wasting throughput but misleading).** Only 16 of 937 entries overlap with the current CSV. Prune to match live CSV.
- [ ] **Large untapped board pool.** `greenhouse_us_verified.csv` (4,659 rows), `lever_us_verified.csv` (1,806 rows), `workday_us_verified.csv` (4,770 rows) — none ingested. Verify first, add in tranches.
- [ ] **Gmail account mismatch.** Connected Gmail is the wrong account; the alerts inbox hasn't been analyzed. Reconnect the correct inbox before doing Gmail-based funnel analysis.

## Next steps

1. **After ~1 day, measure actual run gaps** (`gh run list`) to confirm public+offset crons fixed latency. If still slow, move scheduling off GitHub Actions cron.
3. **Use `run_log.json` funnel data** to check whether the title classifier is too strict (recall-first: err toward alerting).
4. **Reconnect the correct Gmail inbox**, then analyze which boards actually produce relevant alerts.
5. **Selectively ingest from the ~10k curated lists** (greenhouse/lever/workday_us_verified) — verify first, add in tranches; do NOT bulk-add (cycle staleness wrecks latency).

## Key facts & gotchas

- **Single file:** all logic lives in `watcher.py` (~2,160 lines after pagination additions). No external modules beyond `requests`.
- **State is committed to git** by the `github-actions` bot after every run. Push conflicts handled by a 5-retry loop with `git merge -X ours`. Remote state is always source of truth; pull before editing state files locally.
- **Cron schedules:** `watcher.yml` → `7,17,27,37,47,57 * * * *` (every 10 min, offset); `boards.yml` → `23,53 * * * *` (every 30 min, offset). Offset avoids the congested `:00/:15/:30/:45` slots.
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

## Recent changes

- **2026-06-02** — `feat: add per-run funnel observability to state/run_log.json` (`d0d51894`). Both modes now record `{ts, mode, per_source: {src: {fetched, title_ok, loc_ok, new, emailed, error/errors}}, duration_s, cursor}` to `state/run_log.json` (bounded 1,000 records, picked up by existing `git add state/*.json`). Also prints a one-line summary to Actions log each run.
- **2026-06-01** — `fix: paginate Goldman Sachs, IBM, Oracle — fix Oracle requisitionList extraction` (`804f627b`). Oracle was broken since day one; now fixed. All three sources paginate fully.
- **2026-06-01** — `ci: improve cron cadence — watcher 10min offset, boards 30min offset` (`f7a5c236`). Moved off congested `:00/:15/:30/:45` slots.
- **2026-06-01** — `config: add .gitleaks.toml` (`1e06172d`). Suppresses `state/*.json` false positives while keeping default secret detectors active.
- **2026-06-01** — Full architecture audit: created `docs/ARCHITECTURE.md`; corrected dead-board count (16 active, 921 orphaned); confirmed Actions throttling as primary latency risk; identified GS/IBM/Oracle pagination gaps (now fixed).
- **2026-06-01** — Added `CLAUDE.md` and `docs/STATE.md` for persistent project memory. Repo made **public** (unlimited Actions minutes).
