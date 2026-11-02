"""Versioned connector form contracts; importing this package performs no I/O.

Only providers in WORKSPACE_PROVIDERS have an implemented settings flow.
Other descriptors never enable an adapter or grant network access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

CATALOG_VERSION = 1


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = "text"
    required: bool = True
    hint: str = ""
    example: str = ""


@dataclass(frozen=True)
class Method:
    id: str
    label: str
    fields: tuple[Field, ...]
    hint: str = ""


@dataclass(frozen=True)
class Provider:
    id: str
    platform: str
    label: str
    methods: tuple[Method, ...]
    # Configuration defaults are immutable, non-secret, and not an indication
    # of a working adapter or permission to connect to arbitrary hosts.
    defaults: tuple[tuple[str, str | int | bool], ...] = ()
    source_url: str = ""


_MAIL_FIELDS = (
    Field("account_login", "Адрес электронной почты", kind="email", example="name@example.com"),
    Field(
        "app_password",
        "Пароль приложения",
        kind="secret",
        hint="Создайте отдельный пароль для почтового приложения. Основной пароль от почты не подходит.",
    ),
)
MAILRU = Provider(
    id="mailru_mail",
    platform="email",
    label="Почта Mail.ru",
    methods=(
        Method(
            "app_password",
            "Пароль приложения",
            _MAIL_FIELDS,
            hint="Почта → Настройки → Безопасность → Пароли для внешних приложений. Выберите доступ к Почте, не к Облаку.",
        ),
    ),
    defaults=(
        ("imap_host", "imap.mail.ru"),
        ("imap_port", 993),
        ("imap_tls", True),
        ("smtp_host", "smtp.mail.ru"),
        ("smtp_port", 465),
        ("smtp_tls", True),
        ("send_enabled", False),
    ),
    source_url="https://help.mail.ru/mail/login/mailer",
)
YANDEX_MAIL = Provider(
    id="yandex_mail",
    platform="email",
    label="Яндекс Почта",
    methods=(
        Method(
            "app_password",
            "Пароль приложения",
            _MAIL_FIELDS,
            hint="В настройках почты разрешите доступ по IMAP и паролям приложений.",
        ),
    ),
    defaults=(
        ("imap_host", "imap.yandex.ru"),
        ("imap_port", 993),
        ("imap_tls", True),
        ("smtp_host", "smtp.yandex.ru"),
        ("smtp_port", 465),
        ("smtp_tls", True),
        ("send_enabled", False),
    ),
    source_url="https://yandex.ru/support/yandex-360/customers/mail/ru/mail-clients/others",
)
VK = Provider(
    id="vk",
    platform="vk",
    label="ВКонтакте",
    methods=(
        Method("oauth", "Войти через VK ID", ()),
        Method("user_token", "Личный токен", (Field("user_token", "Личный токен VK", kind="secret"),)),
    ),
)
TELEGRAM = Provider(
    id="telegram",
    platform="telegram",
    label="Telegram",
    methods=(Method("session", "Сессия MTProto", (Field("session", "Сессия Telegram", kind="secret"),)),),
)
YANDEX_DISK = Provider(
    id="yandex_disk",
    platform="yandex_disk",
    label="Яндекс.Диск",
    methods=(Method("oauth_token", "OAuth-токен", (Field("oauth_token", "OAuth-токен Яндекс.Диска", kind="secret"),)),),
)
MAILRU_CLOUD = Provider(
    id="mailru_cloud",
    platform="mailru_cloud",
    label="Облако Mail.ru",
    methods=(
        Method(
            "webdav_password",
            "Пароль приложения WebDAV",
            (
                Field("account_login", "Адрес электронной почты", kind="email"),
                Field("webdav_password", "Пароль приложения WebDAV", kind="secret"),
            ),
        ),
    ),
)
PROVIDERS: Mapping[str, Provider] = MappingProxyType(
    {
        p.id: p
        for p in (
            VK,
            TELEGRAM,
            MAILRU,
            YANDEX_MAIL,
            YANDEX_DISK,
            MAILRU_CLOUD,
        )
    }
)


WORKSPACE_PROVIDERS = (MAILRU, YANDEX_MAIL)
WORKSPACE_PROVIDER_IDS = frozenset(p.id for p in WORKSPACE_PROVIDERS)


class InvalidConnectorInput(ValueError):
    """A value-free input error safe to show or log."""


def validate_input(provider_id: str, method_id: str, values: Mapping[str, object]) -> tuple[dict, dict]:
    """Validate a declared form and separate non-secret configuration from secrets.

    Defaults are fixed by the provider rather than accepted from the form. This
    helper does not write anything or authorize network access. Callers must
    authenticate, enforce workspace ownership and encrypt the returned secrets.
    """
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise InvalidConnectorInput("unknown_provider")
    method = next((m for m in provider.methods if m.id == method_id), None)
    if method is None:
        raise InvalidConnectorInput("unknown_auth_method")
    if not isinstance(values, Mapping) or set(values) - {field.name for field in method.fields}:
        raise InvalidConnectorInput("unknown_field")
    config = dict(provider.defaults)
    secrets = {}
    for field in method.fields:
        raw = values.get(field.name, "")
        if not isinstance(raw, str):
            raise InvalidConnectorInput(f"invalid_{field.name}")
        value = raw.strip()
        if not value:
            if field.required:
                raise InvalidConnectorInput(f"required_{field.name}")
            continue
        if len(value) > 8192 or any(ord(ch) < 32 for ch in value):
            raise InvalidConnectorInput(f"invalid_{field.name}")
        if field.kind == "email":
            local, separator, domain = value.partition("@")
            if (
                not separator
                or len(value) > 254
                or len(local) > 64
                or local.startswith(".")
                or local.endswith(".")
                or ".." in local
                or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
                or not re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", domain
                )
            ):
                raise InvalidConnectorInput(f"invalid_{field.name}")
        if field.kind == "secret":
            secrets[field.name] = value
        else:
            config[field.name] = value
    return config, secrets
