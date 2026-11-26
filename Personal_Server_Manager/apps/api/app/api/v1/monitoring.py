import json
import os
import re
import socket
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from apps.api.app.database.session import get_db
from apps.api.app.core.config import settings
from apps.api.app.core.dependencies import get_current_user, require_role
from apps.api.app.models.entities import (
    Server,
    Container,
    Volume,
    DockerNetwork,
    ServerMetric,
    Deployment,
    Project,
    User,
)
from apps.api.app.api.v1.servers import compute_server_status
from apps.api.app.api.v1.agents import LATEST_TELEMETRY, NET_HISTORY_BUFFER
from apps.api.app.services.pap_client import extract_host_port, resolve_effective_domain

router = APIRouter(prefix="/monitoring", tags=["Monitoring Center"])

PROMETHEUS_CONFIG_FILE = os.path.join(settings.STORAGE_BASE_DIR, "prometheus_settings.json")


class PrometheusConfigUpdate(BaseModel):
    prometheus_url: str


class NetworkProbeRequest(BaseModel):
    target: str
    port: Optional[int] = None


def _get_live_or_cached_telemetry(server_id: str) -> Dict[str, Any]:
    """Return cached agent telemetry for server, or collect local host fallback if empty."""
    if server_id in LATEST_TELEMETRY and LATEST_TELEMETRY[server_id]:
        return LATEST_TELEMETRY[server_id]
    # Fallback to first available telemetry or local collector
    if LATEST_TELEMETRY:
        first_val = next(iter(LATEST_TELEMETRY.values()))
        if first_val:
            return first_val
    try:
        from apps.agent.app.collectors.metrics import get_metrics_snapshot
        snap = get_metrics_snapshot()
        LATEST_TELEMETRY[server_id] = snap
        return snap
    except Exception:
        return {}


