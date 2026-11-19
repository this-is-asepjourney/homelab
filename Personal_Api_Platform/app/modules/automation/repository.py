"""Repository for automation rules and execution logs."""

from typing import List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.automation.models import AutomationExecutionLog, AutomationRule


class AutomationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, rule_id: uuid.UUID) -> Optional[AutomationRule]:
        return self.db.get(AutomationRule, rule_id)

    def get_by_name(self, name: str) -> Optional[AutomationRule]:
        stmt = select(AutomationRule).where(AutomationRule.name == name.strip())
        return self.db.scalar(stmt)

    def list_rules(self, trigger_type: Optional[str] = None) -> List[AutomationRule]:
        stmt = select(AutomationRule)
        if trigger_type:
            stmt = stmt.where(AutomationRule.trigger_type == trigger_type)
        stmt = stmt.order_by(AutomationRule.created_at.asc())
        return list(self.db.scalars(stmt).all())

    def create_rule(self, rule: AutomationRule) -> AutomationRule:
        self.db.add(rule)
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def save_rule(self, rule: AutomationRule) -> AutomationRule:
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def delete_rule(self, rule: AutomationRule) -> None:
        self.db.delete(rule)
        self.db.commit()

    def log_execution(self, log: AutomationExecutionLog) -> AutomationExecutionLog:
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log

    def list_logs(self, limit: int = 50) -> List[AutomationExecutionLog]:
        stmt = (
            select(AutomationExecutionLog)
            .order_by(AutomationExecutionLog.created_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())
