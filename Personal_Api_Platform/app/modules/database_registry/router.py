"""HTTP router for database connection registry, schema inspection, and database management."""

from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import PermissionDeniedError
from app.core.permissions import Principal
from app.dependencies import get_current_principal, require_any_scope, require_scopes
from app.modules.database_registry.schemas import (
    DatabaseBackupTriggerRequest,
    DatabaseConnectionCreate,
    DatabaseConnectionRead,
    DatabaseRestoreTriggerRequest,
)
from app.modules.database_registry.service import DatabaseRegistryService
from app.schemas import success_response

# Main legacy prefix /database
router = APIRouter(prefix="/database", tags=["Database Registry & Center"])

# Canonical prefix /databases (Section 4.3)
databases_router = APIRouter(prefix="/databases", tags=["Database Center"])


# ── Existing Connection Registry Endpoints ────────────────────────────────────


@router.get("/connections")
def list_database_connections(
    principal: Principal = Depends(require_scopes("database:read")),
    db: Session = Depends(get_db),
):
    service = DatabaseRegistryService(db)
    conns = service.list_connections(principal)
    data = [DatabaseConnectionRead.model_validate(c).model_dump(mode="json") for c in conns]
    return success_response(
        data=data,
        message="Database connections retrieved successfully",
    )


@router.post("/connections", status_code=status.HTTP_201_CREATED)
def register_database_connection(
    payload: DatabaseConnectionCreate,
    principal: Principal = Depends(require_scopes("database:manage")),
    db: Session = Depends(get_db),
):
    service = DatabaseRegistryService(db)
    conn = service.register_connection(payload, principal)
    return success_response(
        data=DatabaseConnectionRead.model_validate(conn).model_dump(mode="json"),
        message="Database connection registered successfully",
    )


@router.get("/connections/{connection_id}/health")
def check_database_connection_health(
    connection_id: uuid.UUID,
    principal: Principal = Depends(require_scopes("database:read")),
    db: Session = Depends(get_db),
):
    service = DatabaseRegistryService(db)
    health = service.check_connection_health(connection_id, principal)
    return success_response(
        data=health,
        message="Database connection health checked",
    )


@router.get("/sources")
def list_approved_sources(
    principal: Principal = Depends(require_scopes("database:read")),
    db: Session = Depends(get_db),
):
    service = DatabaseRegistryService(db)
    sources = service.list_approved_sources(principal)
    return success_response(
        data=sources,
        message="Approved data sources retrieved successfully",
    )


@router.get("/sources/{source_id}/schema")
def read_approved_schema(
    source_id: uuid.UUID,
    table: Optional[str] = Query(default=None),
    principal: Principal = Depends(require_scopes("database:read")),
    db: Session = Depends(get_db),
):
    service = DatabaseRegistryService(db)
    schema_meta = service.read_approved_schema(source_id, principal, table_name=table)
    return success_response(
        data=schema_meta,
        message="Approved schema metadata retrieved successfully",
    )


@router.api_route("/query", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def reject_unrestricted_sql_query(
    principal: Principal = Depends(get_current_principal),
):
    """Explicitly block unrestricted SQL execution per Blueprint Section 1.2 & 7.3."""
    raise PermissionDeniedError(
        message="Unrestricted SQL execution endpoint is forbidden by PAP security policy.",
        code="UNRESTRICTED_SQL_FORBIDDEN",
    )


# ── Canonical Database Center Endpoints (Architecture Blueprint Section 4.3) ──


@databases_router.get("")
def list_databases_registry(
    principal: Principal = Depends(require_any_scope("database:read", "server:read")),
    db: Session = Depends(get_db),
):
    """Lists all registered databases in homelab PostgreSQL and MySQL with status."""
    service = DatabaseRegistryService(db)
    items = service.get_databases_registry(principal)
    return success_response(
        data=items,
        message="Database registry retrieved successfully",
    )


@databases_router.get("/{database_id}/health")
def get_database_health(
    database_id: str,
    principal: Principal = Depends(require_any_scope("database:read", "server:read")),
    db: Session = Depends(get_db),
):
    """Returns connectivity and operational health for a specific database."""
    service = DatabaseRegistryService(db)
    health = service.get_database_health(database_id, principal)
    return success_response(
        data=health,
        message=f"Database '{database_id}' health verified",
    )


@databases_router.get("/{database_id}/metrics")
def get_database_metrics(
    database_id: str,
    principal: Principal = Depends(require_any_scope("database:read", "metrics:read")),
    db: Session = Depends(get_db),
):
    """Returns database metrics including connection count, table count, and disk size."""
    service = DatabaseRegistryService(db)
    metrics = service.get_database_metrics(database_id, principal)
    return success_response(
        data=metrics,
        message=f"Database '{database_id}' metrics retrieved",
    )


@databases_router.post("/{database_id}/backup", status_code=status.HTTP_201_CREATED)
def trigger_database_backup(
    database_id: str,
    payload: Optional[DatabaseBackupTriggerRequest] = None,
    principal: Principal = Depends(require_any_scope("database:backup", "database:manage")),
    db: Session = Depends(get_db),
):
    """Triggers an on-demand backup for the specified database."""
    service = DatabaseRegistryService(db)
    req = payload or DatabaseBackupTriggerRequest()
    result = service.trigger_database_backup(database_id, req, principal)
    return success_response(
        data=result,
        message=f"Backup for database '{database_id}' created successfully",
    )


@databases_router.get("/{database_id}/backups")
def list_database_backups(
    database_id: str,
    principal: Principal = Depends(require_any_scope("database:backup", "database:read")),
    db: Session = Depends(get_db),
):
    """Lists available backup archives for the specified database."""
    service = DatabaseRegistryService(db)
    backups = service.list_database_backups(database_id, principal)
    return success_response(
        data=backups,
        message=f"Backups for database '{database_id}' retrieved",
    )


@databases_router.post("/{database_id}/restore")
def restore_database(
    database_id: str,
    payload: DatabaseRestoreTriggerRequest,
    principal: Principal = Depends(require_any_scope("database:backup", "database:manage")),
    db: Session = Depends(get_db),
):
    """Restores database from selected backup archive."""
    service = DatabaseRegistryService(db)
    result = service.restore_database(database_id, payload, principal)
    return success_response(
        data=result,
        message=result["message"],
    )
