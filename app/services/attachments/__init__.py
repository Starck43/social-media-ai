"""Transient attachment spool (P15-T14 / P19-T05 storage contract)."""

from app.services.attachments.spool import AttachmentSpool, SpooledAttachment, SpoolRejected, attachment_spool

__all__ = ["AttachmentSpool", "SpoolRejected", "SpooledAttachment", "attachment_spool"]
