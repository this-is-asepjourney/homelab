"""Pydantic schemas for automation engine rules, triggers, and execution history."""

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class AutomationRuleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    description: Optional[str] = Field(default=None, max_length=255)
    trigger_type: str = Field(..., min_length=2, max_length=64, description="Event or condition trigger type")
    condition_expression: Optional[str] = Field(default=None, max_length=255, description="Filter or condition")
    action_type: str = Field(..., min_length=2, max_length=64, description="Action to execute")
    action_target: Optional[str] = Field(default=None, max_length=128, description="Target service or resource")
    is_enabled: bool = Field(default=True)
    cooldown_seconds: int = Field(default=60, ge=0)


class AutomationRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: Optional[str] = None
    trigger_type: str
    condition_expression: Optional[str] = None
    action_type: str
    action_target: Optional[str] = None
    is_enabled: bool
    cooldown_seconds: int
    created_at: datetime
    updated_at: datetime


class AutomationEvaluateRequest(BaseModel):
    event_type: str = Field(..., description="Trigger event type (e.g. container.crash, disk.usage_high)")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Event metadata and parameters")


class AutomationEvaluateResponse(BaseModel):
    matched_rules: int
    actions_triggered: List[Dict[str, Any]]
    status: str


class AutomationLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_id: Optional[uuid.UUID] = None
    trigger_event: str
    action_executed: str
    status: str
    details: Optional[Dict[str, Any]] = None
    created_at: datetime
