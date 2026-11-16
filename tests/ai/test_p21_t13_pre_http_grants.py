"""Grant freshness after reservation, against the accepted pure dispatch seam."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.services.ai.dispatch_seam import (
    CODE_CATALOG_ACCESS_MISSING,
    CODE_PLAN_GRANT_MISSING,
    DispatchContext,
    DispatchDenial,
    DispatchFoundation,
    DispatchGrant,
    DispatchLease,
    DispatchRequest,
    DispatchReservation,
    admit,
)

__source_only__ = True


def foundation(events, *, revoke=False, replacement=None):
    context = DispatchContext(
        actor_user_id=2,
        tenant_id=3,
        plan_holder_user_id=1,
        funding_class="platform",
        plan_policy_version=4,
        credential_version=5,
        credential_ref="deployment-alias",
        payer="holder:1",
    )

    class Authority:
        async def resolve_identity(self, request, hints):
            return context

        async def resolve_credential(self, request, context):
            return context

        async def resolve_payer(self, request, context):
            return context

        async def revalidate(self, admission):
            events.append("authority_revalidated")
            return admission.context

    class Grants:
        def __init__(self):
            self.calls = 0

        async def resolve(self, request, context):
            self.calls += 1
            events.append(f"grant_{self.calls}")
            if self.calls > 1 and revoke:
                raise DispatchDenial(CODE_PLAN_GRANT_MISSING, "step4_grants")
            return DispatchGrant(replacement if self.calls > 1 and replacement is not None else "7")

    class Rate:
        async def acquire(self, request, context):
            events.append("lease")
            return DispatchLease(request.dispatch_id)

        async def release(self, admission):
            events.append("release")

    class Admission:
        async def check_allowance(self, request, context):
            return True

        async def reserve(self, request, context):
            events.append("reservation_committed")
            return DispatchReservation(request.dispatch_id, context.credential_version, {"receipt": "synthetic"})

    class Settlement:
        async def settle(self, admission, *, outcome, evidence=None):
            events.append(outcome)

    return DispatchFoundation(
        authority=Authority(), grants=Grants(), rate=Rate(), admission=Admission(), settlement=Settlement()
    )


def request():
    return DispatchRequest(
        model=SimpleNamespace(id=10, provider=SimpleNamespace(id=11, api_format="openai"), capabilities=["text"]),
        purpose="chat",
        stage="chat",
    )


@pytest.mark.asyncio
async def test_grant_revoke_after_receipt_refunds_and_releases_before_http():
    events = []
    with pytest.raises(DispatchDenial) as denied:
        await admit(request(), foundation=foundation(events, revoke=True))
    assert denied.value.code == CODE_PLAN_GRANT_MISSING
    assert events == [
        "grant_1",
        "lease",
        "reservation_committed",
        "authority_revalidated",
        "grant_2",
        "not_dispatched",
        "release",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog", ["8", "", None])
async def test_changed_or_invalid_fresh_catalog_never_rebinds(catalog):
    events = []
    injected = foundation(events, replacement=catalog)
    if catalog is None:
        grants = injected.grants
        original = grants.resolve

        async def invalid(request, context):
            result = await original(request, context)
            return None if grants.calls > 1 else result

        injected = replace(injected, grants=SimpleNamespace(resolve=invalid))
    with pytest.raises(DispatchDenial) as denied:
        await admit(request(), foundation=injected)
    assert denied.value.code == CODE_CATALOG_ACCESS_MISSING
    assert events[-2:] == ["not_dispatched", "release"]


@pytest.mark.asyncio
async def test_unchanged_grant_rechecked_after_receipt_and_before_return():
    events = []
    admitted = await admit(request(), foundation=foundation(events))
    assert admitted.reserved is True
    assert admitted.context.catalog_id == "7"
    assert events == ["grant_1", "lease", "reservation_committed", "authority_revalidated", "grant_2"]


@pytest.mark.asyncio
async def test_compatibility_mode_does_not_resolve_grants():
    admitted = await admit(request())
    assert admitted.reserved is False
    assert admitted.reservation is None


@pytest.mark.asyncio
async def test_hundreds_concurrent_mocked_attempts_keep_separate_receipts_and_fresh_grants():
    import asyncio
    from time import perf_counter

    count = 256
    events = [[] for _ in range(count)]
    started = perf_counter()
    admitted = await asyncio.gather(*(admit(request(), foundation=foundation(item)) for item in events))
    elapsed = perf_counter() - started
    assert len({item.dispatch_id for item in admitted}) == count
    assert all(item.reserved and item.context.catalog_id == "7" for item in admitted)
    assert all(item.count("grant_1") == item.count("grant_2") == 1 for item in events)
    assert all(item.count("reservation_committed") == 1 for item in events)
    print(f"mocked concurrent attempts={count}; elapsed_seconds={elapsed:.6f}; fresh grant checks={2 * count}")
