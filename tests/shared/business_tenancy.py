"""Explicit synthetic identities for unit tests and a wrapper around DB fixtures."""

from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class Actor:
    user_id: int
    tenant_id: int


@dataclass(frozen=True)
class BusinessWorld:
    first: Actor
    second: Actor


@pytest.fixture
def business_world() -> BusinessWorld:
    return BusinessWorld(first=Actor(user_id=101, tenant_id=201), second=Actor(user_id=102, tenant_id=202))


@pytest.fixture
def business_db_world(personal_world):
    """Expose the existing manager-backed fixture; it needs the normal DB test route."""
    return personal_world


def assert_cross_tenant_not_found(response) -> None:
    """Require the non-disclosing response, not a generic authorization failure."""
    assert response.status_code == 404