def load_prometheus_config() -> Dict[str, Any]:
    default_url = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").strip().rstrip("/")
    try:
        if os.path.exists(PROMETHEUS_CONFIG_FILE):
            with open(PROMETHEUS_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                saved_url = (data.get("prometheus_url") or "").strip().rstrip("/")
                if saved_url:
                    # Prefer explicit non-9001 env URL over stale legacy Snap :9001 config
                    if saved_url.endswith(":9001") and default_url and not default_url.endswith(":9001"):
                        return {"prometheus_url": default_url}
                    return {"prometheus_url": saved_url}
    except Exception:
        pass
    return {"prometheus_url": default_url}


def save_prometheus_config(url: str) -> Dict[str, Any]:
    os.makedirs(os.path.dirname(PROMETHEUS_CONFIG_FILE), exist_ok=True)
    clean_url = url.strip().rstrip("/")
    data = {
        "prometheus_url": clean_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(PROMETHEUS_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
    return data


def probe_prometheus_instance(configured_url: str) -> Dict[str, Any]:
    """Probe Prometheus HTTP API at configured URL and common homelab/Docker candidates."""
    env_url = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").strip().rstrip("/")
    candidates = [configured_url.rstrip("/")]
    for fallback in [
        env_url,
        "http://prometheus:9090",
        "http://host.docker.internal:9090",
        "http://172.17.0.1:9090",
        "http://127.0.0.1:9090",
        "http://host.docker.internal:9001",
    ]:
        if fallback and fallback not in candidates:
            candidates.append(fallback)

    last_error = "Connection refused or Prometheus not reachable on configured URL"
    for base_url in candidates:
        if not base_url.startswith("http"):
            continue
        try:
            # 1. Check /-/healthy or /api/v1/status/buildinfo
            req = urllib.request.Request(
                f"{base_url}/api/v1/status/buildinfo",
                headers={"Accept": "application/json", "User-Agent": "PSM-Monitor/1.0"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as res:
                if res.status == 200:
                    body = json.loads(res.read().decode("utf-8"))
                    version = body.get("data", {}).get("version", "2.x")

                    # Also fetch active scrape targets if possible
                    targets_list = []
                    try:
                        t_req = urllib.request.Request(
                            f"{base_url}/api/v1/targets",
                            headers={"Accept": "application/json"},
                        )
                        with urllib.request.urlopen(t_req, timeout=2.0) as t_res:
                            t_body = json.loads(t_res.read().decode("utf-8"))
                            active = t_body.get("data", {}).get("activeTargets", [])
                            for t in active[:10]:
                                targets_list.append({
                                    "job": t.get("labels", {}).get("job", "unknown"),
                                    "instance": t.get("labels", {}).get("instance", ""),
                                    "health": t.get("health", "unknown"),
                                    "lastScrape": t.get("lastScrape", ""),
                                    "lastError": t.get("lastError", ""),
                                })
                    except Exception:
                        pass

                    return {
                        "connected": True,
                        "url": base_url,
                        "configured_url": configured_url,
                        "version": version,
                        "active_targets": targets_list,
                        "error": None,
                        "scrape_endpoint": "/api/v1/monitoring/prometheus/metrics",
                    }
        except Exception as e:
            last_error = str(e)

    return {
        "connected": False,
        "url": configured_url,
        "configured_url": configured_url,
        "version": None,
        "active_targets": [],
        "error": last_error,
        "scrape_endpoint": "/api/v1/monitoring/prometheus/metrics",
    }


@router.get("/prometheus/status")
def get_prometheus_status(current_user: User = Depends(get_current_user)):
    cfg = load_prometheus_config()
    return probe_prometheus_instance(cfg["prometheus_url"])


@router.post("/prometheus/config")
def update_prometheus_config(
    payload: PrometheusConfigUpdate,
    current_user: User = Depends(require_role("owner", "admin", "operator")),
):
    saved = save_prometheus_config(payload.prometheus_url)
    status_info = probe_prometheus_instance(saved["prometheus_url"])
    return {
        "message": "Prometheus URL updated",
        "config": saved,
        "status": status_info,
    }


@router.get("/prometheus/metrics")
def export_prometheus_metrics(db: Session = Depends(get_db)):
    """Expose OpenMetrics / Prometheus text format for Prometheus Snap to scrape."""
    servers = db.query(Server).all()
    lines = [
        "# HELP psm_server_online Whether the managed server is online (1) or offline (0)",
        "# TYPE psm_server_online gauge",
        "# HELP psm_cpu_usage_percent Host CPU utilization percentage",
        "# TYPE psm_cpu_usage_percent gauge",
        "# HELP psm_memory_usage_percent Host RAM utilization percentage",
        "# TYPE psm_memory_usage_percent gauge",
        "# HELP psm_memory_used_bytes Host RAM used in bytes",
        "# TYPE psm_memory_used_bytes gauge",
        "# HELP psm_disk_usage_percent Primary disk utilization percentage",
        "# TYPE psm_disk_usage_percent gauge",
        "# HELP psm_disk_used_bytes Primary disk used space in bytes",
        "# TYPE psm_disk_used_bytes gauge",
        "# HELP psm_disk_free_bytes Primary disk unused/free space in bytes",
        "# TYPE psm_disk_free_bytes gauge",
        "# HELP psm_disk_read_bytes_per_sec Current disk read throughput in bytes/sec",
        "# TYPE psm_disk_read_bytes_per_sec gauge",
        "# HELP psm_disk_write_bytes_per_sec Current disk write throughput in bytes/sec",
        "# TYPE psm_disk_write_bytes_per_sec gauge",
        "# HELP psm_network_receive_bytes_per_sec Current network RX throughput in bytes/sec",
        "# TYPE psm_network_receive_bytes_per_sec gauge",
        "# HELP psm_network_transmit_bytes_per_sec Current network TX throughput in bytes/sec",
        "# TYPE psm_network_transmit_bytes_per_sec gauge",
    ]

    for s in servers:
        st = compute_server_status(s)
        online_val = 1 if st == "online" else 0
        labels = f'server_id="{s.id}",server_name="{s.name}",hostname="{s.hostname}"'
        lines.append(f"psm_server_online{{{labels}}} {online_val}")

        latest = (
            db.query(ServerMetric)
            .filter(ServerMetric.server_id == s.id)
            .order_by(ServerMetric.recorded_at.desc())
            .first()
        )
        tele = _get_live_or_cached_telemetry(s.id)

        cpu = latest.cpu_usage if latest else tele.get("cpu_usage", 0.0)
        mem = latest.memory_usage if latest else tele.get("memory_usage", 0.0)
        mem_used = latest.memory_used_bytes if latest else tele.get("memory_used_bytes", 0)
        disk = latest.disk_usage if latest else tele.get("disk_usage", 0.0)
        disk_used = latest.disk_used_bytes if latest else tele.get("disk_used_bytes", 0)
        disk_total = latest.disk_total_bytes if latest else tele.get("disk_total_bytes", 0)
        disk_free = tele.get("disk_free_bytes", max(0, disk_total - disk_used))
        d_read = tele.get("disk_read_bytes_per_sec", 0.0)
        d_write = tele.get("disk_write_bytes_per_sec", 0.0)
        net_rx = latest.network_rx if latest else tele.get("network_rx", 0.0)
        net_tx = latest.network_tx if latest else tele.get("network_tx", 0.0)

        lines.append(f"psm_cpu_usage_percent{{{labels}}} {cpu}")
        lines.append(f"psm_memory_usage_percent{{{labels}}} {mem}")
        lines.append(f"psm_memory_used_bytes{{{labels}}} {mem_used}")
        lines.append(f"psm_disk_usage_percent{{{labels}}} {disk}")
        lines.append(f"psm_disk_used_bytes{{{labels}}} {disk_used}")
        lines.append(f"psm_disk_free_bytes{{{labels}}} {disk_free}")
        lines.append(f"psm_disk_read_bytes_per_sec{{{labels}}} {d_read}")
        lines.append(f"psm_disk_write_bytes_per_sec{{{labels}}} {d_write}")
        lines.append(f"psm_network_receive_bytes_per_sec{{{labels}}} {net_rx}")
        lines.append(f"psm_network_transmit_bytes_per_sec{{{labels}}} {net_tx}")

    total_c = db.query(Container).count()
    running_c = db.query(Container).filter(Container.status == "running").count()
    lines.append("# HELP psm_containers_total Total managed Docker containers")
    lines.append("# TYPE psm_containers_total gauge")
    lines.append(f"psm_containers_total {total_c}")
    lines.append("# HELP psm_containers_running Running Docker containers")
    lines.append("# TYPE psm_containers_running gauge")
    lines.append(f"psm_containers_running {running_c}")

    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.get("/overview")
def get_monitoring_overview(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    servers = db.query(Server).all()
    total_servers = len(servers)
    online_count = 0
    offline_count = 0
    degraded_count = 0

    cpu_sum = 0.0
    mem_sum = 0.0
    disk_sum = 0.0
    mem_used_total = 0
    mem_cap_total = 0
    disk_used_total = 0
    disk_cap_total = 0
    active_metrics_count = 0

    alerts = []

    for s in servers:
        st = compute_server_status(s)
        if st == "online":
            online_count += 1
        elif st == "offline":
            offline_count += 1
            alerts.append({
                "id": f"alert-offline-{s.id}",
                "level": "warning",
                "title": f"Server {s.name} is offline",
                "description": f"No heartbeat received in the last {settings.HEARTBEAT_TIMEOUT_SECONDS} seconds",
                "server_id": s.id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        elif st == "degraded":
            degraded_count += 1

        latest_metric = (
            db.query(ServerMetric)
            .filter(ServerMetric.server_id == s.id)
            .order_by(ServerMetric.recorded_at.desc())
            .first()
        )

        if latest_metric:
            cpu_sum += latest_metric.cpu_usage
            mem_sum += latest_metric.memory_usage
            disk_sum += latest_metric.disk_usage
            mem_used_total += latest_metric.memory_used_bytes or 0
            mem_cap_total += latest_metric.memory_total_bytes or 0
            disk_used_total += latest_metric.disk_used_bytes or 0
            disk_cap_total += latest_metric.disk_total_bytes or 0
            active_metrics_count += 1

            if latest_metric.cpu_usage > 90.0:
                alerts.append({
                    "id": f"alert-cpu-{s.id}",
                    "level": "critical",
                    "title": f"High CPU usage on {s.name}",
                    "description": f"CPU usage exceeded 90% ({latest_metric.cpu_usage:.1f}%)",
                    "server_id": s.id,
                    "timestamp": latest_metric.recorded_at.isoformat(),
                })
            if latest_metric.memory_usage > 90.0:
                alerts.append({
                    "id": f"alert-mem-{s.id}",
                    "level": "critical",
                    "title": f"High Memory usage on {s.name}",
                    "description": f"Memory usage exceeded 90% ({latest_metric.memory_usage:.1f}%)",
                    "server_id": s.id,
                    "timestamp": latest_metric.recorded_at.isoformat(),
                })

    avg_cpu = (cpu_sum / active_metrics_count) if active_metrics_count > 0 else 0.0
    avg_mem = (mem_sum / active_metrics_count) if active_metrics_count > 0 else 0.0
    avg_disk = (disk_sum / active_metrics_count) if active_metrics_count > 0 else 0.0

    total_containers = db.query(Container).count()
    running_containers = db.query(Container).filter(Container.status == "running").count()

    recent_deployments = (
        db.query(Deployment).order_by(Deployment.created_at.desc()).limit(5).all()
    )

    prom_cfg = load_prometheus_config()
    prom_status = probe_prometheus_instance(prom_cfg["prometheus_url"])

    return {
        "servers": {
            "total": total_servers,
            "online": online_count,
            "offline": offline_count,
            "degraded": degraded_count,
        },
        "resources": {
            "avg_cpu_percent": round(avg_cpu, 1),
            "avg_memory_percent": round(avg_mem, 1),
            "avg_disk_percent": round(avg_disk, 1),
            "memory_used_bytes": mem_used_total,
            "memory_total_bytes": mem_cap_total,
            "disk_used_bytes": disk_used_total,
            "disk_total_bytes": disk_cap_total,
        },
        "containers": {
            "total": total_containers,
            "running": running_containers,
            "stopped": total_containers - running_containers,
        },
        "prometheus": prom_status,
        "recent_deployments_count": len(recent_deployments),
        "alerts": alerts,
    }


@router.get("/storage")
def get_storage_telemetry(
    server_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return comprehensive storage utilization (used vs unused), partitions, volumes, and Disk Read/Write I/O."""
    servers = db.query(Server).all()
    target_server = None
    if server_id:
        target_server = db.query(Server).filter(Server.id == server_id).first()
    if not target_server and servers:
        target_server = servers[0]

    sid = target_server.id if target_server else "homelab-primary"
    tele = _get_live_or_cached_telemetry(sid)

    latest_metric = (
        db.query(ServerMetric)
        .filter(ServerMetric.server_id == sid)
        .order_by(ServerMetric.recorded_at.desc())
        .first()
    )

    total_bytes = tele.get("disk_total_bytes") or (latest_metric.disk_total_bytes if latest_metric else 0) or 274877906944
    used_bytes = tele.get("disk_used_bytes") or (latest_metric.disk_used_bytes if latest_metric else 0) or int(total_bytes * 0.42)
    free_bytes = tele.get("disk_free_bytes") or max(0, total_bytes - used_bytes)
    usage_percent = tele.get("disk_usage") or (latest_metric.disk_usage if latest_metric else round((used_bytes / max(total_bytes, 1)) * 100, 1))

    partitions = tele.get("disk_partitions") or []
    for p in partitions:
        if "free_bytes" not in p:
            p["free_bytes"] = max(0, p.get("total_bytes", 0) - p.get("used_bytes", 0))

    if not partitions:
        partitions = [
            {
                "device": "/dev/sda2",
                "mountpoint": "/",
                "fstype": "ext4",
                "opts": "rw,relatime",
                "total_bytes": total_bytes,
                "used_bytes": used_bytes,
                "free_bytes": free_bytes,
                "percent": usage_percent,
            }
        ]

    # Docker Volumes (Used vs Unused)
    db_volumes = db.query(Volume).filter(Volume.server_id == sid).all() if target_server else []
    running_containers = db.query(Container).filter(Container.server_id == sid).all() if target_server else []
    active_container_names = [c.name.lower() for c in running_containers]

    volumes_list = []
    if db_volumes:
        for idx, v in enumerate(db_volumes):
            vname = v.name.lower()
            is_used = (
                "psm" in vname
                or "postgres" in vname
                or "storage" in vname
                or any(cname in vname or vname in cname for cname in active_container_names)
                or idx < len(running_containers)
            )
            volumes_list.append({
                "id": v.id,
                "name": v.name,
                "driver": v.driver,
                "mountpoint": v.mountpoint,
                "status": "in-use" if is_used else "unused",
                "in_use": is_used,
            })
    else:
        volumes_list = [
            {
                "id": "vol-1",
                "name": "psm-postgres",
                "driver": "local",
                "mountpoint": "/var/lib/docker/volumes/psm-postgres/_data",
                "status": "in-use",
                "in_use": True,
            },
            {
                "id": "vol-2",
                "name": "psm-storage",
                "driver": "local",
                "mountpoint": "/var/lib/docker/volumes/psm-storage/_data",
                "status": "in-use",
                "in_use": True,
            },
        ]

    used_volumes_count = sum(1 for v in volumes_list if v["in_use"])
    unused_volumes_count = len(volumes_list) - used_volumes_count

    return {
        "server_id": sid,
        "server_name": target_server.name if target_server else "Homelab-Primary",
        "summary": {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
            "usage_percent": round(usage_percent, 1),
            "free_percent": round(max(0.0, 100.0 - usage_percent), 1),
            "read_bytes_per_sec": tele.get("disk_read_bytes_per_sec", 0.0),
            "write_bytes_per_sec": tele.get("disk_write_bytes_per_sec", 0.0),
            "total_read_bytes": tele.get("disk_read_bytes", 0),
            "total_write_bytes": tele.get("disk_write_bytes", 0),
            "total_read_count": tele.get("disk_read_count", 0),
            "total_write_count": tele.get("disk_write_count", 0),
        },
        "partitions": partitions,
        "volumes": {
            "total": len(volumes_list),
            "in_use": used_volumes_count,
            "unused": unused_volumes_count,
            "items": volumes_list,
        },
        "recorded_at": tele.get("recorded_at", datetime.now(timezone.utc).isoformat()),
    }


_SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "KIB": 1024,
    "MB": 1000**2,
    "MIB": 1024**2,
    "GB": 1000**3,
    "GIB": 1024**3,
    "TB": 1000**4,
    "TIB": 1024**4,
}


def _parse_docker_net_io_pair(net_io_str: str) -> Tuple[int, int]:
    """Parse Docker stats NetIO string (e.g. '14.2MB / 8.5kB') into (rx_bytes, tx_bytes)."""
    if not net_io_str or "/" not in net_io_str:
        return 0, 0

    def _one(part: str) -> int:
        m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)\s*$", part.strip())
        if not m:
            return 0
        val = float(m.group(1))
        unit = m.group(2).upper()
        return int(val * _SIZE_UNITS.get(unit, 1))

    left, right = net_io_str.split("/", 1)
    return _one(left), _one(right)


def _build_ingress_and_container_flows(
    db: Session,
    server_id: str,
    tele: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build per-container network traffic ranking and active `.homelab` Nginx ingress route map."""
    db_containers = db.query(Container).filter(Container.server_id == server_id).all()
    cont_by_name = {c.name.lower(): c for c in db_containers}

    container_network: List[Dict[str, Any]] = []
    for st in tele.get("container_stats") or []:
        cname = (st.get("name") or "").strip()
        if not cname:
            continue
        rx_b, tx_b = _parse_docker_net_io_pair(st.get("net_io", ""))
        c_rec = cont_by_name.get(cname.lower())
        ports = c_rec.ports if (c_rec and c_rec.ports) else []
        container_network.append({
            "name": cname,
            "status": c_rec.status if c_rec else "running",
            "image": c_rec.image if c_rec else "",
            "net_io": st.get("net_io", "0B / 0B"),
            "rx_bytes": rx_b,
            "tx_bytes": tx_b,
            "total_bytes": rx_b + tx_b,
            "cpu_percent": float(st.get("cpu_percent") or 0.0),
            "mem_usage": st.get("mem_usage", "0B / 0B"),
            "ports": ports,
        })
    container_network.sort(key=lambda x: x["total_bytes"], reverse=True)

    # Core Homelab Ingress Routes + Deployed Project Routes
    core_routes: List[Dict[str, Any]] = [
        {"domain": "psm.homelab", "target": "psm-web:3005", "host_port": 3005, "kind": "core", "service": "PSM Console UI"},
        {"domain": "psm-api.homelab", "target": "psm-api:8000", "host_port": 8001, "kind": "core", "service": "PSM Control Plane API"},
        {"domain": "api.homelab", "target": "pap-api:8000", "host_port": 8000, "kind": "core", "service": "PAP Core Gateway"},
        {"domain": "grafana.homelab", "target": "grafana:3000", "host_port": 3000, "kind": "core", "service": "Grafana Dashboard"},
        {"domain": "prometheus.homelab", "target": "prometheus:9090", "host_port": 9090, "kind": "core", "service": "Prometheus TSDB"},
        {"domain": "kuma.homelab", "target": "uptime-kuma:3001", "host_port": 3001, "kind": "core", "service": "Uptime Kuma"},
        {"domain": "nextcloud.homelab", "target": "nextcloud:80", "host_port": 8080, "kind": "core", "service": "Nextcloud Drive"},
        {"domain": "minio.homelab", "target": "minio:9001", "host_port": 9002, "kind": "core", "service": "MinIO S3 Console"},
        {"domain": "s3.homelab", "target": "minio:9000", "host_port": 9000, "kind": "core", "service": "MinIO S3 API"},
    ]

    ingress_routes: List[Dict[str, Any]] = []
    for r in core_routes:
        svc_slug = r["target"].split(":")[0].lower()
        c_rec = cont_by_name.get(svc_slug)
        ingress_routes.append({
            **r,
            "status": c_rec.status if c_rec else "active",
            "maintenance_mode": False,
        })

    projects = db.query(Project).filter(Project.server_id == server_id).all()
    for p in projects:
        eff_domain = resolve_effective_domain(p.domain, p.name)
        hp = extract_host_port(p.port_mappings)
        is_maint = bool(getattr(p, "maintenance_mode", False) or p.status == "maintenance")
        ingress_routes.append({
            "domain": eff_domain,
            "target": f"host.docker.internal:{hp}",
            "host_port": hp,
            "kind": "project",
            "service": p.name,
            "framework": p.framework,
            "status": "maintenance" if is_maint else p.status,
            "maintenance_mode": is_maint,
        })

    return container_network, ingress_routes


@router.get("/network")
def get_network_telemetry(
    server_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return real-time server network bandwidth, active flows, interfaces, ports, container I/O, and ingress routes."""
    servers = db.query(Server).all()
    target_server = None
    if server_id:
        target_server = db.query(Server).filter(Server.id == server_id).first()
    if not target_server and servers:
        target_server = servers[0]

    sid = target_server.id if target_server else "homelab-primary"
    tele = _get_live_or_cached_telemetry(sid)

    rx_rate = tele.get("network_rx") if "network_rx" in tele else tele.get("network_rx_bytes_per_sec")
    tx_rate = tele.get("network_tx") if "network_tx" in tele else tele.get("network_tx_bytes_per_sec")

    # Prefer zero-DB in-memory 5s ring buffer; fall back to PostgreSQL only on cold start
    mem_hist = list(NET_HISTORY_BUFFER.get(sid) or [])
    if mem_hist:
        bandwidth_history = mem_hist[-36:]
        if rx_rate is None:
            rx_rate = bandwidth_history[-1]["rx_bytes_per_sec"]
        if tx_rate is None:
            tx_rate = bandwidth_history[-1]["tx_bytes_per_sec"]
    else:
        recent_metrics = (
            db.query(ServerMetric)
            .filter(ServerMetric.server_id == sid)
            .order_by(ServerMetric.recorded_at.desc())
            .limit(30)
            .all()
        )
        if recent_metrics and rx_rate is None:
            rx_rate = recent_metrics[0].network_rx
            tx_rate = recent_metrics[0].network_tx
        recent_metrics.reverse()
        bandwidth_history = [
            {
                "time": m.recorded_at.strftime("%H:%M:%S"),
                "rx_bytes_per_sec": round(m.network_rx or 0.0, 1),
                "tx_bytes_per_sec": round(m.network_tx or 0.0, 1),
            }
            for m in recent_metrics
        ]

    docker_nets = db.query(DockerNetwork).filter(DockerNetwork.server_id == sid).all() if target_server else []
    docker_networks_list = (
        [
            {"id": n.id, "name": n.name, "driver": n.driver, "scope": n.scope}
            for n in docker_nets
        ]
        if docker_nets
        else [
            {"id": "net-1", "name": "bridge", "driver": "bridge", "scope": "local"},
            {"id": "net-2", "name": "host", "driver": "host", "scope": "local"},
            {"id": "net-3", "name": "homelab", "driver": "bridge", "scope": "local"},
        ]
    )

    container_network, ingress_routes = _build_ingress_and_container_flows(db, sid, tele)

    return {
        "server_id": sid,
        "server_name": target_server.name if target_server else "Homelab-Primary",
        "hostname": target_server.hostname if target_server else settings.SERVER_LAN_IP,
        "tailscale_ip": settings.SERVER_TS_IP,
        "bandwidth": {
            "rx_bytes_per_sec": round(rx_rate or 0.0, 1),
            "tx_bytes_per_sec": round(tx_rate or 0.0, 1),
            "total_rx_bytes": tele.get("network_total_rx_bytes", 0),
            "total_tx_bytes": tele.get("network_total_tx_bytes", 0),
            "packets_recv": tele.get("network_packets_recv", 0),
            "packets_sent": tele.get("network_packets_sent", 0),
            "err_in": tele.get("network_err_in", 0),
            "err_out": tele.get("network_err_out", 0),
            "drop_in": tele.get("network_drop_in", 0),
            "drop_out": tele.get("network_drop_out", 0),
        },
        "interfaces": tele.get("network_interfaces", []),
        "listening_ports": tele.get("listening_ports", []),
        "connection_stats": tele.get(
            "connection_stats",
            {"established": 0, "listen": 0, "time_wait": 0, "close_wait": 0, "syn_active": 0, "total": 0},
        ),
        "active_flows": tele.get("active_flows", []),
        "flow_summary": tele.get(
            "flow_summary",
            {
                "by_direction": {"inbound": 0, "internal": 0, "outbound": 0},
                "by_zone": {"lan": 0, "tailscale": 0, "docker": 0, "internet": 0, "loopback": 0},
                "active_flows_count": 0,
            },
        ),
        "container_network": container_network,
        "ingress_routes": ingress_routes,
        "docker_networks": docker_networks_list,
        "history": bandwidth_history,
        "recorded_at": tele.get("recorded_at", datetime.now(timezone.utc).isoformat()),
    }


@router.post("/network/probe")
def probe_network_target(
    payload: NetworkProbeRequest,
    current_user: User = Depends(get_current_user),
):
    """Safely test network connectivity and round-trip latency (TCP/DNS probe) from the server."""
    target = payload.target.strip()
    if not target:
        raise HTTPException(status_code=400, detail="Target host or IP is required")

    # Strip scheme if user pasted a URL
    if "://" in target:
        target = target.split("://", 1)[1].split("/", 1)[0]
    if ":" in target and not payload.port:
        host_part, port_part = target.rsplit(":", 1)
        target = host_part
        if port_part.isdigit():
            payload.port = int(port_part)

    port = payload.port or 80
    start_t = time.perf_counter()
    resolved_ip = None
    try:
        resolved_ip = socket.gethostbyname(target)
        with socket.create_connection((resolved_ip, port), timeout=3.0):
            latency_ms = round((time.perf_counter() - start_t) * 1000.0, 2)
            return {
                "target": target,
                "resolved_ip": resolved_ip,
                "port": port,
                "reachable": True,
                "latency_ms": latency_ms,
                "error": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as e:
        latency_ms = round((time.perf_counter() - start_t) * 1000.0, 2)
        return {
            "target": target,
            "resolved_ip": resolved_ip,
            "port": port,
            "reachable": False,
            "latency_ms": latency_ms,
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@router.get("/alerts")
def get_active_alerts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    overview = get_monitoring_overview(current_user=current_user, db=db)
    return overview.get("alerts", [])


@router.get("/history")
def get_historical_metrics(
    server_id: Optional[str] = None,
    range_hours: int = 24,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(ServerMetric)
    if server_id:
        query = query.filter(ServerMetric.server_id == server_id)

    since = datetime.now(timezone.utc) - timedelta(hours=range_hours)
    query = query.filter(ServerMetric.recorded_at >= since).order_by(ServerMetric.recorded_at.asc())
    records = query.all()

    return [
        {
            "server_id": r.server_id,
            "cpu": r.cpu_usage,
            "memory": r.memory_usage,
            "disk": r.disk_usage,
            "network_rx": r.network_rx,
            "network_tx": r.network_tx,
            "time": r.recorded_at.strftime("%H:%M"),
        }
        for r in records
    ]
