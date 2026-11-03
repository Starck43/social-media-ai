"""Tenant-owned contact aggregate. No merge execution or provider import."""

from .core import Contact, ContactConsent, ContactIdentity, ContactMergeJournal

__all__ = ["Contact", "ContactIdentity", "ContactConsent", "ContactMergeJournal"]
