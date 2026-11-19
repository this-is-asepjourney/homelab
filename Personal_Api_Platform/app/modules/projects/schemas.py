"""Pydantic schemas for project registration, membership, and deployment orchestration."""

from datetime import datetime
from typing import Any, Dict, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9][a-z0-9\-_]*$")
    name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = Field(default=None, max_length=500)
    status: str = Field(default="active", pattern=r"^(active|inactive|archived)$")


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=255)
    description: Optional[str] = Field(default=None, max_length=500)
    status: Optional[str] = Field(default=None, pattern=r"^(active|inactive|archived)$")


class ProjectMembershipCreate(BaseModel):
    user_id: uuid.UUID
    membership_role: str = Field(default="member", min_length=2, max_length=64)


class ProjectMembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    membership_role: str
    created_at: datetime


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime


# Deployment Orchestration Schemas (Section 4.2)
class DeployRequest(BaseModel):
    version: Optional[str] = Field(default="latest", description="Release version tag")
    commit_hash: Optional[str] = Field(default=None, description="Git commit hash")
    branch: Optional[str] = Field(default="main", description="Target git branch")
    environment_variables: Optional[Dict[str, str]] = Field(default_factory=dict)


class RollbackRequest(BaseModel):
    target_deployment_id: Optional[str] = Field(default=None, description="Previous deployment ID to rollback to")
    version: Optional[str] = Field(default=None, description="Target version tag")


class DeploymentRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deployment_id: str
    project: str
    status: str
    version: str
    duration_seconds: int
    health_check: str
    commit_hash: Optional[str] = None
    image_tag: Optional[str] = None
    logs: Optional[str] = None
    created_at: Optional[datetime] = None


class ProjectLogsResponse(BaseModel):
    project: str
    logs: str


class ProjectActionResponse(BaseModel):
    project: str
    action: str
    status: str
    message: str
