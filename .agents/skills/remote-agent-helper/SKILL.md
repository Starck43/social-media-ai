---
name: remote-agent-helper
description: "Audit or verify a bounded remote PR locally; coordinate through PR comments."
---

# Local PR helper

Require repo/PR URL and expected source SHA. Read AGENTS, PR body and task card;
verify the isolated task checkout and ownership. Load only needed contracts and
record actual documentation reads. For review use audit; assigned test execution
uses test-runner. Publish findings/checks via PR comments. Correct only authorized
owned paths; never self-accept an executor result or infer merge/deploy permission.
