from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Float
from sqlalchemy.orm import relationship, Mapped

from .base import Base
from ..core.decorators import app_label

if TYPE_CHECKING:
	from .managers.social_manager import SocialGroup


@app_label("social")
class Post(Base):
	__tablename__ = 'posts'
	__table_args__ = {'schema': 'social_manager'}

	id: Mapped[int] = Column(Integer, primary_key=True)
	group_id: Mapped[int] = Column(Integer, ForeignKey('social_manager.social_groups.id'))
	platform_post_id: Mapped[str] = Column(String(100), nullable=False)
	text: Mapped[str] = Column(Text)
	likes_count: Mapped[int] = Column(Integer, default=0)
	comments_count: Mapped[int] = Column(Integer, default=0)
	reposts_count: Mapped[int] = Column(Integer, default=0)
	views_count: Mapped[int] = Column(Integer, default=0)
	post_date: Mapped[DateTime] = Column(DateTime)

	# Relationship one-to-one to SocialGroup
	group: Mapped['SocialGroup'] = relationship("SocialGroup", back_populates="posts")
	# Relationship one-to-many to Comment
	comments: Mapped[list['Comment']] = relationship("Comment", back_populates="post")

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.content_manager import PostManager
		objects: ClassVar[PostManager | BaseManager]
	else:
		objects: ClassVar = None


@app_label("social")
class Comment(Base):
	__tablename__ = 'comments'
	__table_args__ = {'schema': 'social_manager'}

	id: Mapped[int] = Column(Integer, primary_key=True)
	post_id: Mapped[int] = Column(Integer, ForeignKey('social_manager.posts.id'))
	platform_comment_id: Mapped[str] = Column(String(100), nullable=False)
	author_id: Mapped[str] = Column(String(100))
	author_name: Mapped[str] = Column(String(200))
	text: Mapped[str] = Column(Text)
	likes_count: Mapped[int] = Column(Integer, default=0)
	sentiment_score: Mapped[float] = Column(Float)
	sentiment_label: Mapped[str] = Column(String(20))
	comment_date: Mapped[DateTime] = Column(DateTime)

	post: Mapped['Post'] = relationship("Post", back_populates="comments")

	# Manager will be set after class definition to avoid circular imports
	if TYPE_CHECKING:
		from .managers.base_manager import BaseManager
		from .managers.content_manager import CommentManager
		objects: ClassVar[CommentManager | BaseManager]
	else:
		objects: ClassVar = None


from .managers.content_manager import PostManager, CommentManager  # noqa: E402
Post.objects = PostManager()
Comment.objects = CommentManager()
