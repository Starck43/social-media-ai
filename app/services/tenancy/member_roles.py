"""Workspace access levels an owner gives a member, mapped onto existing roles.

A level is not a new permission matrix: each one names a `Role` row whose
seeded rights (`scripts/setup/assign_roles_permissions.py`) apply to the
member inside this workspace only (see `app/core/permissions.py`). The owner
level is the canonical owner role and is never handed out through an invite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemberRole:
    key: str
    codename: str
    label: str
    hint: str


MEMBER_ROLES: tuple[MemberRole, ...] = (
    MemberRole("owner", "SUPERUSER", "Владелец", "Всё, включая команду, тариф и каналы"),
    MemberRole("admin", "ADMIN", "Соадминистратор", "Настройка сценариев, задач и источников, запуск"),
    MemberRole("manager", "MANAGER", "Менеджер", "Источники и уведомления, просмотр аналитики"),
    MemberRole("analyst", "ANALYST", "Аналитик", "Аналитика и дайджесты, экспорт"),
    MemberRole("viewer", "VIEWER", "Наблюдатель", "Только просмотр источников, аналитики и дайджестов"),
)

BY_KEY: dict[str, MemberRole] = {role.key: role for role in MEMBER_ROLES}
# Old form value: «Участник» was a read-only member.
LEGACY_KEYS: dict[str, str] = {"member": "viewer"}
INVITABLE: tuple[MemberRole, ...] = tuple(role for role in MEMBER_ROLES if role.key != "owner")
DEFAULT_INVITE_KEY = "viewer"


def resolve(key: Any, *, invitable_only: bool = False) -> MemberRole | None:
    """The level for a submitted key, or None when it is unknown or not allowed."""
    if not isinstance(key, str):
        return None
    normalized = key.strip().lower()
    role = BY_KEY.get(LEGACY_KEYS.get(normalized, normalized))
    if role is None or (invitable_only and role.key == "owner"):
        return None
    return role


def key_of(membership: Any) -> str | None:
    """The level key of a membership (legacy NULL role is an owner), or None."""
    if getattr(membership, "role_id", None) is None:
        return "owner"
    role = getattr(membership, "role", None)
    codename = getattr(role, "codename", None)
    name = getattr(codename, "name", codename)
    return next((level.key for level in MEMBER_ROLES if level.codename == name), None)
