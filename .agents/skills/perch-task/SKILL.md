---
name: perch-task
description: "Execute one bounded task in Perch through `perch run`, or hand one scoped job to it as an external executor."
---

# Perch executor

Perch is an external operator invoked as a process, not a native subagent. It is
installed into the Node 24 global prefix only, so use the shim `~/.local/bin/perch`
(Node 24 + PATH in one place, works from non-interactive shells). Without the shim,
a shell on another Node major resolves no `perch` at all.

Perch keeps state under `$HOME/.perch`. A sandboxed caller that cannot write it
fails with `EPERM ... mkdir ~/.perch/threads/...`; grant that directory or export
`PERCH_HOME=<scratch dir>` per call. Auth is stored separately, so the account
stays signed in either way.

## When to use it

Prefer the configured native child. Use Perch for a bounded, self-contained job
in a dedicated folder and say so in the card: analysis, drafting, a focused
scripted check. It never owns HEAD, the registry, DB setup or cleanup — the
calling agent keeps preflight, evidence and cleanup.

## Invocation

```
perch run "<task>" --json --cwd <target-dir> --permission auto_review \
  --model deepseek-v4.1-flash-1017 --mode agents
```

- `--json` returns `status`, `assistantText`, `error`, `threadId` and
  `contextSnapshot` (`modelId`, `toolCallsByType`, context tokens).
- `--cwd` sets the working folder; `perch run` also creates `.perch/` there, so
  use a scratch or task directory, never a clean project root.
- `--permission auto_review` lets reversible writes run; `default` stops for
  every write and is unusable headless. Force pushes and bulk deletions still
  ask — a headless turn then ends `approval_required`, which is a task-related
  BLOCKED, not something to route around.
- `--thread <id>` continues a previous turn; the id comes from the JSON body.
- `--model` accepts a Roost tier (`standard`, `standard 1m`, `pro`, `pro 1m`),
  `auto`, **and an exact pin**, although `perch --help` lists only the tiers. The
  project pin is the bare short id — see [Model identifiers](#model-identifiers).
  Tiers above the account plan are accepted by the flag and then fail on the
  first call with `status: "failed"`; read `status`, never exit code alone.
- `--mode plan` is read-only; use `ask` for one question without tool work.
- Shell-local models and keys go through `perch byolm` / `perch byok`; they do
  not consume Perch Tokens and never fall back to Roost silently.
- A `status: "failed"` whose `error` is a transient provider message (model busy,
  rate limit, timeout) may be retried once with the identical command; that is
  not a permission change. Permission-, approval- or plan-related failures are
  reported, never retried with a wider mode.

## Model identifiers

The two sides of this integration use **opposite** naming rules — mixing them up
fails on both sides:

| Where | Accepted form | Rejected form |
| --- | --- | --- |
| `perch --model` | bare id: `deepseek-v4.1-flash-1017` (tier or `auto` also allowed) | any `provider/…` prefix |
| OpenCode `--model` / agent `model` | `provider/modelID`: `river/deepseek-ai/DeepSeek-V4.1-Flash-1017` | bare id |

Three strings describe "the same" model; only the first is an input to Perch:

| String | Where it belongs |
| --- | --- |
| `deepseek-v4.1-flash-1017` | **only this one** goes into `perch --model` |
| `deepseek/deepseek-v4.1-flash` + label `DeepSeek V4.1 Flash (1017)` | `contextSnapshot.modelId` / `modelLabel` in the JSON reply — evidence, never an input |
| `river/deepseek-ai/DeepSeek-V4.1-Flash-1017` | OpenCode's own provider model — required there, invalid for Perch |

`modelId` is the **upstream provider slug**, not an echo of the `--model`
argument: it drops the catalog suffix and varies by provider (`kimi-3` reports
`azure_foundry` / `fw-kimi-k3`, `glm-5.3` reports `openrouter` / `z-ai/glm-5.3`).
So never assert that `modelId` equals the string you passed — it cannot. Confirm
the pin from `modelLabel` (it carries the catalog name, e.g. `(1017)`) together
with `provider`, and treat a `modelLabel` outside the requested build as the
finding.

Perch: a provider prefix (`deepseek/…`, `openrouter/…`, `river/…`) makes the call
fail before any work — exit code 1, **empty stdout**, and `Unknown model: <arg>`
plus the full help on stderr. Because there is no JSON in that case, check that
the captured output actually parses before reading `status`; an unparseable
capture is the unknown-model failure, not a model result.

OpenCode the other way round: a bare id is rejected as `Invalid model reference`
(no provider at all) or `Model unavailable` (provider missing from the id), so
never strip the `river/` prefix from OpenCode's own model settings.

`--model auto` is valid and picks Roost's own choice (Standard), so it is not
evidence for the pin: confirm `contextSnapshot.modelLabel` is the DeepSeek 4.1
Flash 1017 build (and `modelId` is its upstream slug, `deepseek/deepseek-v4.1-flash`)
and treat anything else as a finding, not a pass.

## Roles

Declare this skill at session start, then load `task` and follow it; this skill
adds only the Perch execution delta. Record the exact `perch` command, `status`,
`modelLabel`/`modelId` and the JSON receipt in the card/PR. Perch never owns HEAD,
the registry, DB setup or cleanup. Delete the Perch threads you started.
