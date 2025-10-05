from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean
from sqlalchemy.orm import relationship, Mapped

from .base import Base
from ..core.decorators import app_label

if TYPE_CHECKING:
	from . import User, Post, Statistics, AIAnalysisResult


@app_label("social")
class SocialAccount(Base):
	__tablename__ = 'social_accounts'
	__table_args__ = {'schema': 'social_manager'}

	id: Mapped[int] = Column(Integer, primary_key=True)
	user_id: Mapped[int] = Column(Integer, ForeignKey('social_manager.users.id'))
	platform: Mapped[str] = Column(String(20), nullable=False)
	platform_user_id: Mapped[str] = Column(String(100), nullable=False)
	access_token: Mapped[str] = Column(Text, nullable=False)
	refresh_token: Mapped[Optional[str]] = Column(Text)
	token_expires_at: Mapped[Optional[datetime]] = Column(DateTime)
	profile_data: Mapped[Optional[dict]] = Column(JSON)
	is_active: Mapped[bool] = Column(Boolean, default=True)

	# Relationships
	user: Mapped['User'] = relationship("User", back_populates="social_accounts")
	groups: Mapped[list['SocialGroup']] = relationship(
		"SocialGroup",
		back_populates="social_account",
		cascade="all, delete-orphan"
	)

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.social_manager import SocialAccountBaseManager
		objects: ClassVar[SocialAccountBaseManager | BaseManager]
	else:
		objects: ClassVar = None


@app_label("social")
class SocialGroup(Base):
	__tablename__ = 'social_groups'
	__table_args__ = {'schema': 'social_manager'}

	id: Mapped[int] = Column(Integer, primary_key=True)
	social_account_id: Mapped[int] = Column(Integer, ForeignKey('social_manager.social_accounts.id'))
	platform: Mapped[str] = Column(String(20), nullable=False)
	platform_group_id: Mapped[str] = Column(String(100), nullable=False)
	name: Mapped[str] = Column(String(200), nullable=False)
	screen_name: Mapped[Optional[str]] = Column(String(100))
	photo_url: Mapped[Optional[str]] = Column(Text)
	members_count: Mapped[int] = Column(Integer, default=0)
	is_tracking: Mapped[bool] = Column(Boolean, default=True)
	settings: Mapped[dict] = Column(JSON, default=dict)

	# Relationships
	social_account: Mapped['SocialAccount'] = relationship(
		"SocialAccount",
		back_populates="groups"
	)
	posts: Mapped[list['Post']] = relationship(
		"Post",
		back_populates="group",
		cascade="all, delete-orphan"
	)
	statistics: Mapped[list['Statistics']] = relationship(
		"Statistics",
		back_populates="group",
		cascade="all, delete-orphan"
	)
	ai_analyses: Mapped[list['AIAnalysisResult']] = relationship(
		"AIAnalysisResult",
		back_populates="group",
		cascade="all, delete-orphan"
	)

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.social_manager import SocialGroupBaseManager
		objects: ClassVar[SocialGroupBaseManager | BaseManager]
	else:
		objects: ClassVar = None


from .managers.social_manager import SocialAccountBaseManager, SocialGroupBaseManager  # noqa: E402
SocialAccount.objects = SocialAccountBaseManager()
SocialGroup.objects = SocialGroupBaseManager()
