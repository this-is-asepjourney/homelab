"""HTTP router for event-driven automation rules and recovery policies."""

from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import Principal
from app.dependencies import require_any_scope, require_scopes
from app.modules.automation.schemas import (
    AutomationEvaluateRequest,
    AutomationEvaluateResponse,
    AutomationLogRead,
    AutomationRuleCreate,
    AutomationRuleRead,
)
from app.modules.automation.service import AutomationService
from app.schemas import success_response

router = APIRouter(prefix="/automations", tags=["Automation Center"])


@router.get("/rules")
def list_automation_rules(
    trigger_type: Optional[str] = Query(default=None),
    principal: Principal = Depends(require_any_scope("automations:read", "server:read")),
    db: Session = Depends(get_db),
):
    """Lists configured event-driven automation rules."""
    service = AutomationService(db)
    rules = service.list_rules(trigger_type=trigger_type)
    data = [AutomationRuleRead.model_validate(r).model_dump(mode="json") for r in rules]
    return success_response(
        data=data,
        message="Automation rules retrieved successfully",
    )


@router.post("/rules", status_code=status.HTTP_201_CREATED)
def create_automation_rule(
    payload: AutomationRuleCreate,
    principal: Principal = Depends(require_scopes("automations:manage")),
    db: Session = Depends(get_db),
):
    """Registers a new event trigger and action automation rule."""
    service = AutomationService(db)
    rule = service.create_rule(payload, principal)
    return success_response(
        data=AutomationRuleRead.model_validate(rule).model_dump(mode="json"),
        message="Automation rule created successfully",
    )


@router.get("/rules/{rule_id}")
def get_automation_rule(
    rule_id: uuid.UUID,
    principal: Principal = Depends(require_any_scope("automations:read", "server:read")),
    db: Session = Depends(get_db),
):
    """Retrieves specific automation rule configuration."""
    service = AutomationService(db)
    rule = service.get_rule(rule_id)
    return success_response(
        data=AutomationRuleRead.model_validate(rule).model_dump(mode="json"),
        message="Automation rule details retrieved successfully",
    )


@router.delete("/rules/{rule_id}")
def delete_automation_rule(
    rule_id: uuid.UUID,
    principal: Principal = Depends(require_scopes("automations:manage")),
    db: Session = Depends(get_db),
):
    """Deletes an automation rule."""
    service = AutomationService(db)
    service.delete_rule(rule_id, principal)
    return success_response(
        data={"rule_id": str(rule_id)},
        message="Automation rule deleted successfully",
    )


@router.post("/evaluate")
def evaluate_automation_event(
    payload: AutomationEvaluateRequest,
    principal: Principal = Depends(require_any_scope("automations:manage", "server:read", "iot:write")),
    db: Session = Depends(get_db),
):
    """Evaluates an incoming event against automation policies and executes actions."""
    service = AutomationService(db)
    result = service.evaluate_event(payload, principal)
    return success_response(
        data=result.model_dump(mode="json"),
        message=f"Evaluated event '{payload.event_type}': {result.matched_rules} actions triggered",
    )


@router.get("/history")
def get_automation_history(
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(require_any_scope("automations:read", "audit:read")),
    db: Session = Depends(get_db),
):
    """Lists audit trail of automated actions and recoveries executed."""
    service = AutomationService(db)
    logs = service.get_history(limit=limit)
    data = [AutomationLogRead.model_validate(l).model_dump(mode="json") for l in logs]
    return success_response(
        data=data,
        message="Automation execution history retrieved successfully",
    )
