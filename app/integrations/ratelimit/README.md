# Provider rate budgets

Internal foundation only; no business adapter uses this until it explicitly integrates it. This module does not authorize a connection, issue HTTP requests or activate any capability. The caller must authorize the actor and connection, enter the selected workspace's `tenant_scope`, and resolve a verified canonical provider account. For one account within a workspace, adapters must use the same canonical identifier and connection; an inconsistent connection binding fails closed. Each workspace has its own bucket, so its traffic or cooldown cannot affect another workspace. This follows the owner decision; it does not enforce a combined cross-workspace vendor quota.

Use `budget_for(provider, canonical_account, connection_id=connection.id)` to obtain a catalog-controlled policy, and `RateLimiter().acquire(budget, max_wait=...)` immediately before dispatch. Each admission checks the active connection in the current tenant. Disabling a connection deletes its buckets in the same transaction; deleting it cascades. No account identifier or response body is stored or logged: only a domain-separated HMAC is stored. Configure the same stable, independent `RATE_LIMIT_KEY` (URL-safe base64, at least 32 decoded random bytes) for API and runtime. It must differ from credential and PII keys. Changing it without a coordinated migration resets budgets and needs a separate approved plan.

Convert a provider response to a signal within the operation passed to the read/write helper:

```python
signal = provider_signal(provider, status, headers, body)
if signal is not None:
    raise signal
```

`execute_read` retries only `ProviderLimitSignal`, with jitter, a bounded attempt count and a shared wait budget. It records the provider cooldown before retrying. `ProviderDeferred.retry_after` means no successful result is available yet, not an empty result or a provider error. `execute_write` never retries an admitted operation; an exception after admission becomes `WriteOutcomeUnknown` and requires reconciliation. Deferral before admission remains `ProviderDeferred`. A consumed token is not returned after dispatch or cancellation. Cancellation propagates normally; if it occurs after write admission, the caller must also treat the write outcome as unknown and reconcile it.

Every admission consults PostgreSQL; unique-key insertion and `SELECT FOR UPDATE` serialize the tenant/account bucket. The server clock is read after locking. Provider cooldowns cannot shorten existing cooldowns. `max_wait` bounds retry/pause time; connection acquisition and the provider operation need their own timeouts. Lock waits are limited to 250 ms and SQL statements to 2 s; lock contention defers or retries within the wait budget. Database errors fail closed.

`bitrix24`: 2/s with burst 50; `onec`: 10/min, burst 10; `google_calendar`: 30/min, burst 5; `caldav`: 30/min, burst 3. These are conservative code-owned defaults, not verified vendor contracts. Tenant input cannot override them. Dropping the table resets quotas, so a deployed rollback requires a separate coordinated plan.
