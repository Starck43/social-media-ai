# Agent Tasks

This page is split by reader (P11-T23):

- [Agent Tasks and Job Queue](AGENT_TASKS_QUEUE.md) — runtime, `agent_tasks`/`jobs`
  tables, source-aware admission, clearing `/app/jobs`, queue health signals,
  run lifecycle, cron, adding/editing tasks, default tasks, job retention,
  tuning, and why not Celery.
- [Agent Task Plans](AGENT_TASKS_PLAN.md) — the owner-approved explicit task-plan
  contract: model, six input groups, chat journey (P8-T39), composed research and
  digest plans (P8-T40), product capabilities, policy, VK vertical,
  compatibility/acceptance and the version-1 example.

Anchors that previously resolved on this page now resolve in the document that
owns the section:

| Old anchor | New location |
| --- | --- |
| `#explicit-task-plans` | [AGENT_TASKS_PLAN](AGENT_TASKS_PLAN.md#explicit-task-plans) |
| `#model` | [AGENT_TASKS_PLAN](AGENT_TASKS_PLAN.md#model) |
| `#user-input-and-surfaces` | [AGENT_TASKS_PLAN](AGENT_TASKS_PLAN.md#user-input-and-surfaces) |
| `#chat-task-journey-p8-t39` | [AGENT_TASKS_PLAN](AGENT_TASKS_PLAN.md#chat-task-journey-p8-t39) |
| `#composed-research-and-digest-plans-p8-t40` | [AGENT_TASKS_PLAN](AGENT_TASKS_PLAN.md#composed-research-and-digest-plans-p8-t40) |
| `#product-capabilities` | [AGENT_TASKS_PLAN](AGENT_TASKS_PLAN.md#product-capabilities) |
| `#policy` | [AGENT_TASKS_PLAN](AGENT_TASKS_PLAN.md#policy) |
| `#vk-vertical` | [AGENT_TASKS_PLAN](AGENT_TASKS_PLAN.md#vk-vertical) |
| `#compatibility-documentation-and-acceptance` | [AGENT_TASKS_PLAN](AGENT_TASKS_PLAN.md#compatibility-documentation-and-acceptance) |
| `#version-1-example` | [AGENT_TASKS_PLAN](AGENT_TASKS_PLAN.md#version-1-example) |
| `#tables` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#tables) |
| `#source-aware-run-admission` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#source-aware-run-admission) |
| `#clearing-the-queue-appjobs` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#clearing-the-queue-appjobs) |
| `#queue-health-signals-appjobs` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#queue-health-signals-appjobs) |
| `#the-reaction-when-to-look-and-what-to-do` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#the-reaction-when-to-look-and-what-to-do) |
| `#how-a-run-happens` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#how-a-run-happens) |
| `#cron-expressions` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#cron-expressions) |
| `#adding-a-task` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#adding-a-task) |
| `#default-tasks` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#default-tasks) |
| `#job-history-and-one-time-runs` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#job-history-and-one-time-runs) |
| `#tuning` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#tuning) |
| `#why-not-celery` | [AGENT_TASKS_QUEUE](AGENT_TASKS_QUEUE.md#why-not-celery) |
