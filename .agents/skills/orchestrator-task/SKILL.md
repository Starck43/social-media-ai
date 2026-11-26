---
name: orchestrator-task
description: "Use when one session must both execute and audit a task; enforces phase separation and an independent verdict so the implementer never accepts its own work."
---

# Orchestrator: executor and auditor in one session

Declare this skill at session start, then load `task` and `audit`. `task` owns
implementation and evidence; `audit` owns the review contract and closeout. This
skill adds only the separation rules that make one session safe for both.

## Phase separation

Run the phases in order and never mix their outputs:

1. **EXECUTE** — claim, implement, run checks through the test route. Do not
   write acceptance text or a verdict. End with a frozen candidate SHA and the
   Evidence record in the card/PR.
2. **HANDOFF** — write the `audit` handoff from the card: acceptance criteria,
   traceability, changed paths, exact checks, links. This is the only context the
   reviewer gets.
3. **REVIEW** — dispatch the independent route (`perch` → `free-pi` → `auditor`)
   with the handoff. You may pre-flight the diff and rerun one exact command, but
   you may not issue the verdict. Start the reviewer from the frozen SHA, not
   from your session history.
4. **CLOSE** — only an independent verdict from that route authorizes merge and
   registry closeout. Apply it exactly; if the route is unavailable, leave the
   task READY or BLOCKED and report the gap. Never turn your own review into
   `ACCEPTED`.

## Context hygiene

- Keep the parent context for decisions and the handoff. Send exploration,
  log parsing, inventories and one-command runs to cheap children.
- Between phases, rely on the card/PR, not on replayed chat. If the context is
  compacted or the session is new, rebuild from the frozen SHA and the card.
- Do not carry the reviewer's reasoning back into implementation; carry only the
  verdict, corrections and next action.
- Budget the whole session: no more than 1–2 children, each with a compact
  handoff and a ~1M input / ~20 turn cap. Preflight `perch`/`free-pi` once before
  REVIEW. If EXECUTE already consumed the budget, stop and hand the frozen SHA
  to a fresh session instead of auditing in an exhausted context.

## Prohibitions

- No same-session `ACCEPTED`, no merge on your own verdict, no self-written audit
  note.
- No "review" that only re-reads your own summary instead of the diff and test
  evidence.
- No phase overlap to save a turn: an edit after the handoff invalidates the
  candidate and restarts EXECUTE, test and review on the new SHA.
- No UI/style implementation during REVIEW. An audit prompt with "fix it along
  the way" becomes a follow-up `task` lane after the verdict.
