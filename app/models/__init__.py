"""
Application Models Package.

This package contains all data models for the application, including:
- Core models (User, SocialAccount, SocialGroup)
— Content models (Post, Comment)
— Analytics models (Statistics, AIAnalysisResult)
— Notification models (Notification)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Core models
from .base import Base, TimestampMixin
from .model_type import ModelType

# Import all models to ensure they're registered with SQLAlchemy
from .permission import Permission
from .role import Role
from .user import User
from .social import SocialAccount, SocialGroup
from .content import Post, Comment
from .analytics import Statistics, AIAnalysisResult
from .notification import Notification


__all__ = [
    # Base classes
    'Base',
    'TimestampMixin',

    # Models
    'ModelType',
    'Permission',
    'Role',
    'User',
    'SocialAccount',
    'SocialGroup',
    'Post',
    'Comment',
    'Statistics',
    'AIAnalysisResult',
    'Notification',
]

