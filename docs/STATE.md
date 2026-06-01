# ms-job-watcher — Session Handoff

## Current status

Both pipelines are running and healthy as of the last automated push (Jun 1 2026, 20:46 UTC). Pipeline 1 (`--mode main`) polls Microsoft, NVIDIA, Amazon, Goldman Sachs, IBM, and Oracle every 20 min via direct API adapters. Pipeline 2 (`--mode boards`) sweeps a CSV of ~1,200 ATS boards in batches of 200 every 30 min using a cursor. `seen.json` holds 6,357 deduplicated job IDs. The boards pipeline has bootstrapped 1,211 board entries and marked 937 dead. No active bugs are known; the open items below are verification tasks and potential improvements.

## Open bugs / issues

- [ ] **Actions quota throttling — primary latency risk.** Median run gap is 98 min (watcher) and 134 min (boards) vs. 20/30 min scheduled. Max observed gap: ~6 hours. Pattern is consistent with minutes-quota exhaustion, not cron jitter. Check billing page (GitHub → Settings → Billing → Actions). On Free plan (2,000 min/month), the 20-min cron alone requires ~4,320 min/month. Fix: upgrade to Pro (3,000 min) or reduce cron frequency. Billing API blocked by token scope — must check manually.
- [ ] **Goldman Sachs, IBM, Oracle have no pagination — jobs beyond cap are silently dropped.** GS returns ≤20 items (GraphQL pageSize), IBM ≤30 (size param), Oracle ≤14 (limit baked into URL). If more matching jobs exist they are permanently missed. Add pagination loops.
- [ ] **Dead-board single-strike permanent marking — no resurrection.** One 404/410 = dead forever. 16 boards in the current CSV are marked dead; some may be transient failures. Implement N-strikes (e.g., 3 consecutive) or a monthly TTL re-probe.
- [ ] **`boards_dead.json` has 921 orphaned entries (stale, not wasting throughput but misleading).** Cross-reference confirms only 16 of 937 dead entries overlap with the current CSV. The rest are from boards removed in earlier CSV versions. Prune `boards_dead.json` to match the live CSV.
- [ ] **Large untapped board pool.** `greenhouse_us_verified.csv` (4,659 rows), `lever_us_verified.csv` (1,806 rows), `workday_us_verified.csv` (4,770 rows) — none ingested. Run `verify_*.py` on these and merge validated rows into the live CSV to 5–10× coverage.

## Next steps

1. **[URGENT]** Check GitHub Actions billing page for minutes used this month — confirms or rules out quota throttling as the latency cause.
2. Add pagination to Goldman Sachs (GraphQL pageNumber), IBM (size offset), and Oracle (extract limit from URL, paginate with offset).
3. Implement N-strikes dead-board policy (3 consecutive 404s before permanent mark). Prune 921 orphaned entries from `boards_dead.json`.
4. Ingest `greenhouse_us_verified.csv` and `lever_us_verified.csv` into the live boards CSV after verification.
5. Review title classifier tuning: check if `SENIORITY_MAYBE_TOKENS` demotes too many entry/mid roles to `maybe` (recall-first philosophy — err toward alerting).

## Key facts & gotchas

- **Single file:** all logic lives in `watcher.py` (2,115 lines). No external modules beyond `requests`.
- **State is committed to git** by the `github-actions` bot after every run. Push conflicts are handled by a 5-retry loop with `git merge -X ours`. This means the remote state is always the source of truth; local state files may be stale if you haven't pulled.
- **Dead boards: single-strike permanent.** One 404/410 → `boards_dead.add(board_id)` → skipped forever. No retry logic.
- **Dead boards: 921 of 937 are orphaned stale entries.** Cross-referenced against live CSV (Jun 1 2026): only **16 boards** in the current 1,200-row CSV are actually dead. The other 921 entries in `boards_dead.json` are from boards removed in earlier CSV versions — they don't slow down batches since those boards never appear in the current CSV. Batch slot waste is NOT a current problem.
- **New board bootstrap suppresses first-run alerts.** When a board is seen for the first time (`board_id not in boards_seen`), all its current jobs are added to `seen` silently — no email. This prevents a flood when adding new boards, but means alert lag until the second sweep of that board.
- **Cursor persists in `state/boards_cursor.json`** (currently at 600). It wraps to 0 after reaching the end of the CSV. The full cycle takes `ceil(n_boards / batch_size)` runs.
- **Workday URL normalization is complex.** Many Workday boards return external job paths as `/job/Title_R1234567` (missing locale + site prefix). `workday_normalize_external_job_url` reconstructs the full URL. Bugs here produce unclickable links in emails.
- **US location filtering** uses state abbreviation regex + ISO 3166 country-code blocklist (`NON_US_COUNTRY_CODES`). International cities with US-state-like abbreviations (e.g., "Budapest, OR, hu") were previously causing false positives — fixed Apr 1 2026.
- **Concurrency:** ThreadPoolExecutor with per-platform semaphores (GH=8, Lever=8, SR=6, WD=4, Ashby=6). Workday is most restrictive.
- **Email:** Gmail SMTP SSL on port 465. Secrets: `EMAIL_USER`, `EMAIL_APP_PASSWORD`, `ALERT_TO_EMAIL`.
- **Recall-first philosophy:** a missed job (false negative) is expensive; a junk alert (false positive) is cheap. When in doubt, err toward alerting.
- **Full architecture reference:** see `docs/ARCHITECTURE.md` — repo map, full function index, runtime traces, external API surface, and ranked risk findings.

## Recent changes

- **2026-06-01** — Full architecture audit: created `docs/ARCHITECTURE.md`; corrected dead-board count (16 active, 921 orphaned); confirmed Actions throttling (median gap 98–134 min vs 20–30 min target); identified GS/IBM/Oracle pagination gaps as missed-job risk.
- **2026-06-01** — Added `CLAUDE.md` and `docs/STATE.md` for persistent project memory.
- **2026-04-08** — `feat: add 107 Greenhouse and 30 Lever boards from curated sources`
- **2026-04-01** — `fix: reject non-US country names in is_us_location + backfill Workday req keys`
- **2026-04-01** — `feat: add 170 verified Ashby company boards`
- **2026-04-01** — `fix: resolve Workday duplicate alerts and false-positive US location matches for international cities`
- **2026-04-01** — `feat: add Ashby platform support + clean up 22 dead boards + migrate 12 companies to correct platforms`
