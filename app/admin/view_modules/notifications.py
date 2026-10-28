"""Notifications SQLAdmin views."""

from __future__ import annotations

from ._shared import (
    ActionType,
    BaseAdmin,
    Notification,
    NotificationType,
    RedirectResponse,
    Request,
    SelectField,
    action,
    logger,
    logging,
    validators,
)

logger = logging.getLogger(__name__)


class NotificationAdmin(BaseAdmin, model=Notification):
    can_create = False
    can_edit = False
    can_delete = False
    name = "Уведомление"
    name_plural = "Уведомления"
    icon = "fa fa-bell"

    # Technical archive only. No action acknowledges any User receipt.
    action_permissions = {
        "send-to-messenger": ActionType.UPDATE,
    }
    inline_columns = ["id", "title", "notification_type", "is_read", "created_at"]
    column_list = ["id", "title", "notification_type", "is_read", "created_at"]
    column_searchable_list = ["title", "notification_type"]
    column_sortable_list = ["created_at", "is_read"]
    column_default_sort = [("created_at", True)]
    column_labels = dict(
        {
            "id": "ID",
            "title": "Заголовок",
            "message": "Сообщение",
            "notification_type": "Тип уведомления",
            "is_read": "Исторический общий флаг (не личное прочтение)",
            "related_entity_type": "Тип сущности",
            "related_entity_id": "ID сущности",
        },
        **BaseAdmin.column_labels,
    )

    column_formatters = {
        "is_read": lambda m, a: "✅ Прочитано" if m.is_read else "📬 Новое",
        "notification_type": lambda m, a: (
            m.notification_type.label
            if m.notification_type and hasattr(m.notification_type, "label")
            else str(m.notification_type) if m.notification_type else "—"
        ),
        **BaseAdmin.column_formatters,
    }

    form_excluded_columns = [] + BaseAdmin.form_excluded_columns

    form_overrides = {"notification_type": SelectField, "is_read": SelectField, **BaseAdmin.form_overrides}

    # Form arguments with choices from MediaType enum
    form_args = {
        "title": {
            "label": "Заголовок",
            "description": "Краткая суть уведомления, показывается в списке",
        },
        "message": {
            "label": "Сообщение",
            "description": "Полный текст уведомления для пользователя",
        },
        "notification_type": {
            "label": "Тип уведомления",
            "description": "Определяет иконку и категорию; значения берутся из общего enum",
            "choices": NotificationType.choices(),  # Use MediaType enum
            "coerce": str,
            "validators": [validators.AnyOf([value for value, _ in NotificationType.choices()])],
        },
        "related_entity_type": {
            "label": "Тип сущности",
            "description": "Тип объекта, к которому относится уведомление: source, platform, analysis",
        },
        "related_entity_id": {
            "label": "ID сущности",
            "description": "ID объекта указанного типа; служит для перехода из уведомления",
        },
        "tenant_id": {
            "label": "Рабочее пространство",
            "description": "Пусто — уведомление видно всем рабочим пространствам",
        },
        "is_read": {
            "label": "Прочитано",
            "description": "Снятая галочка означает, что уведомление ещё не просмотрено пользователем",
            "choices": [(True, "Да"), (False, "Нет")],
            "coerce": lambda x: x == "True" if isinstance(x, str) else bool(x),
        },
        **BaseAdmin.form_args,
    }

    @action(
        name="send_to_messenger",
        label="Отправить в мессенджер",
        add_in_list=False,
        add_in_detail=False,
    )
    async def send_to_messenger_action(self, request: Request):
        """Send notification to messenger (Telegram/VK)."""
        from app.services.notifications.messenger import messenger_service

        pks = request.query_params.get("pks", "")
        if not pks:
            return RedirectResponse(request.url_for("admin:list", identity=self.identity))

        pk = pks.split(",")[0]
        try:
            notification = await Notification.objects.get(id=int(pk))
            if notification is not None and notification.tenant_id is not None:
                result = await messenger_service.send_notification(
                    title=notification.title,
                    message=notification.message,
                    notification_type=notification.notification_type,
                    messenger="telegram",
                    recipient_id=request.query_params.get("recipient_id"),
                    tenant_id=notification.tenant_id,
                )
                if (result.get("telegram") or {}).get("success") is True:
                    logger.info("Notification %s delivered to messenger", pk)
                else:
                    logger.warning(
                        "Notification %s not delivered: explicit owned recipient required or transport failed", pk
                    )
            elif notification is not None:
                logger.warning("Ownerless notification cannot be delivered to a workspace")
        except Exception:
            logger.warning("Notification messenger action failed")

        return RedirectResponse(
            url=request.url_for("admin:list", identity=self.identity),
            status_code=303,
        )


__all__ = [name for name in globals() if not name.startswith("__")]
