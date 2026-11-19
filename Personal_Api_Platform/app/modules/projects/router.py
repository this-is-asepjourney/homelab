"""HTTP router for project registration, membership, and deployment orchestration."""

from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import Principal
from app.dependencies import require_any_scope, require_scopes
from app.modules.projects.schemas import (
    DeployRequest,
    DeploymentRecordRead,
    ProjectActionResponse,
    ProjectCreate,
    ProjectLogsResponse,
    ProjectMembershipCreate,
    ProjectMembershipRead,
    ProjectRead,
    ProjectUpdate,
    RollbackRequest,
)
from app.modules.projects.service import ProjectService
from app.schemas import success_response

router = APIRouter(prefix="/projects", tags=["Projects & Deployment Center"])
deployments_router = APIRouter(prefix="/deployments", tags=["Deployment Center"])


@router.get("")
def list_projects(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(require_scopes("projects:read")),
    db: Session = Depends(get_db),
):
    service = ProjectService(db)
    projects, total = service.list_projects(principal, page=page, page_size=page_size)
    data = [ProjectRead.model_validate(p).model_dump(mode="json") for p in projects]
    return success_response(
        data=data,
        message="Projects retrieved successfully",
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    principal: Principal = Depends(require_scopes("projects:manage")),
    db: Session = Depends(get_db),
):
    service = ProjectService(db)
    project = service.create_project(payload, principal)
    return success_response(
        data=ProjectRead.model_validate(project).model_dump(mode="json"),
        message="Project registered successfully",
    )


@router.get("/{project_id}")
def get_project(
    project_id: str,
    principal: Principal = Depends(require_scopes("projects:read")),
    db: Session = Depends(get_db),
):
    service = ProjectService(db)
    project = service.get_project(project_id, principal)
    return success_response(
        data=ProjectRead.model_validate(project).model_dump(mode="json"),
        message="Project details retrieved successfully",
    )


@router.patch("/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    principal: Principal = Depends(require_scopes("projects:manage")),
    db: Session = Depends(get_db),
):
    service = ProjectService(db)
    project = service.update_project(project_id, payload, principal)
    return success_response(
        data=ProjectRead.model_validate(project).model_dump(mode="json"),
        message="Project updated successfully",
    )


@router.post("/{project_id}/members", status_code=status.HTTP_201_CREATED)
def add_project_member(
    project_id: str,
    payload: ProjectMembershipCreate,
    principal: Principal = Depends(require_scopes("projects:manage")),
    db: Session = Depends(get_db),
):
    service = ProjectService(db)
    membership = service.add_member(project_id, payload, principal)
    return success_response(
        data=ProjectMembershipRead.model_validate(membership).model_dump(mode="json"),
        message="Project member added successfully",
    )


# ── Deployment Lifecycle Endpoints (Architecture Blueprint Section 4.2) ──


@router.post("/{project_id}/deploy", status_code=status.HTTP_202_ACCEPTED)
def deploy_project(
    project_id: str,
    payload: Optional[DeployRequest] = None,
    principal: Principal = Depends(require_any_scope("deployments:write", "projects:manage")),
    db: Session = Depends(get_db),
):
    """Triggers asynchronous project deployment orchestrating with PSM/Worker."""
    service = ProjectService(db)
    req = payload or DeployRequest()
    deployment = service.deploy_project(project_id, req, principal)
    return success_response(
        data=deployment.model_dump(mode="json"),
        message=f"Deployment '{deployment.deployment_id}' initiated successfully",
    )


@router.post("/{project_id}/restart")
def restart_project(
    project_id: str,
    principal: Principal = Depends(require_any_scope("deployments:write", "projects:manage", "docker:restart")),
    db: Session = Depends(get_db),
):
    """Restarts runtime container for the given project via PSM."""
    service = ProjectService(db)
    result = service.restart_project(project_id, principal)
    return success_response(
        data=result.model_dump(mode="json"),
        message=result.message,
    )


@router.post("/{project_id}/stop")
def stop_project(
    project_id: str,
    principal: Principal = Depends(require_any_scope("deployments:write", "projects:manage")),
    db: Session = Depends(get_db),
):
    """Stops runtime container for the given project via PSM."""
    service = ProjectService(db)
    result = service.stop_project(project_id, principal)
    return success_response(
        data=result.model_dump(mode="json"),
        message=result.message,
    )


@router.post("/{project_id}/rollback")
def rollback_project(
    project_id: str,
    payload: Optional[RollbackRequest] = None,
    principal: Principal = Depends(require_any_scope("deployments:write", "projects:manage")),
    db: Session = Depends(get_db),
):
    """Rolls back project container to a previous deployment version."""
    service = ProjectService(db)
    req = payload or RollbackRequest()
    result = service.rollback_project(project_id, req, principal)
    return success_response(
        data=result.model_dump(mode="json"),
        message=f"Rollback deployment '{result.deployment_id}' executed successfully",
    )


@router.get("/{project_id}/logs")
def get_project_logs(
    project_id: str,
    principal: Principal = Depends(require_any_scope("deployments:read", "projects:read")),
    db: Session = Depends(get_db),
):
    """Retrieves runtime stdout/stderr container logs for the given project."""
    service = ProjectService(db)
    logs = service.get_project_logs(project_id, principal)
    return success_response(
        data=logs.model_dump(mode="json"),
        message="Project logs retrieved successfully",
    )


# ── Standalone Deployments Router (/api/v1/deployments/{id}) ──


@deployments_router.get("/{deployment_id}")
def get_deployment_status(
    deployment_id: str,
    principal: Principal = Depends(require_any_scope("deployments:read", "projects:read")),
    db: Session = Depends(get_db),
):
    """Queries deployment execution progress, health check, and build status."""
    service = ProjectService(db)
    deployment = service.get_deployment(deployment_id, principal)
    return success_response(
        data=deployment.model_dump(mode="json"),
        message="Deployment status retrieved successfully",
    )
