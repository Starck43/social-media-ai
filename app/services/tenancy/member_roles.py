"""Fixed workspace role choices, separate from platform operator roles."""

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
    MemberRole("owner", "owner", "Владелец", "Всё, включая команду, тариф и каналы"),
    MemberRole("admin", "admin", "Соадминистратор", "Настройка сценариев, задач и источников, запуск"),
    MemberRole("manager", "manager", "Менеджер", "Источники и уведомления, просмотр аналитики"),
    MemberRole("analyst", "analyst", "Аналитик", "Аналитика и дайджесты, экспорт"),
    MemberRole("viewer", "viewer", "Наблюдатель", "Только просмотр источников, аналитики и дайджестов"),
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
    """The level key of a membership, or None when no role is assigned.

    A NULL role means "no role assigned" (fail-closed since fdb6c18):
    it grants no ownership and maps to no level, so callers show
    "Роль не назначена" and deny workspace rights.
    """
    if getattr(membership, "role_id", None) is None:
        return None
    role = getattr(membership, "role", None)
    codename = getattr(role, "codename", None)
    name = getattr(codename, "value", codename)
    return next((level.key for level in MEMBER_ROLES if level.codename == name), None)
