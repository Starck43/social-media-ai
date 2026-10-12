# Task Registry

## Open
- [ ] PR #105 (PRD-02 atomic confirmation consumption, head bceefda): BLOCKED — one regression in `tests/test_agent_runtime_log_privacy.py::test_confirmed_tool_failure_keeps_flow_without_logging_private_error` (existing test mocks `agent_sessions` without `consume_pending_confirmation`); 58 prepared tests pass locally; new file `tests/test_runtime_confirmation_authorization.py` not Black-formatted (owner: opencode)
- [ ] PR #30 fresh head 8ded166: owner decision needed — waive cosmetic `git diff --check` (blank line at EOF, 4 tenancy files, pre-existing since a4d3237) or approve scoped production-file whitespace fix; then resume #30 pytest and #31/#32 queue (owner: opencode)
- [ ] Pre-existing full-suite failures on base b4fd0f0 (47 tests: handle_collect mock_infrastructure ImportError, JobManager SimpleNamespace AttributeError, web layout/wizard assertions) — fixed by later dev commits, 482eacb full suite is clean (owner: opencode)

## Done
- [x] PRD-05 unknown-handler terminal outcome: dispatcher records terminal `failed` for unregistered job types even when the caller allows retries; claim loss and outcome-write uncertainty stay explicit errors; docs + 4 regression tests; 30 focused tests + 22 subtests passed (owner: opencode, branch: dev)
- [x] PR review #30/#31/#32 old heads: #30 full+control (0 new failures vs base), #31 8 passed, #32 33 passed — all exit 0 on targeted checks (owner: opencode)
- [x] PR #30/#31/#32 fresh heads: worktrees ff-only updated to 8ded166/2bfd3ee/68809d0, #30 diagnostics run, queue stopped on failed diff --check (owner: opencode)
- [x] UX-02 (historical; see docs/IMPLEMENTATION_STATUS.md): action_send registry binding + botaction.update gate + permission scope repair + ok/no_data/partial/skipped run outcomes (owner: opencode, branch: dev)
