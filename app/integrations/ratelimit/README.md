# Provider rate budgets

Internal foundation only; no business adapter uses this until it explicitly integrates it. This module does not authorize a connection, issue HTTP requests or activate any capability. The calling adapter must first authorize the actor/workspace and resolve the provider's **verified canonical account identity**, identical across workspaces and processes for the same real account. Aliases, usernames, supplied arbitrary IDs or per-connection IDs can split a quota and are not safe substitutes. No identity or response body is logged or stored: only a domain-separated keyed digest is stored. API and runtime must share the stable `PII_INDEX_KEY`; changing the key without a coordinated migration resets budgets, so rotation requires a separate approved plan.

Use `budget_for(provider, account)` to obtain a catalog-controlled policy, `RateLimiter().acquire(budget, max_wait=...)` to spend a token immediately before dispatch, and convert a provider response to a signal:

```python
signal = provider_signal(provider, status, headers, body)
if signal is not None:
    raise signal
```

`execute_read` retries only `ProviderLimitSignal`, with jitter, a bounded attempt count and a shared wait budget. It records the provider cooldown before retrying. `ProviderDeferred.retry_after` means no successful result is available yet, **not** an empty result or a provider error. `execute_write` never retries an admitted operation; an exception after admission becomes `WriteOutcomeUnknown` and requires reconciliation. Deferral before admission remains `ProviderDeferred`. A consumed token is not returned after dispatch or cancellation. Cancellation propagates normally; if it occurs after write admission, the caller must also treat the write outcome as unknown and reconcile it.

Every admission consults PostgreSQL. The bucket is serialized by unique-key insertion and `SELECT FOR UPDATE`, using the database clock after lock acquisition. Provider cooldowns never shorten an existing cooldown. `max_wait` bounds retry/pause time; database connection acquisition and the provider operation itself still need their own timeouts. Lock waits are limited to 250 ms and SQL statements to 2 s; lock contention defers or retries inside the same wait budget. Database errors fail closed.

`bitrix24`: 2/s with burst 50; `onec`: 10/min, burst 10; `google_calendar`: 30/min, burst 5; `caldav`: 30/min, burst 3. These are conservative code-owned defaults, not verified vendor contracts. Tenant input cannot override them. Cross-workspace global technical bucket design awaits owner confirmation; no generic CRUD, tenant data or credentials are exposed. Roll back the draft before merge by removing this package, model registration, migration and the migration hook; after deployment, dropping the table resets all quotas, so a safe rollout/rollback requires a separate coordinated plan.
