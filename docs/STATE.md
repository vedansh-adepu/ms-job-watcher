# ms-job-watcher — Session Handoff

## Current status

Both pipelines are running and healthy as of the last automated push (Jun 1 2026, 20:46 UTC). Pipeline 1 (`--mode main`) polls Microsoft, NVIDIA, Amazon, Goldman Sachs, IBM, and Oracle every 20 min via direct API adapters. Pipeline 2 (`--mode boards`) sweeps a CSV of ~1,200 ATS boards in batches of 200 every 30 min using a cursor. `seen.json` holds 6,357 deduplicated job IDs. The boards pipeline has bootstrapped 1,211 board entries and marked 937 dead. No active bugs are known; the open items below are verification tasks and potential improvements.

## Open bugs / issues

- [ ] **VERIFY: dead-board batch slot waste.** `boards_dead` has 937 entries against ~1,200 CSV rows (~78% dead). Dead boards are NOT filtered before batch slicing — they consume cursor slots and return immediately (no HTTP call, elapsed=0). Confirm the true live/dead split against the current CSV and quantify the wasted throughput per 200-slot batch. A pre-filter (skip dead boards before slicing) could multiply effective sweep speed.
- [ ] **VERIFY: single-strike dead marking.** A board is permanently marked dead on the **first** 404 or 410 (`_is_dead_http_status`, line 1597; `dead_boards.add(board_id)`, line 1744). No consecutive-failure threshold exists. Boards that have transient outages or temporarily redirect will never be retried. Decide whether to add an N-strikes policy or a TTL-based resurrection window.
- [ ] **VERIFY: `boards_seen` vs `boards_dead` overlap.** `boards_seen` (1,211) > CSV row count (~1,200), meaning some entries may be from boards removed from the CSV. Audit whether stale `boards_seen`/`boards_dead` entries create any confusion or need periodic pruning.

## Next steps

1. Run `python watcher.py --mode boards --no-email --dry-run` locally with debug prints to get a live count of dead-skipped vs actually-fetched boards in a single batch.
2. Decide on dead-board resurrection policy (options: TTL window, N-strikes, manual CSV curation, or periodic `boards_dead.json` flush).
3. If dead-board pre-filtering is approved, add a filter in `run_boards_sweep` before `batch = boards[start:end]` to exclude dead boards from the cursor-addressable list — this would dramatically increase effective boards/batch for live boards.
4. Review title classifier tuning: philosophy is recall-first (false negatives are expensive; false positives are cheap). Check if `SENIORITY_MAYBE_TOKENS` is demoting too many entry/mid roles to `maybe`.
5. Consider adding more sources to `--mode main` (e.g., Meta, Apple, Google — all have stable internal APIs or Greenhouse/Lever boards).

## Key facts & gotchas

- **Single file:** all logic lives in `watcher.py` (2,115 lines). No external modules beyond `requests`.
- **State is committed to git** by the `github-actions` bot after every run. Push conflicts are handled by a 5-retry loop with `git merge -X ours`. This means the remote state is always the source of truth; local state files may be stale if you haven't pulled.
- **Dead boards: single-strike permanent.** One 404/410 → `boards_dead.add(board_id)` → skipped forever. No retry logic.
- **Dead boards waste batch slots.** The batch of 200 is sliced from the full CSV (including dead boards), and dead boards are skipped inside `_process_single_board` after the slot is consumed. With ~78% dead, an effective 200-slot batch only fetches from ~44 live boards.
- **New board bootstrap suppresses first-run alerts.** When a board is seen for the first time (`board_id not in boards_seen`), all its current jobs are added to `seen` silently — no email. This prevents a flood when adding new boards, but means alert lag until the second sweep of that board.
- **Cursor persists in `state/boards_cursor.json`** (currently at 600). It wraps to 0 after reaching the end of the CSV. The full cycle takes `ceil(n_boards / batch_size)` runs.
- **Workday URL normalization is complex.** Many Workday boards return external job paths as `/job/Title_R1234567` (missing locale + site prefix). `workday_normalize_external_job_url` reconstructs the full URL. Bugs here produce unclickable links in emails.
- **US location filtering** uses state abbreviation regex + ISO 3166 country-code blocklist (`NON_US_COUNTRY_CODES`). International cities with US-state-like abbreviations (e.g., "Budapest, OR, hu") were previously causing false positives — fixed Apr 1 2026.
- **Concurrency:** ThreadPoolExecutor with per-platform semaphores (GH=8, Lever=8, SR=6, WD=4, Ashby=6). Workday is most restrictive.
- **Email:** Gmail SMTP SSL on port 465. Secrets: `EMAIL_USER`, `EMAIL_APP_PASSWORD`, `ALERT_TO_EMAIL`.
- **Recall-first philosophy:** a missed job (false negative) is expensive; a junk alert (false positive) is cheap. When in doubt, err toward alerting.

## Recent changes

- **2026-06-01** — Added `CLAUDE.md` and `docs/STATE.md` for persistent project memory.
- **2026-04-08** — `feat: add 107 Greenhouse and 30 Lever boards from curated sources`
- **2026-04-01** — `fix: reject non-US country names in is_us_location + backfill Workday req keys`
- **2026-04-01** — `feat: add 170 verified Ashby company boards`
- **2026-04-01** — `fix: resolve Workday duplicate alerts and false-positive US location matches for international cities`
- **2026-04-01** — `feat: add Ashby platform support + clean up 22 dead boards + migrate 12 companies to correct platforms`
