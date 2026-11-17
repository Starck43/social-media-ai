# P8-T29 — live VK collection-path gate diagnostic (2026-11-18)

Status: **cannot proceed in this environment**. This is an acceptance-gate
diagnostic, not an audit acceptance and not a reopening of the resolved Q-5
profile/method question.

Candidate: `f7c309127f27f938563efe3fd35c53e33a3bff24` and its integrated
descendants through `27bb1250df81ef7b5b5426ec5ad457543b1cd984` on `origin/dev`.
Owner authorized one bounded live task-path run on 2026-11-18.

## Passed

- Exact-revision source-only batch: 82 passed (10 P8-T29 paths).
- DB-backed acceptance paths: 69 passed across six files on isolated
  `test_schema`; synthetic residue verified removed; schema inventory unchanged
  after the migration baseline was reconciled to `dev` head.
- Full check commands, counts, pre-existing unrelated failures and the stale
  fake correction are recorded in [the task card](../../tasks/p8-t29.md).

## Failed / unrun

- One live `newsfeed.search` through the real `VKCollectionAdapter` and
  `VKClient` (one request, `count=1`, personal credential resolved from the
  owner's vault row 19, no token printed or stored) failed at transport:
  `httpx.ConnectError`. No response, no staged rows, no public-schema write.
- Network triage from the same machine: `curl --max-time 8` to
  `https://api.vk.com/method/users.get`, `https://vk.com` and
  `https://api.vk.ru/method/users.get` all timed out (`http_code=000`), while
  `https://example.com` returned 200. The paired browse relay was not
  connected, so no alternative browser network path was available.
- Consequence: the card's remaining requirement — exact-revision evidence that
  bounded official-API results feed staging/analysis with honest provenance and
  coverage — is **unrun**. The historical 2026-10-26 timeouts and this attempt
  are diagnostic only and are neither PASS nor a claim that VK refused a method.

## Recommended fix

Run the same bounded path from a network where VK hosts answer (the network used
for the accepted Q-5 probe), without changing credentials or scope. The failing
script was, in full:

```python
import asyncio
from app.models import Platform
from app.services.collection.adapters.vk import VKCollectionAdapter
from app.services.social.vk_client import VKClient

class SingleRequestVKClient(VKClient):
    async def _fetch_page(self, method, params):
        response = await self._make_request(method, params)
        self._raise_api_error(response)
        return response

async def main():
    platform = await Platform.objects.filter(platform_type="vk").first()
    page = await VKCollectionAdapter(SingleRequestVKClient(platform), owner_user_id=2).search_posts(
        query="ремонт", since=None, until=None, limit=1, cursor=None
    )
    print(len(page.items), page.total_available)

asyncio.run(main())
```

Run it as `PYTHONPATH=<task worktree> DB_SCHEMA=public .venv/bin/python <script>`
with the owner's `.env` loaded, then stage the captured normalized items in
exclusive `test_schema` through the real collector and verify provenance and
coverage before cleanup. Alternatively connect the browse relay and repeat the
transport check; if neither is possible, the owner must explicitly descope the
live gate in the task card before acceptance.

## Scope and evidence limits

No secrets, sessions or generated data are committed. No public-schema data was
written. Q-5's accepted profile/method evidence stands and was not re-probed as
a profile decision. This diagnostic does not accept P8-T29 and does not change
its separate acceptance criteria.
