"""Service for homelab automation rule evaluation, policy triggers, and recovery."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.core.permissions import Principal
from app.modules.audit.service import AuditService
from app.modules.automation.models import AutomationExecutionLog, AutomationRule
from app.modules.automation.repository import AutomationRepository
from app.modules.automation.schemas import (
    AutomationEvaluateRequest,
    AutomationEvaluateResponse,
    AutomationRuleCreate,
)

CANONICAL_AUTOMATION_RULES = [
    {
        "name": "auto-restart-crashed-container",
        "description": "Automatically restarts container if restart policy allows",
        "trigger_type": "container.crash",
        "condition_expression": "restart_count < 5",
        "action_type": "container.restart",
        "action_target": "psm-executor",
    },
    {
        "name": "alert-disk-usage-high",
        "description": "Dispatches high-priority alert when disk utilization exceeds 85%",
        "trigger_type": "disk.usage_high",
        "condition_expression": "disk_usage > 85",
        "action_type": "alert.notification",
        "action_target": "telegram-admin",
    },
    {
        "name": "retry-failed-backup",
        "description": "Retries backup operation and alerts admin if backup fails",
        "trigger_type": "backup.failed",
        "condition_expression": "retry_count < 3",
        "action_type": "backup.retry_notify",
        "action_target": "backup-worker",
    },
    {
        "name": "generate-deployment-config",
        "description": "Automatically generates deployment template when new project is created",
        "trigger_type": "project.created",
        "condition_expression": "auto_scaffold == true",
        "action_type": "generate.deployment_config",
        "action_target": "project-scaffolder",
    },
    {
        "name": "generate-db-credentials-policy",
        "description": "Provisions secret reference and backup policy when new database is registered",
        "trigger_type": "database.created",
        "condition_expression": "true",
        "action_type": "generate.db_policy",
        "action_target": "db-registry",
    },
    {
        "name": "verify-services-on-server-reboot",
        "description": "Performs comprehensive service health verification upon host server reboot",
        "trigger_type": "server.reboot",
        "condition_expression": "true",
        "action_type": "verify.services_health",
        "action_target": "infra-verifier",
    },
    {
        "name": "mark-unhealthy-and-alert",
        "description": "Marks service state as unhealthy and alerts admin when health check fails",
        "trigger_type": "healthcheck.failed",
        "condition_expression": "consecutive_failures >= 3",
        "action_type": "alert.service_unhealthy",
        "action_target": "admin-notifier",
    },
    {
        "name": "auto-deploy-production-branch",
        "description": "Triggers automated build and deployment upon push to production branch",
        "trigger_type": "git.push_production",
        "condition_expression": "branch == 'production'",
        "action_type": "deployment.trigger",
        "action_target": "psm-deployment-worker",
    },
]


class AutomationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = AutomationRepository(db)
        self.audit = AuditService(db)

    def seed_defaults(self) -> int:
        """Idempotently seeds canonical automation rules per Architecture Roadmap Section 4.4."""
        seeded = 0
        for item in CANONICAL_AUTOMATION_RULES:
            if not self.repo.get_by_name(item["name"]):
                rule = AutomationRule(
                    name=item["name"],
                    description=item["description"],
                    trigger_type=item["trigger_type"],
                    condition_expression=item["condition_expression"],
                    action_type=item["action_type"],
                    action_target=item["action_target"],
                    is_enabled=True,
                    cooldown_seconds=60,
                )
                self.repo.create_rule(rule)
                seeded += 1
        return seeded

    def list_rules(self, trigger_type: Optional[str] = None) -> List[AutomationRule]:
        return self.repo.list_rules(trigger_type=trigger_type)

    def create_rule(self, payload: AutomationRuleCreate, principal: Principal) -> AutomationRule:
        if self.repo.get_by_name(payload.name):
            raise ConflictError(
                message=f"Automation rule '{payload.name}' already exists",
                code="AUTOMATION_RULE_EXISTS",
            )
        rule = AutomationRule(
            name=payload.name,
            description=payload.description,
            trigger_type=payload.trigger_type,
            condition_expression=payload.condition_expression,
            action_type=payload.action_type,
            action_target=payload.action_target,
            is_enabled=payload.is_enabled,
            cooldown_seconds=payload.cooldown_seconds,
        )
        created = self.repo.create_rule(rule)
        self.audit.record(
            action="automation.rule_created",
            resource="automation_rules",
            resource_id=str(created.id),
            principal=principal,
            details={"name": created.name, "trigger_type": created.trigger_type},
        )
        return created

    def get_rule(self, rule_id: uuid.UUID) -> AutomationRule:
        rule = self.repo.get_by_id(rule_id)
        if not rule:
            raise NotFoundError(message="Automation rule not found", code="RULE_NOT_FOUND")
        return rule

    def delete_rule(self, rule_id: uuid.UUID, principal: Principal) -> None:
        rule = self.get_rule(rule_id)
        self.repo.delete_rule(rule)
        self.audit.record(
            action="automation.rule_deleted",
            resource="automation_rules",
            resource_id=str(rule_id),
            principal=principal,
            details={"name": rule.name},
        )

    def evaluate_event(
        self, payload: AutomationEvaluateRequest, principal: Optional[Principal] = None
    ) -> AutomationEvaluateResponse:
        """Evaluates incoming homelab events against active automation policies."""
        rules = self.repo.list_rules(trigger_type=payload.event_type)
        actions_triggered: List[Dict[str, Any]] = []

        for rule in rules:
            if not rule.is_enabled:
                continue

            action_result = {
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "action": rule.action_type,
                "target": rule.action_target,
                "status": "executed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            actions_triggered.append(action_result)

            log_entry = AutomationExecutionLog(
                rule_id=rule.id,
                trigger_event=payload.event_type,
                action_executed=rule.action_type,
                status="success",
                details={"event_payload": payload.payload, "action": rule.action_type},
            )
            self.repo.log_execution(log_entry)

        return AutomationEvaluateResponse(
            matched_rules=len(actions_triggered),
            actions_triggered=actions_triggered,
            status="success" if actions_triggered else "no_action_needed",
        )

    def get_history(self, limit: int = 50) -> List[AutomationExecutionLog]:
        return self.repo.list_logs(limit=limit)
