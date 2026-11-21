"""Task operation catalog. Provider setup descriptors do not authorize operations."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Direction(StrEnum):
    READ = "read"
    WRITE = "write"


class ResourceKind(StrEnum):
    MAIL_FOLDER = "mail_folder"
    DISK_FOLDER = "disk_folder"
    PORTAL = "portal"
    PAIRED_ROOT = "paired_root"
    SOCIAL_SOURCE = "social_source"


@dataclass(frozen=True)
class Capability:
    provider: str
    kind: ResourceKind
    handler: str | None
    method: str | None = None
    installation_keys: tuple[str, ...] = ()
    direction: Direction = Direction.READ


CAPABILITIES = MappingProxyType(
    {
        "mailru_mail.headers.read": Capability("mailru_mail", ResourceKind.MAIL_FOLDER, "imap_sync", "app_password"),
        "yandex_mail.headers.read": Capability("yandex_mail", ResourceKind.MAIL_FOLDER, "imap_sync", "app_password"),
        "yandex_disk.folders.list": Capability(
            "yandex_disk", ResourceKind.DISK_FOLDER, "list_owned_folder", "oauth_token"
        ),
        "mailru_cloud.folders.list": Capability(
            "mailru_cloud", ResourceKind.DISK_FOLDER, "list_owned_folder", "webdav_password"
        ),
        "bitrix24.tasks.list": Capability("bitrix24", ResourceKind.PORTAL, "bitrix24_task_projection"),
        "local_gateway.search": Capability("local_gateway", ResourceKind.PAIRED_ROOT, "connector_gateway"),
        "vk.posts.read": Capability("vk", ResourceKind.SOCIAL_SOURCE, None, installation_keys=("VK_APP_ID",)),
        "telegram.session.read": Capability("telegram", ResourceKind.SOCIAL_SOURCE, None, "session"),
        "telegram.bot.read": Capability(
            "telegram", ResourceKind.SOCIAL_SOURCE, None, installation_keys=("TELEGRAM_BOT_TOKEN",)
        ),
    }
)
