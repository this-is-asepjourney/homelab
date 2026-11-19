"""SQLAlchemy models for registered projects and user memberships."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    memberships: Mapped[list["UserProjectMembership"]] = relationship(
        "UserProjectMembership",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    deployments: Mapped[list["DeploymentRecord"]] = relationship(
        "DeploymentRecord",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class DeploymentRecord(Base):
    __tablename__ = "deployment_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), default="v1.0.0", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(128), default="admin", nullable=False)
    duration_seconds: Mapped[int] = mapped_column(default=0, nullable=False)
    health_check: Mapped[str] = mapped_column(String(32), default="passed", nullable=False)
    logs: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="deployments")


class UserProjectMembership(Base):
    __tablename__ = "user_project_memberships"
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_user_project_membership"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    membership_role: Mapped[str] = mapped_column(String(64), default="member", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    project: Mapped["Project"] = relationship("Project", back_populates="memberships")

