# Collection task rerun and launch audit

Date: 2026-10-17  
Source revision: `b70911d6c1e0c6a71e5668a502df42f1e73fefa9`  
Scope: signed-in operator's collect task 1582, linked source 2939. The owner
authorized these two runs and writes in the development `public` schema. No
application data was edited manually, and no source or task settings were
changed during the runs.

## Observed task behavior

At test time task 1582 was a one-source `collect` task with `force_refresh` and
`force_reanalyze` enabled, start date `2026-04-01`, and no `analyze_inline`
override. Existing Job 1736 had already failed on the same source before this
Safari reproduction.

| Action | Job | Duration | Safari result |
| --- | ---: | ---: | --- |
| Edit page: “Сохранить и выполнить” | 1737 | 5.20 s | Modal opened; partial result, 1 source error, 0 items |
| Task list: “Выполнить сейчас” | 1738 | 5.12 s | Modal showed running progress, then the same partial result |

Both jobs persisted `sources=1`, `collected=0`, `error=1`, `items=0`,
`new_items=0`, and a per-source `error` outcome. Raw rows for source 2939
remained at zero; its 23 existing analytics rows were unchanged. Neither run
added collection data or overwrote analytics. The task's summary status still
read `ok` even though each Job reported one failed source.

## Cause and launch findings

The three runs failed before requesting the source wall. The VK client attempted
to resolve the configured screen name through `utils.resolveScreenName`; the
request raised `httpx.ConnectTimeout` during TLS connection setup. This is a
connectivity failure, not an observed VK authorization rejection. The exact
proxy, firewall, or network cause is unknown; no further network probes were
made. The handler recorded the source outcome as an error and allowed the Job
to finish with a partial result.

The initially reported missing progress modal was not reproduced: both Safari
actions redirected to a Job-specific task-list URL and displayed the modal. A
passive browser observer saw no `run_now` entry in a `FormDataEvent` for the edit
form, but that request nevertheless created Job 1737. This observation does not
establish a form-submission defect. No launch code was changed.

## What “rebuild and update” does to stored data

`force_refresh` asks the platform collector to fetch the requested date window;
it does not clear raw or analytics tables. Collect jobs default to
`analyze_inline=false`, so fetched content is staged in `collected_items` for a
separate analyze job. Staging inserts new source/external-ID pairs and ignores
conflicts, leaving an already staged row intact rather than replacing its
contents. A collection that fails before the wall request stages nothing.

`force_reanalyze` is a separate analyzer option. On a collect job without inline
analysis it does not cause immediate analysis. A later analyze job can update
the existing daily analytics row for the same source and date; its content hash
ledger is merged. Raw staging is retired only for content covered by a
successfully saved analysis, with old staged rows also subject to retention.

## Follow-up

The run modal showed “Выполнено частично” while the task summary remained `ok`
after all linked sources errored. An all-source failure should be distinguishable
from partial success in both views. The VK connection timeout needs
environment-level diagnosis; this audit did not identify its underlying network
cause.

No application code, migrations, or tests were changed or run. Existing source
and analytics data were preserved.
