# P12 — Domain workspace and navigation expansion

[Product direction](../PRODUCT_PLAN.md#expansion-sequence) · [ROADMAP](../../ROADMAP.md#p12) · [Collaboration workflow](../COLLABORATION.md)

**Goal:** Scale the `/app` web workspace from the initial social-monitoring vertical into a domain-grouped navigation shell and bounded management surfaces for the expansion sequence (`Research/Connectors` inside `/app/settings?tab=connections`, `Knowledge/Documents`, `Communication/Follow-up`, and `Cases/Approvals`), while preserving chat-first execution, `WebPerms` gating, and `ready=False` rollout flags.

---

## 1. Branching, PR & Multi-Agent Coordination Protocol

1. **Single Integration Trunk (`dev`):**
   - `dev` is the working integration branch and must remain green at all times (`pytest -c .ci/pytest.ini`, Black/isort `--line-length 120`, template & RBAC contract tests).
   - Unfinished user-facing sections remain hidden behind `NavItem(..., ready=False)` (`· скоро`) in `app/web/nav.py` until their task card acceptance criteria are satisfied.
2. **Isolated Task Worktrees & Ready-for-Review PRs:**
   - Every task is claimed on `dev` (`ROADMAP.md` + `docs/tasks/p12-tXX.md` status -> `IN_PROGRESS`) and developed in an isolated worktree `/tmp/social-media-ai-p12-tXX` on branch `feat/p12-tXX-<slug>`.
   - Each task is merged into `dev` via a **Ready-for-Review Pull Request** after passing offline unit and template contract suites.
   - No agent self-accepts or marks `DONE` without owner review/merge approval (`docs/COLLABORATION.md`).
3. **Token-Efficient Agent Context (No Need to Read the UI Prototype):**
   - The interactive React + Jinja2 prototype is for the **Owner & Coordinator** to visually validate layout and UX flows.
   - **Worker agents DO NOT read frontend prototype code.** Every worker agent reads **only 3 markdown files (~3k tokens total)**:
     1. `ROADMAP.md` (queue state & dependencies),
     2. `docs/phases/p12-domain-workspace-expansion.md` (this wave/dependency matrix and template contract),
     3. The single assigned task card `docs/tasks/p12-tXX.md` (exact DB columns, service signatures, Jinja2 context variables, and `Owned paths`).

---

## 2. Multi-Agent Wave & Dependency Graph (Sequential vs. Parallel Tracks)

```text
[Wave 0: DONE in dev]
  ├── Q-16: Shared Platform JSON catalog (app/models/collection/platform.py, platform_config.py)
  └── P12-T01: Domain-grouped sidebar (app/web/nav.py) + Draft Jinja2 templates in app/web/templates/web/
         │
         ▼
[Wave 1: Sequential Foundation — 1 Agent]
  └── P12-T02: Workspace Connectors & Cloud Drive Mounting in /app/settings?tab=connections
        (Extends PlatformType + _BOOTSTRAP, adds UserCredential.tenant_id, SourceType.DRIVE_FOLDER)
         │
         ├───────────────────────────────┬───────────────────────────────┐
         ▼                               ▼                               ▼
[Wave 2: Parallel Domain Tracks — Up to 3 Agents Simultaneously (Zero File Overlap)]
  ├── Agent A: P12-T03            ├── Agent B: P12-T04            └── Agent C: P12-T05
  │   Knowledge & Documents       │   Communications & Follow-ups     Cases & Decimal Comparison
  │   (/app/knowledge,            │   (/app/communications,           (/app/cases)
  │    /app/documents)            │    /app/followups)
         │                               │                               │
         └───────────────────────────────┴───────────────────────────────┘
                                         │
                                         ▼
[Wave 3: Cross-Domain Finalizer — 1 Agent]
  └── P12-T06: Shared Right-Hand Evidence Drawer (#context-panel in base.html) & /app/chat Context Handoff
```

| Wave | Task ID | Execution Mode | Depends On | Parallelizable With | Strictly Isolated `Owned paths` (No Merge Conflicts) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Wave 0** | [P12-T01](../audit/p12-t01.md) | **DONE** (2026-11-01) | — | — | `app/web/nav.py`, `app/web/templates/web/_shell_nav.html` |
| **Wave 1** | [P12-T02](../tasks/p12-t02.md) | **Sequential** (Foundation) | `P12-T01`, `Q-16` | None (feeds Wave 2) | `app/types/enums/platform_types.py`, `app/services/social/platform_config.py`, `app/models/identity/user_credential.py`, `app/web/settings.py`, `app/web/templates/web/settings.html` |
| **Wave 2** | [P12-T03](../tasks/p12-t03.md) | **Parallel Track A** | `P12-T01`, `P12-T02` | `P12-T04`, `P12-T05` | `app/web/knowledge.py`, `app/web/documents.py`, `app/services/knowledge/`, `app/web/templates/web/knowledge.html`, `app/web/templates/web/documents.html` |
| **Wave 2** | [P12-T04](../tasks/p12-t04.md) | **Parallel Track B** | `P12-T01`, `P12-T02` | `P12-T03`, `P12-T05` | `app/web/communications.py`, `app/web/followups.py`, `app/services/communications/`, `app/web/templates/web/communications.html`, `app/web/templates/web/followups.html` |
| **Wave 2** | [P12-T05](../tasks/p12-t05.md) | **Parallel Track C** | `P12-T01` | `P12-T03`, `P12-T04` | `app/web/cases.py`, `app/services/cases/`, `app/web/templates/web/cases.html` |
| **Wave 3** | [P12-T06](../tasks/p12-t06.md) | **Sequential** (Finalizer) | `P12-T03`, `P12-T04`, `P12-T05` | None | `app/web/templates/web/base.html`, `app/web/chat.py`, `app/static/js/context_panel.js` |

---

## 3. Draft Jinja2 Templates & Progressive Wiring Contract

Draft Jinja2 templates for all expansion views are already checked into `app/web/templates/web/` and verified by `tests/unit_web/test_template_contracts.py`:

- `app/web/templates/web/settings.html` (`tab=connections` extended in `P12-T02`)
- `app/web/templates/web/knowledge.html` (`P12-T03` — expects `knowledge_snippets: list[dict]`)
- `app/web/templates/web/documents.html` (`P12-T03` — expects `documents: list[dict]`)
- `app/web/templates/web/communications.html` (`P12-T04` — expects `outbox_items: list[dict]`)
- `app/web/templates/web/followups.html` (`P12-T04` — expects `followup_items: list[dict]`)
- `app/web/templates/web/cases.html` (`P12-T05` — expects `case_record: dict`, `comparison_rows: list[dict]`)

**How agents wire templates as backend logic matures:**
1. Every template already uses `|default(...)` guards so it renders safely under `StrictUndefined` both with empty state and with real service dicts.
2. When an agent implements `P12-T03`, `P12-T04`, or `P12-T05`, they **do not invent a new HTML layout**—they create the FastAPI route in `app/web/<section>.py`, pass the exact context dictionary documented in `docs/tasks/p12-tXX.md` into `templates.TemplateResponse(request, "web/<section>.html", ctx)`, and flip `ready=True` for that `NavItem` in `app/web/nav.py` as the final step of their PR.
