import logging
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy import func
from sqlalchemy.orm import Session
from apps.api.app.models.entities import Job, Deployment, DeploymentLog
from apps.api.app.websocket.manager import ws_manager
from apps.api.app.core.config import settings
from apps.api.app.services.pap_client import (
    extract_host_port,
    resolve_effective_domain,
    sync_domain_routing,
    sync_psm_to_pap,
)

logger = logging.getLogger(__name__)
_PORT_PAIR_RE = re.compile(r"^\s*\d{1,5}\s*:\s*\d{1,5}\s*$")


async def create_and_dispatch_job(
    db: Session,
    server_id: str,
    job_type: str,
    payload: Dict[str, Any],
    deployment_id: Optional[str] = None,
    priority: int = 10
) -> Job:
    job = Job(
        server_id=server_id,
        deployment_id=deployment_id,
        job_type=job_type,
        payload=payload,
        status="queued",
        priority=priority,
        attempts=0
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Attempt to dispatch immediately via WebSocket to active agent
    notification = {
        "event": "job.dispatch",
        "job_id": job.id,
        "job_type": job.job_type,
        "server_id": server_id,
        "payload": job.payload
    }
    sent = await ws_manager.send_to_agent(server_id, notification)
    if sent:
        job.status = "running"
        job.locked_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)

    return job


def claim_next_job(db: Session, server_id: str) -> Optional[Job]:
    """Claim pending job from queue with row locking."""
    query = db.query(Job).filter(
        Job.server_id == server_id,
        Job.status == "queued"
    ).order_by(Job.priority.desc(), Job.created_at.asc())

    # Skip locked for PostgreSQL
    if not settings.DATABASE_URL.startswith("sqlite"):
        query = query.with_for_update(skip_locked=True)

    job = query.first()
    if job:
        job.status = "running"
        job.attempts += 1
        job.locked_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job)
    return job


def sync_effective_port_mappings(
    db: Session,
    deployment: Optional[Deployment],
    output: Optional[Dict[str, Any]],
) -> bool:
    """
    Persist agent-reported effective port mappings back to `Project.port_mappings`
    and re-sync Nginx Ingress + CoreDNS routing when runtime port recovery remapped ports.
    """
    if not deployment or not deployment.project or not isinstance(output, dict):
        return False

    eff_ports = str(output.get("effective_port_mappings") or "").strip()
    if not eff_ports:
        return False

    valid_pairs = [part.strip() for part in eff_ports.split(",") if _PORT_PAIR_RE.match(part)]
    if not valid_pairs:
        return False
    normalized_eff = ", ".join(valid_pairs)

    project = deployment.project
    old_ports = (project.port_mappings or "").strip()
    if normalized_eff == old_ports:
        return False

    project.port_mappings = normalized_eff
    max_seq = (
        db.query(func.max(DeploymentLog.sequence))
        .filter(DeploymentLog.deployment_id == deployment.id)
        .scalar()
        or 0
    )
    db.add(
        DeploymentLog(
            deployment_id=deployment.id,
            sequence=int(max_seq) + 1,
            level="info",
            message=(
                f"[Auto-Port] Port project disinkronkan otomatis dari "
                f"'{old_ports or '-'}' ke '{normalized_eff}' (Nginx Ingress & DNS ikut diperbarui)."
            ),
        )
    )

    try:
        eff_domain = resolve_effective_domain(project.domain, project.name)
        target_hp = extract_host_port(normalized_eff)
        sync_domain_routing(
            domain=eff_domain,
            target_port=target_hp,
            project_name=project.name,
            maintenance_mode=bool(getattr(project, "maintenance_mode", False)),
            maintenance_title=getattr(project, "maintenance_title", None),
            maintenance_message=getattr(project, "maintenance_message", None),
            maintenance_eta=getattr(project, "maintenance_eta", None),
        )
    except Exception:
        pass

    try:
        sync_psm_to_pap(db)
    except Exception:
        pass

    return True


def complete_job(
    db: Session,
    job_id: str,
    status: str,
    output: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    duration_seconds: int = 0
) -> Optional[Job]:
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return None

    job.status = "success" if status == "success" else "failed"
    job.finished_at = datetime.now(timezone.utc)

    # If linked to a deployment, update deployment status
    if job.deployment_id:
        deployment = db.query(Deployment).filter(Deployment.id == job.deployment_id).first()
        if deployment:
            deployment.status = "success" if status == "success" else "failed"
            deployment.finished_at = datetime.now(timezone.utc)
            deployment.duration_seconds = duration_seconds

            # If deployment succeeded and an image tag was created, update project status
            sync_effective_port_mappings(db, deployment, output) if status == "success" else None
            if status == "success" and job.payload.get("image_tag"):
                deployment.image_tag = job.payload.get("image_tag")
                if deployment.project:
                    deployment.project.status = "active"
            elif status == "success" and deployment.project:
                deployment.project.status = "active"

    db.commit()
    db.refresh(job)
    return job
