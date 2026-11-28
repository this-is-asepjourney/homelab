"""API endpoints for Real-time Traffic Streaming and Topology Telemetry."""

import asyncio
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.modules.traffic.broadcaster import traffic_broadcaster

router = APIRouter(prefix="/traffic", tags=["Topology & Traffic Streaming"])


class SimulateRequest(BaseModel):
    action: str = "nfc_tap"  # "nfc_tap", "telemetry", "nfc_sync", "dns_ingress", "stock", "psm_deploy", "minio_s3", "health_check", "backup"
    client_ip: str = "192.168.25.104"


@router.websocket("/ws")
async def websocket_traffic_endpoint(websocket: WebSocket):
    """Realtime WebSocket endpoint streaming all live API calls."""
    await traffic_broadcaster.connect_ws(websocket)
    try:
        # Keep connection open; client can send pings
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        traffic_broadcaster.disconnect_ws(websocket)
    except Exception:
        traffic_broadcaster.disconnect_ws(websocket)


@router.get("/stream")
async def sse_traffic_stream():
    """Server-Sent Events (SSE) fallback stream for live API traffic."""
    return StreamingResponse(
        traffic_broadcaster.subscribe_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/recent")
async def get_recent_traffic() -> Dict[str, Any]:
    """Retrieve recent API traffic history for topology hydration."""
    return {
        "status": "success",
        "data": traffic_broadcaster.get_recent(),
        "stats": traffic_broadcaster.get_stats(),
    }


@router.get("/stats")
async def get_traffic_stats() -> Dict[str, Any]:
    """Retrieve real-time traffic statistics."""
    return {
        "status": "success",
        "data": traffic_broadcaster.get_stats(),
    }


class TrafficEventPayload(BaseModel):
    method: str = "GET"
    path: str = "/"
    status: int = 200
    duration_ms: float = 1.0
    client_ip: str = "127.0.0.1"
    source: Optional[str] = None
    target: str = "core"
    sub_target: Optional[str] = None
    tag: Optional[str] = None
    color: Optional[str] = None
    detail: Optional[str] = None


@router.post("/event")
async def ingest_traffic_event(payload: TrafficEventPayload) -> Dict[str, Any]:
    """Ingests a traffic event into the broadcaster from external workers or decoupled background tasks."""
    traffic_broadcaster.record_and_broadcast(
        method=payload.method,
        path=payload.path,
        status=payload.status,
        duration_ms=payload.duration_ms,
        client_ip=payload.client_ip,
        source=payload.source,
        target=payload.target,
        sub_target=payload.sub_target,
        tag=payload.tag,
        color=payload.color,
        detail=payload.detail,
    )
    return {"status": "ok"}


@router.post("/simulate")
async def simulate_api_action(payload: SimulateRequest) -> Dict[str, Any]:
    """Triggers realistic multi-hop event through the central core gateway matching current 2026 homelab flow."""
    action = payload.action
    settings = get_settings()
    lan_ip = getattr(settings, "SERVER_LAN_IP", "192.168.25.105")

    if action == "nfc_tap":
        traffic_broadcaster.record_and_broadcast(
            method="POST",
            path="/api/nfc/tap",
            status=200,
            duration_ms=2.1,
            client_ip=payload.client_ip or "192.168.25.104",
            source="esp32",
            target="core",
            sub_target="postgres",
            tag="NFC_TAP",
            color="#34d399",
            detail="ESP32 (192.168.25.104) -> POST /api/nfc/tap [UID: 058E208F87A200]",
        )
        asyncio.create_task(_delay_broadcast(0.32, "core", "postgres", "PG_AUTH", "#60a5fa", "Core -> PostgreSQL eternal_iot :5432 [Verify nfc_users & log tap]"))
        asyncio.create_task(_delay_broadcast(0.65, "core", "esp32", "UNLOCKED", "#22d3ee", "Core -> ESP32 [Relay GPIO26 HIGH, Buzzer OK]"))
        asyncio.create_task(_delay_broadcast(0.90, "core", "grafana", "METRICS", "#f43f5e", "Core /metrics -> Grafana IoT Dashboard :3000"))

    elif action == "nfc_sync":
        traffic_broadcaster.record_and_broadcast(
            method="POST",
            path="/api/nfc/users",
            status=201,
            duration_ms=3.4,
            client_ip=f"{lan_ip}:80",
            source="apps",
            target="core",
            sub_target="postgres",
            tag="USER_REG",
            color="#34d399",
            detail="Web App -> POST /api/nfc/users [Register Operator NFC UID]",
        )
        asyncio.create_task(_delay_broadcast(0.32, "core", "postgres", "PG_WRITE", "#60a5fa", "Core -> PostgreSQL eternal_iot [INSERT INTO nfc_users]"))
        asyncio.create_task(_delay_broadcast(0.65, "core", "esp32", "NVS_PUSH", "#38bdf8", "Core -> ESP32 [Sync whitelist to Flash NVS]"))

    elif action == "telemetry":
        traffic_broadcaster.record_and_broadcast(
            method="MQTT_PUB",
            path="/SysCtrl",
            status=200,
            duration_ms=1.4,
            client_ip=payload.client_ip or "192.168.25.104",
            source="esp32",
            target="mqtt",
            sub_target="core",
            tag="MQTT_PUB",
            color="#38bdf8",
            detail="ESP32 -> Mosquitto :1883 [/SysCtrl {temp: 36.4°C, hum: 37%, rssi: -55dBm}]",
        )
        asyncio.create_task(_delay_broadcast(0.30, "mqtt", "core", "MQTT_SUB", "#06b6d4", "Mosquitto :1883 -> PAP Core MqttPgConsumer (In-Process Worker)"))
        asyncio.create_task(_delay_broadcast(0.60, "core", "postgres", "ETERNAL_PG", "#60a5fa", "Core -> PostgreSQL 16 :5432 [db=eternal_iot]"))
        asyncio.create_task(_delay_broadcast(0.75, "mqtt", "prometheus", "TELEGRAF", "#f59e0b", "Mosquitto -> Telegraf :9273 -> Prometheus :9090"))
        asyncio.create_task(_delay_broadcast(1.00, "prometheus", "grafana", "PROM_QL", "#f43f5e", "Grafana :3000 <- PromQL + PostgreSQL eternal_iot"))

    elif action == "dns_ingress":
        traffic_broadcaster.record_and_broadcast(
            method="DNS_HTTP",
            path="*.homelab",
            status=200,
            duration_ms=0.8,
            client_ip=payload.client_ip or lan_ip,
            source="router",
            target="core",
            sub_target="psm",
            tag="DNS_INGRESS",
            color="#38bdf8",
            detail=f"CoreDNS (:53) + Nginx Ingress (:80) -> Resolve *.homelab / *.tailscale.homelab ({lan_ip})",
        )
        asyncio.create_task(_delay_broadcast(0.30, "router", "psm", "VHOST_PSM", "#a78bfa", "Ingress :80 -> ProxyPass psm.homelab -> psm-web:3005"))
        asyncio.create_task(_delay_broadcast(0.55, "router", "grafana", "VHOST_GRAF", "#f43f5e", "Ingress :80 -> ProxyPass grafana.homelab -> grafana:3000"))
        asyncio.create_task(_delay_broadcast(0.75, "router", "apps", "VHOST_APP", "#34d399", "Ingress :80 -> ProxyPass deployed *.homelab apps / 503 Maintenance"))

    elif action == "stock":
        traffic_broadcaster.record_and_broadcast(
            method="POST",
            path="/api/v1/stock/transactions",
            status=201,
            duration_ms=3.1,
            client_ip=payload.client_ip or lan_ip,
            source="apps",
            target="core",
            sub_target="mysql",
            tag="STOCK_POS",
            color="#fbbf24",
            detail="Stock UMKM / POS -> POST /api/v1/stock/transactions [Order #TRX-8821]",
        )
        asyncio.create_task(_delay_broadcast(0.35, "core", "mysql", "MYSQL_WRITE", "#fbbf24", "Core -> MySQL 8.4 :3306 [stock_umkm / fadb]"))
        asyncio.create_task(_delay_broadcast(0.65, "core", "minio", "S3_ASSET", "#22d3ee", "App / Core -> MinIO Silo S3 :9000 [bucket=stock-umkm]"))

    elif action == "psm_deploy":
        traffic_broadcaster.record_and_broadcast(
            method="POST",
            path="/api/v1/deployments/git",
            status=200,
            duration_ms=4.8,
            client_ip=payload.client_ip or f"{lan_ip}:3005",
            source="psm",
            target="core",
            sub_target="postgres",
            tag="PSM_DEPLOY",
            color="#a78bfa",
            detail="PSM Console (:3005/:8001) + psm-agent -> Build & Deploy Project Container",
        )
        asyncio.create_task(_delay_broadcast(0.32, "core", "postgres", "PG_SYNC", "#60a5fa", "Core -> PostgreSQL psm :5432 [Update project & deployment state]"))
        asyncio.create_task(_delay_broadcast(0.60, "core", "router", "VHOST_REG", "#38bdf8", "Core DNS -> Register *.homelab Nginx VHost + CoreDNS reload"))
        asyncio.create_task(_delay_broadcast(0.85, "core", "minio", "S3_WIRE", "#22d3ee", "psm-agent -> Auto-wire MinIO S3 bucket & credentials"))

    elif action == "minio_s3":
        traffic_broadcaster.record_and_broadcast(
            method="PUT",
            path="/api/v1/integrations/minio/health",
            status=200,
            duration_ms=1.9,
            client_ip=payload.client_ip or lan_ip,
            source="apps",
            target="core",
            sub_target="minio",
            tag="MINIO_S3",
            color="#22d3ee",
            detail="Homelab Apps -> MinIO Silo S3 (:9000/:9002) & Redis (:6379) Cache",
        )
        asyncio.create_task(_delay_broadcast(0.35, "core", "minio", "S3_BUCKET", "#22d3ee", "MinIO S3 :9000 [images, stock-umkm, homelab-storage, backups]"))

    elif action == "health_check":
        traffic_broadcaster.record_and_broadcast(
            method="GET",
            path="/metrics",
            status=200,
            duration_ms=1.1,
            client_ip="prometheus",
            source="prometheus",
            target="core",
            sub_target="grafana",
            tag="HEALTH_SCRAPE",
            color="#f59e0b",
            detail="Prometheus (:9090) <- Scrape /metrics (pap-api, psm-api, node-exporter, cadvisor, telegraf)",
        )
        asyncio.create_task(_delay_broadcast(0.35, "prometheus", "grafana", "GRAF_DASH", "#f43f5e", "Grafana (:3000) <- Query Prometheus + Uptime Kuma (:3001) probes"))
        asyncio.create_task(_delay_broadcast(0.60, "router", "core", "HEALTH_OK", "#34d399", "Nginx :80 -> GET /health & /api/v1/system/central-pulse"))

    elif action == "backup":
        traffic_broadcaster.record_and_broadcast(
            method="POST",
            path="/api/v1/backups/create",
            status=200,
            duration_ms=6.2,
            client_ip=payload.client_ip or lan_ip,
            source="psm",
            target="core",
            sub_target="postgres",
            tag="BACKUP_ENG",
            color="#f43f5e",
            detail="Disaster Recovery Engine -> Snapshotting DB, Configs & MinIO S3 Vault",
        )
        asyncio.create_task(_delay_broadcast(0.30, "core", "postgres", "PG_DUMP", "#60a5fa", "Core -> PostgreSQL :5432 [pg_dump pap_core, psm, eternal_iot, nextcloud]"))
        asyncio.create_task(_delay_broadcast(0.55, "core", "mysql", "MYSQL_DUMP", "#fbbf24", "Core -> MySQL :3306 [mysqldump fadb, stock_umkm]"))
        asyncio.create_task(_delay_broadcast(0.85, "core", "minio", "S3_VAULT", "#22d3ee", "Core -> MinIO S3 :9000 [Upload SHA-256 .tar.gz to s3://backups/pap/]"))

    return {
        "status": "success",
        "action": action,
        "message": f"Simulated action '{action}' dispatched through core gateway",
    }


async def _delay_broadcast(delay: float, source: str, target: str, tag: str, color: str, detail: str):
    await asyncio.sleep(delay)
    traffic_broadcaster.record_and_broadcast(
        method="EVENT",
        path=f"/internal/{tag.lower()}",
        status=200,
        duration_ms=round(delay * 10, 1),
        source=source,
        target=target,
        tag=tag,
        color=color,
        detail=detail,
    )

