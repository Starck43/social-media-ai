# Task Registry

## Open
- [ ] PR #30 fresh head 8ded166: owner decision needed — waive cosmetic `git diff --check` (blank line at EOF, 4 tenancy files, pre-existing since a4d3237) or approve scoped production-file whitespace fix; then resume #30 pytest and #31/#32 queue (owner: opencode)
- [ ] Pre-existing full-suite failures on base b4fd0f0 (47 tests: handle_collect mock_infrastructure ImportError, JobManager SimpleNamespace AttributeError, web layout/wizard assertions) — fixed by later dev commits, 482eacb full suite is clean (owner: opencode)

## Done
- [x] PR review #30/#31/#32 old heads: #30 full+control (0 new failures vs base), #31 8 passed, #32 33 passed — all exit 0 on targeted checks (owner: opencode)
- [x] PR #30/#31/#32 fresh heads: worktrees ff-only updated to 8ded166/2bfd3ee/68809d0, #30 diagnostics run, queue stopped on failed diff --check (owner: opencode)
- [x] UX-02 (docs/LOCAL_EXPERIENCE_PLAN.md): action_send registry binding + botaction.update gate + permission scope repair + ok/no_data/partial/skipped run outcomes (owner: opencode, branch: dev)
