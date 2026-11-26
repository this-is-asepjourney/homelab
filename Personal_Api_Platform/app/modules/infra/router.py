"""HTTP router for unified homelab infrastructure management (PAP Control Plane)."""

from typing import Any, Dict, List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import Principal
from app.dependencies import require_any_scope, require_scopes
from app.modules.infra.schemas import InfraOverviewResponse
from app.modules.infra.service import InfraService
from app.schemas import success_response

router = APIRouter(prefix="/infra", tags=["Infrastructure Hub"])


@router.get("/overview")
def get_infra_overview(
    refresh: bool = False,
    principal: Principal = Depends(require_any_scope("infra:read", "server:read", "metrics:read")),
    db: Session = Depends(get_db),
):
    """Unified Server Management API per Architecture Blueprint Section 4.1.

    Combines server telemetry, Docker container health, databases, and core homelab services.
    """
    service = InfraService(db)
    overview = service.get_overview(force_refresh=refresh)
    return success_response(
        data=overview,
        message="Infrastructure overview retrieved successfully",
    )


@router.get("/central-pulse")
def get_infra_central_pulse(
    principal: Principal = Depends(require_any_scope("infra:read", "server:read", "metrics:read")),
    db: Session = Depends(get_db),
):
    """Returns sub-millisecond Central Gateway responsiveness, service health, PSM sync & backup vault pulse."""
    service = InfraService(db)
    pulse = service.get_central_pulse()
    return success_response(
        data=pulse,
        message="Central platform pulse retrieved successfully",
    )


@router.get("/servers")
def get_infra_servers(
    principal: Principal = Depends(require_any_scope("infra:read", "server:read")),
    db: Session = Depends(get_db),
):
    """Lists registered server nodes in the homelab with live metrics and network topology."""
    service = InfraService(db)
    servers = service.list_servers()
    return success_response(
        data=servers,
        message="Server nodes retrieved successfully",
    )


@router.get("/services")
def get_infra_services(
    principal: Principal = Depends(require_any_scope("infra:read", "server:read")),
    db: Session = Depends(get_db),
):
    """Lists all registered core and application services running in the homelab."""
    service = InfraService(db)
    services = service.list_services()
    return success_response(
        data=services,
        message="Infrastructure services retrieved successfully",
    )
