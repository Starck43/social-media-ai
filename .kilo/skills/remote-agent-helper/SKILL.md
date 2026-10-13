---
name: remote-agent-helper
description: Work as the local execution helper for the remote origin agent (Starck43/Kilo) — delegated reviews, prepared checks, scoped corrections, read-only queue reconciliation and docs-ledger updates under this project's worktree/evidence discipline.
---

# Remote-agent helper (local executor for origin-agent packages)

The remote agent (Starck43 on origin) prepares bounded packages with an "Owner handoff" section and delegates local execution here. This skill is the standing procedure for those delegations.

## Communication

- Reply to the agent terse: verdict, tested SHA, result/exit, blockers. If details live in the commit body or updated docs, point there — never duplicate what the agent will read himself.
- Owner-relayed instructions can arrive as plain text; follow the PR handoff body for technical specifics.

## Worktree discipline (hard rules)

- One package = one dedicated worktree, path named in the handoff (e.g. `/Users/admin/Projects/social-media-ai-<name>`). Inspect `git worktree list` first; reuse existing.
- Never switch/stash/clean/commit the main dev checkout (`/Users/admin/Projects/social-media-ai`) or another PR's worktree.
- Fetch through an existing worktree (default: `-C /Users/admin/Projects/social-media-ai-pr34 fetch origin`), then create only if neither the path nor the local branch exists:
  `git -C /Users/admin/Projects/social-media-ai-pr34 worktree add --track -b <branch> <path> origin/<branch>`
- If the worktree exists: verify branch, clean status, unpublished commits; `git pull --ff-only` only when safe; stop on divergence; never recreate/reset.
- repo root is a git repo; real worktrees live at `/Users/admin/Projects/social-media-ai-*`.

## Intake verification (always, before anything else)

1. `git status --short --branch` in the target worktree.
2. `git rev-parse HEAD` == submitted head SHA from the owner message/handoff.
3. `git diff --name-only origin/dev...HEAD` == the handoff's declared paths (count and names).
4. `git diff --check origin/dev...HEAD`.
5. Fresh-fetch first (`...-pr34 fetch origin`) so "fresh heads" really are fresh; compare local vs remote head per branch.

## Running checks

- Approved environment: `/Users/admin/Projects/social-media-ai/.venv/bin/python` (Python 3.12.6); black/isort from the same venv. No blind installs, no env copying.
- Standalone suites: `python tests/test_<name>.py` (unittest style).
- DB discipline: before any DB runner, `ps aux | grep -E "pytest|test_<...>" | grep -v grep` must show zero concurrent runners (shared sequential `test_schema`). One DB command at a time.
- DB standalone runners read `POSTGRES_URL` before app imports and do not load `.env`: source the main repo `.env` into the process env only — `set -a && . /Users/admin/Projects/social-media-ai/.env && set +a` — never copy the file, never echo values. App `Settings` also needs the rest of `.env` (SECRET_KEY etc.).
- No full suite, pytest collection with conftest bootstrap, schema create/reset/drop/stamp/migration, or live/browser/provider actions unless explicitly requested.

## Logs and captured exits

- Save every run to the perr dir: `/var/folders/kt/35hkcgs9253gdfq1n79259zh0000gn/T/kilo/perr/`.
- Capture exit codes inside the same file. Do not evaluate `$(date ...)` twice in one command (redirect and echo land in different files on a minute boundary) — assign the filename to a variable first.

## Formatting review (project config line-length 120)

- `black --check` / `isort --check-only` on changed files from inside the repo (config auto-discovered).
- Baseline comparison is mandatory before blaming the PR: `git show origin/dev:<file> | black --line-length 120 --check -` — temp copies outside the repo fall back to black's 88 default, so always pass `--line-length 120` explicitly.
- Fix only drift the PR introduced (new file, new lines). Keep inherited layout in pre-existing files; never repo-wide reformat. Compare PR-added lines against black-formatted output to identify genuine new drift.

## Code review pattern

- Map the handoff claims to the diff: changed-function list, signature/decorator/guard AST identity (`ast.dump` comparison against `git show origin/dev:<file>`), prepared method counts vs observed counts, caller-compatibility for touched call chains, privacy/redaction assertions.
- Verify isolation claims of new tests (imports list via ast).

## Corrections protocol

- Only when needed; stage only reviewed paths (`git add -- <paths>`).
- Commit message pattern: `fix(<area>): address <package> review findings` + body: "Owner handoff: <what/why>, exact tested SHA, commands/results/exits, limitations, next step. No schema/live/Ready/merge."
- Push `git push origin HEAD:<branch>`; never force push; never empty commit when no corrections.
- Keep the PR Draft; Ready (even when granted) is not merge permission; never merge, never push to dev.

## Reporting back

Return to the owner/agent: verdict (approved / corrections pushed), exact tested SHA, command/result/exit per suite, log filenames, current pushed head, redacted failures, and not-run scope. Publish the review as a PR comment for the remote agent (that is how findings reach him), terse per the communication rule.

## Evidence integrity (hard rules)

- Never invent or re-attribute test counts, durations or SHAs. A claimed run must have a command, a log file and a captured exit at a stated SHA.
- If a record turns out unsupported, correct it at the source (PR body + visible comment) and say so plainly.
- Exact tested SHA stays attached to its own head; docs-only deltas make byte-identical code carryover legitimate — state the delta explicitly.

## Read-only reconciliation (when asked to compare PRs)

- No checkouts, merges, branches or new tests. Read state from existing worktrees.
- Per PR: local vs remote head, paths vs `origin/dev`, `merge-base` against dev and pairwise (shared commits?), caller compatibility across the set, tested-SHA mapping (tested SHA == current pushed head?).
- Answer format: «можно интегрировать / блокер», evidence sources, recommended order, caveats.

## Docs-ledger reconciliation (docs-only packages)

- Update existing rows only; never add new handoff docs.
- Record factual merge SHAs, closed original PRs, per-merge tested SHAs and log sources.
- Never carry a historical full suite onto a new baseline; keep OPEN gates and parallel-chat assignments (PR29 deferred; PR30/31/32 model-layout lanes) verbatim.
- Produce the compact diff first, uncommitted, for review; commit/push only on approval.
