"""The platform-role picker on the user edit form.

`User.role` is a *relationship*, so SQLAdmin's stock converter renders it as a
`QuerySelectField`: every row in the role table, labelled with raw `str(obj)`.
That form would offer roles the actor may not hand out, and it would show
`superuser` with no readable label. The manager refuses those writes, so the
form would lie about what is possible.

`RolePickerConverter` therefore builds the `role` field itself: a plain
`SelectField` whose choices are the roles the signed-in actor may assign, code
first, highest rank first. The list is computed per request — `UserAdmin`
overrides `scaffold_form` and binds a fresh converter with the actor's choices —
so one operator's rank never leaks into another's form.
"""

from typing import Any

from sqladmin import fields as sqladmin_fields
from sqladmin.forms import ModelConverter
from wtforms import validators

from app.core.access_catalog import role_label


def role_choices(actor: Any) -> list[tuple[str, str]]:
    """(role code, label) pairs the actor may assign, highest rank first."""
    from app.types import assignable_platform_roles

    return [(role.value, role_label(role.value)) for role in reversed(assignable_platform_roles(actor))]


def role_edit(actor: Any, target: Any) -> tuple[list[tuple[str, str]], str | None]:
    """What the edit form offers for `target`, and why it may be read-only.

    Returns `(choices, locked_reason)`. An empty reason means the select works.
    The two read-only cases are the ones `UserManager.set_platform_role`
    refuses, so the form never invites a write that would come back as 403:

    - **the actor's own account** — a mis-click would demote themselves, and
      the console has no other owner-adjacent path to undo it;
    - **the platform owner's account** — no console, API or CLI operation
      re-roles the owner; the role is a fact, not a setting.

    Whatever is returned, the role is *shown*: the select is filled with the
    target's current role so the page still answers "which role is this?".
    """
    from app.core.access_catalog import role_label

    current = getattr(getattr(target, "role", None), "codename", None)
    shown = [(str(current), role_label(str(current)))] if current else []

    if getattr(actor, "id", None) is not None and getattr(actor, "id", None) == getattr(target, "id", None):
        return shown, "Свою платформенную роль изменить нельзя: её назначает другой администратор платформы"
    if getattr(target, "is_superuser", False):
        return shown, "Роль собственника платформы не меняется из консоли"
    return role_choices(actor), None


class RoleChoiceField(sqladmin_fields.SelectField):
    """A role select whose options are exactly the actor's assignable roles."""

    def __init__(self, *args: Any, choices: list[tuple[str, str]] | None = None, **kwargs: Any) -> None:
        # Choices arrive positionally (WTForms binds `label, validators,
        # coerce, choices`) or by keyword; both must survive the binding.
        call_args = list(args)
        if len(call_args) >= 4:
            call_args[3] = list(choices if choices is not None else call_args[3])
        else:
            kwargs["choices"] = list(choices if choices is not None else kwargs.get("choices", []))
        kwargs.setdefault("validators", [])
        super().__init__(*call_args, **kwargs)

    def process_data(self, value: Any) -> None:
        """The bound object is a `PlatformRole` row; the select works on codenames."""
        codename = getattr(value, "codename", value)
        if value is not None and codename is None:
            raise ValueError("Роль не назначена: выберите роль из списка")
        super().process_data(str(codename) if codename is not None else None)


class RolePickerConverter(ModelConverter):
    """Stock converter everywhere except `role`, which gets a fixed choice list."""

    choices: list[tuple[str, str]] = []
    # Why the select is read-only, when it is. Two cases share the field but not
    # the reason: the actor's own account (a mis-click would demote themselves)
    # and the platform owner's account (nobody re-roles the owner, ever).
    locked_reason: str | None = None

    def _role_field(self, prop: Any, field_widget_args: dict[str, Any], label: str | None):
        widget_args = dict(field_widget_args)
        widget_args.setdefault("class", "form-control")
        description = getattr(prop, "doc", None)
        if self.locked_reason:
            # The role is shown, not offered: the manager refuses the write, so
            # the form must not invite it. Disabled fields are not submitted, so
            # this is also what keeps `update_model` from seeing a change.
            widget_args["disabled"] = True
            description = self.locked_reason
        elif not self.choices:
            # Nothing to assign at this rank: say so instead of rendering an
            # empty select the operator would try to submit.
            widget_args["disabled"] = True
            description = "Нет ролей, которые вы можете назначить"
        return RoleChoiceField(
            label=label,
            choices=self.choices,
            description=description,
            render_kw=widget_args,
            validators=[validators.Optional()],
        )

    async def convert(
        self,
        model: type,
        prop: Any,
        session_maker: Any,
        field_args: dict[str, Any],
        field_widget_args: dict[str, Any],
        form_include_pk: bool,
        label: str | None = None,
        override: type[Any] | None = None,
        form_ajax_refs: dict[str, Any] | None = None,
    ):
        if getattr(prop, "key", None) != "role":
            return await super().convert(
                model,
                prop,
                session_maker,
                field_args,
                field_widget_args,
                form_include_pk,
                label=label,
                override=override,
                form_ajax_refs=form_ajax_refs,
            )

        # `role` is a many-to-one, and SQLAdmin reaches it through
        # `_prepare_kwargs` → `_prepare_relationship`, which adds `data=` (the
        # option list) and `allow_blank=`. `SelectField` takes neither, so the
        # field is built here directly instead of through that path.
        return self._role_field(prop, field_widget_args, label)


__all__ = ["RoleChoiceField", "RolePickerConverter", "role_choices", "role_edit"]
