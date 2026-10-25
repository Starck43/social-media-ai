---
name: opencode-task
description: "Execute a bounded OpenCode task or coordinate its configured native tester."
---

# OpenCode executor

Read AGENTS and the attached task card; load only needed COLLABORATION sections
and linked contracts. Record documents consulted and checks in the task evidence.
Implement only owned files. For tests load test-runner: build owns target preflight
and cleanup, then calls configured `tester` via V2 `subagent`, not V1 `task` or
`--agent tester`. Keep the River model/subagent mode in the user's opencode.jsonc.
Tester runs/analyzes supplied commands, without source edits or extra tests.
Use native children only; preserve evidence before ending their sessions.
