import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SourceKind(str, enum.Enum):
    channel = "channel"
    comment_chat = "comment_chat"
    group = "group"
    private = "private"


class InactiveReason(str, enum.Enum):
    no_access = "no_access"
    disabled_by_user = "disabled_by_user"


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    linked_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sources.tg_id"), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    inactive_reason: Mapped[InactiveReason | None] = mapped_column(
        Enum(InactiveReason), nullable=True
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )
    sync_state: Mapped["SyncState | None"] = relationship(
        back_populates="source",
        uselist=False,
    )
    thread_roots: Mapped[list["ThreadRoot"]] = relationship(
        "ThreadRoot",
        back_populates="source",
        foreign_keys="ThreadRoot.source_id",
        cascade="all, delete-orphan",
    )
    linked_thread_roots: Mapped[list["ThreadRoot"]] = relationship(
        "ThreadRoot",
        back_populates="channel_source",
        foreign_keys="ThreadRoot.channel_source_id",
        cascade="all, delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("source_id", "tg_msg_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id"), nullable=False
    )
    tg_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text: Mapped[str | None] = mapped_column(Text)
    entities: Mapped[list | None] = mapped_column(JSONB)
    reply_to_msg_id: Mapped[int | None] = mapped_column(BigInteger)
    reply_to_top_id: Mapped[int | None] = mapped_column(BigInteger)
    grouped_id: Mapped[int | None] = mapped_column(BigInteger)
    topic_id: Mapped[int | None] = mapped_column(BigInteger)
    fwd_from: Mapped[dict | None] = mapped_column(JSONB)
    views: Mapped[int | None] = mapped_column(Integer)
    reactions: Mapped[dict | None] = mapped_column(JSONB)

    source: Mapped["Source"] = relationship(back_populates="messages")


class ThreadRoot(Base):
    __tablename__ = "thread_roots"
    __table_args__ = (UniqueConstraint("source_id", "root_msg_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    root_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    channel_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    source: Mapped["Source"] = relationship(
        "Source",
        back_populates="thread_roots",
        foreign_keys=[source_id],
    )
    channel_source: Mapped["Source"] = relationship(
        "Source",
        back_populates="linked_thread_roots",
        foreign_keys=[channel_source_id],
    )


class SyncState(Base):
    __tablename__ = "sync_state"

    source_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sources.id"), primary_key=True
    )
    oldest_processed_id: Mapped[int | None] = mapped_column(BigInteger)
    newest_processed_id: Mapped[int | None] = mapped_column(BigInteger)
    backfill_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped["Source"] = relationship(back_populates="sync_state")
