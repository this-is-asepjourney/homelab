"""HTTP router for liveness, readiness, system info, version, and Prometheus metrics scrape."""

import os
from datetime import datetime, timezone
import time
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.integrations.prometheus import render_prometheus_metrics
from app.modules.server.service import ServerService
from app.schemas import success_response

router = APIRouter(tags=["System"])

_latest_worker_heartbeat: Dict[str, Any] = {}


class WorkerHeartbeatPayload(BaseModel):
    worker_id: str = "pap-worker-01"
    uptime_sec: int = 0
    mqtt_connected: bool = True
    mqtt_messages_processed: int = 0
    last_mqtt_topic: Optional[str] = None
    last_mqtt_at: Optional[str] = None
    auto_backup_enabled: bool = True
    last_backup_at: Optional[str] = None
    active_monitors: int = 0
    memory_rss_mb: float = 0.0
    devices_online: int = 0
    devices_monitored: int = 0
    databases_health: Optional[Dict[str, str]] = None
    last_maintenance_at: Optional[str] = None

    class Config:
        extra = "allow"


@router.get("/health")
def health_check():
    return success_response(
        data={
            "status": "healthy",
            "service": "personal-api-platform",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        message="PAP service is alive",
    )


@router.get("/ready")
def readiness_check(db: Session = Depends(get_db)):
    service = ServerService(db)
    return success_response(
        data=service.get_readiness(),
        message="PAP service is ready",
    )


@router.get("/system/info")
def system_info(db: Session = Depends(get_db)):
    service = ServerService(db)
    return success_response(
        data=service.get_system_info(),
        message="System information retrieved successfully",
    )


@router.get("/system/version")
def system_version(db: Session = Depends(get_db)):
    service = ServerService(db)
    return success_response(
        data=service.get_version(),
        message="API version retrieved successfully",
    )


@router.get("/metrics", include_in_schema=False)
def prometheus_exporter_endpoint():
    """Exposes bounded-label PAP & IoT telemetry metrics for Prometheus scraping."""
    payload = render_prometheus_metrics()
    return Response(content=payload, media_type="text/plain; version=0.0.4; charset=utf-8")


@router.post("/system/worker-heartbeat")
def receive_worker_heartbeat(payload: WorkerHeartbeatPayload):
    """Heartbeat receiver called periodically by the decoupled pap-worker background daemon."""
    global _latest_worker_heartbeat
    data = payload.dict()
    data["received_at"] = datetime.now(timezone.utc).isoformat()
    data["received_timestamp"] = time.time()
    _latest_worker_heartbeat = data
    return {"status": "ok", "message": "Heartbeat recorded"}


@router.get("/system/worker-status")
def get_worker_status():
    """Returns the operational status of the background engine (external pap-worker or in-process MQTT->PG consumer)."""
    global _latest_worker_heartbeat
    if not _latest_worker_heartbeat:
        enable_mqtt = os.getenv("ENABLE_MQTT_CONSUMER", "true").lower() in ("true", "1", "yes")
        try:
            from app.services.mqtt_influx_consumer import mqtt_influx_consumer
            if mqtt_influx_consumer.is_running or enable_mqtt:
                status_str = "in_process_active" if mqtt_influx_consumer.is_running else "standby"
                return success_response(
                    data={
                        "status": status_str,
                        "healthy": bool(mqtt_influx_consumer.is_running),
                        "role": "in_process_consumer",
                        "details": {
                            "mqtt_connected": bool(mqtt_influx_consumer.connected),
                            "mqtt_messages_processed": int(mqtt_influx_consumer.messages_count),
                            "last_mqtt_topic": mqtt_influx_consumer.last_topic,
                            "last_mqtt_at": mqtt_influx_consumer.last_message_at,
                            "devices_tracked": len(mqtt_influx_consumer.devices_last_seen),
                            "target_db": "eternal_iot",
                        },
                    },
                    message="In-process MQTT->PostgreSQL consumer status retrieved successfully",
                )
        except Exception:
            pass
        return success_response(
            data={
                "status": "not_registered",
                "healthy": False,
                "role": "monolith_fallback",
                "detail": "No external pap-worker heartbeat registered. Falling back to internal mode.",
            },
            message="Worker status",
        )

    diff = time.time() - _latest_worker_heartbeat.get("received_timestamp", 0)
    is_healthy = diff < 60.0
    status_str = "healthy" if is_healthy else ("stale" if diff < 120.0 else "offline")
    return success_response(
        data={
            "status": status_str,
            "healthy": is_healthy,
            "seconds_since_heartbeat": round(diff, 1),
            "details": _latest_worker_heartbeat,
        },
        message="Worker status retrieved successfully",
    )


@router.get("/system/central-pulse")
def get_public_central_pulse(refresh: bool = False, db: Session = Depends(get_db)):
    """Lightweight cached Central Hub pulse for PAP Mission Control UI and PSM Dashboard."""
    from app.modules.infra.service import InfraService
    pulse = InfraService(db).get_central_pulse(force_refresh=refresh)
    return success_response(
        data=pulse,
        message="Central Hub pulse retrieved successfully",
    )


@router.get("/system/services")
def get_public_services_catalog(refresh: bool = False, db: Session = Depends(get_db)):
    """Public cached service catalog with LAN, Tailscale, and .homelab URLs for Mission Control UI."""
    from app.core.hub_cache import hub_cache
    from app.modules.infra.service import InfraService
    if refresh:
        hub_cache.invalidate("infra:")
    services = InfraService(db).list_services()
    return success_response(
        data=services,
        message="Homelab service catalog retrieved successfully",
    )

