"""Version-bound case approval and connector provenance metadata service (P12-T05).

Implements:
  1. Version-bound approval tied to (actor_id, doc_version_hash, formula_version).
  2. Automatic invalidation (`approval_state = "draft_invalidated"`) whenever currency rates,
     VAT mode, or document version hash change.
  3. Explicit separation between internal product approval and legally binding digital signature.
  4. Plain-language 4-layer connector storage architecture summary (aligned with P12-T08)
     for transparent audit of where quote files and emails came from.
"""

from __future__ import annotations

import hashlib
from typing import Any

APPROVAL_STATE_PENDING = "pending_review"
APPROVAL_STATE_APPROVED = "approved"
APPROVAL_STATE_INVALIDATED = "draft_invalidated"

APPROVAL_STATE_LABELS: dict[str, str] = {
    APPROVAL_STATE_PENDING: "Ожидает согласования руководителем",
    APPROVAL_STATE_APPROVED: "Расчёт и выбор поставщика согласованы",
    APPROVAL_STATE_INVALIDATED: "Согласование сброшено (изменились курсы валют, НДС или версия документа)",
}

DEFAULT_FORMULA_VERSION = "calc.procurement.v2.4"

# Four-entity connector storage contract (P12-T08 recommendation)
CONNECTOR_STORAGE_LAYERS: list[dict[str, str]] = [
    {
        "layer_key": "platform_params",
        "what_title": "Общие параметры платформы",
        "what_examples": "Базовый адрес сервиса (API URL), версия протокола, адреса OAuth-авторизации",
        "where_stored": "Глобальный справочник платформ (Platform.params в базе данных, без паролей и ключей)",
        "who_configures": "Администратор системы",
    },
    {
        "layer_key": "integrations_catalog",
        "what_title": "Предустановки сервисов и описание формы",
        "what_examples": "Готовые шаблоны для Mail.ru, Яндекс Почты, Яндекс Диска, 1С: способы входа, поля, подсказки и правила проверки",
        "where_stored": "Версионируемый каталог в коде приложения (app/integrations/catalog/)",
        "who_configures": "Разработчик вместе с адаптером",
    },
    {
        "layer_key": "workspace_connection",
        "what_title": "Конкретное подключение вашей команды",
        "what_examples": "Понятное название («Почта отдела закупок»), выбранный сервис, адрес ящика, способ входа, выбранные папки",
        "where_stored": "Отдельная запись подключения (Connection в БД), привязанная к рабочему пространству и владельцу",
        "who_configures": "Пользователь с правами управления подключениями",
    },
    {
        "layer_key": "encrypted_secrets",
        "what_title": "Пароли, токены и ключи доступа",
        "what_examples": "Пароли приложений, токены доступа (access/refresh tokens) и секретные ключи",
        "where_stored": "Зашифрованное хранилище секретов по защищённой ссылке из подключения (Connection)",
        "who_configures": "Владелец доступа (интерфейс никогда не показывает сохранённые пароли обратно)",
    },
]


def compute_assumptions_signature(
    doc_version_hash: str,
    formula_version: str,
    assumptions_note: str,
) -> str:
    """Compute a deterministic hash binding document version, formula version, and currency/VAT assumptions."""
    payload = f"{doc_version_hash.strip()}|{formula_version.strip()}|{assumptions_note.strip()}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def create_case_record(
    *,
    code: str,
    title: str,
    stage_label: str,
    doc_kind: str,
    doc_version_hash: str,
    formula_version: str = DEFAULT_FORMULA_VERSION,
    assumptions_note: str,
    approval_state: str = APPROVAL_STATE_PENDING,
    approved_actor: str | None = None,
) -> dict[str, Any]:
    """Create a structured procurement/comparison case record matching the web/cases.html contract."""
    if approval_state not in APPROVAL_STATE_LABELS:
        raise ValueError(f"Unsupported approval_state: {approval_state}")

    bound_sig = compute_assumptions_signature(doc_version_hash, formula_version, assumptions_note)
    return {
        "code": code,
        "title": title,
        "stage_label": stage_label,
        "doc_kind": doc_kind,
        "doc_version_hash": doc_version_hash,
        "formula_version": formula_version,
        "approval_state": approval_state,
        "approval_state_label": APPROVAL_STATE_LABELS[approval_state],
        "approved_actor": approved_actor,
        "assumptions_note": assumptions_note,
        "bound_signature": bound_sig if approval_state == APPROVAL_STATE_APPROVED else None,
        "signature_disclaimer": (
            "Внутреннее согласование в системе фиксирует проверенный расчёт цен и выбранного поставщика, "
            "но не заменяет квалифицированную электронную подпись (УКЭП) в ЭДО."
        ),
    }


def approve_case_record(
    case_record: dict[str, Any],
    actor_id: str,
) -> dict[str, Any]:
    """Bind approval to (actor_id, doc_version_hash, formula_version) and current assumptions."""
    if not actor_id or not actor_id.strip():
        raise ValueError("actor_id is required to approve a case.")

    updated = dict(case_record)
    updated["approval_state"] = APPROVAL_STATE_APPROVED
    updated["approval_state_label"] = APPROVAL_STATE_LABELS[APPROVAL_STATE_APPROVED]
    updated["approved_actor"] = actor_id.strip()
    updated["bound_signature"] = compute_assumptions_signature(
        updated["doc_version_hash"],
        updated["formula_version"],
        updated["assumptions_note"],
    )
    return updated


def update_case_assumptions(
    case_record: dict[str, Any],
    *,
    new_assumptions_note: str | None = None,
    new_doc_version_hash: str | None = None,
    new_formula_version: str | None = None,
) -> dict[str, Any]:
    """Update currency/VAT assumptions or document revision; invalidates any prior approval if changed."""
    updated = dict(case_record)
    old_sig = compute_assumptions_signature(
        updated["doc_version_hash"],
        updated["formula_version"],
        updated["assumptions_note"],
    )

    if new_assumptions_note is not None:
        updated["assumptions_note"] = new_assumptions_note
    if new_doc_version_hash is not None:
        updated["doc_version_hash"] = new_doc_version_hash
    if new_formula_version is not None:
        updated["formula_version"] = new_formula_version

    new_sig = compute_assumptions_signature(
        updated["doc_version_hash"],
        updated["formula_version"],
        updated["assumptions_note"],
    )

    if new_sig != old_sig and case_record.get("approval_state") == APPROVAL_STATE_APPROVED:
        updated["approval_state"] = APPROVAL_STATE_INVALIDATED
        updated["approval_state_label"] = APPROVAL_STATE_LABELS[APPROVAL_STATE_INVALIDATED]
        updated["approved_actor"] = None
        updated["bound_signature"] = None

    return updated


CANONICAL_CASE_RECORD: dict[str, Any] = create_case_record(
    code="CASE-2026-042",
    title="Закупка полимерного сырья и катализатора на ноябрьский цикл",
    stage_label="Сравнение предложений и согласование закупки",
    doc_kind="Сводная таблица предложений и проект спецификации № 8",
    doc_version_hash="sha256:6f28a904c11e",
    formula_version=DEFAULT_FORMULA_VERSION,
    assumptions_note=(
        "Цены приведены к рублям (₽) без НДС (ставка НДС 20%). " "Курсы пересчёта: 1 USD = 92,50 ₽ · 1 EUR = 100,20 ₽."
    ),
    approval_state=APPROVAL_STATE_APPROVED,
    approved_actor="saloon.as (Руководитель закупок)",
)
