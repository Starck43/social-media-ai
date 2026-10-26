"""Opt-in Django-style two-list enhancement of SQLAdmin's relationship field.

Keep SQLAdmin's offered choices, validation and submitted PKs unchanged. Assets
use the existing permission-transfer filenames for backwards compatibility.
"""

from sqladmin.editors import FieldMedia
from sqladmin.fields import QuerySelectMultipleField


class FilteredSelectMultipleField(QuerySelectMultipleField):
    """Use in ``form_overrides``; optional ``transfer_mode="permissions"`` groups codes.

    The native multiple select remains the sole form value source and no-JS
    fallback. This class does not fetch options or implement access control.
    """

    def __init__(self, *args, transfer_mode="plain", **kwargs):
        if transfer_mode not in {"plain", "permissions"}:
            raise ValueError("Unknown filtered-select mode")
        self.transfer_mode = transfer_mode
        super().__init__(*args, **kwargs)

    def __call__(self, **kwargs):
        kwargs.setdefault("data-filtered-select", self.transfer_mode)
        if self.transfer_mode == "permissions":
            kwargs.setdefault("data-permission-transfer", "true")
        kwargs.setdefault("size", 10)
        return super().__call__(**kwargs)

    @property
    def media(self):
        return FieldMedia(
            css=["/static/css/permission-transfer.css"],
            js=["/static/js/permission-transfer.js"],
        )
