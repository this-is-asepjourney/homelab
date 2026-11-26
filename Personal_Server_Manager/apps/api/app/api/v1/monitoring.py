import json
import os
import re
import socket
import subprocess
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
    DockerImage,
    ServerMetric,
    Deployment,
    Project,
    EnvironmentVariable,
    User,
)
from apps.api.app.api.v1.servers import compute_server_status
from apps.api.app.api.v1.agents import LATEST_TELEMETRY, NET_HISTORY_BUFFER
from apps.api.app.services.alert_service import load_notification_settings
from apps.api.app.services.pap_client import extract_host_port, resolve_effective_domain
from apps.api.app.core.security import decrypt_secret

router = APIRouter(prefix="/monitoring", tags=["Monitoring Center"])

PROMETHEUS_CONFIG_FILE = os.path.join(settings.STORAGE_BASE_DIR, "prometheus_settings.json")
S3_BUCKETS_REGISTRY_FILE = os.path.join(settings.STORAGE_BASE_DIR, "s3_buckets_registry.json")
_MINIO_STATUS_CACHE: Dict[str, Any] = {"expires_at": 0.0, "data": None}


class PrometheusConfigUpdate(BaseModel):
    prometheus_url: str


class NetworkProbeRequest(BaseModel):
    target: str
    port: Optional[int] = None


class S3BucketCreateRequest(BaseModel):
    bucket_name: str
    public_read: bool = True


HOMELAB_ECOSYSTEM_CATALOG: List[Dict[str, Any]] = [
    # Core Databases
    {"key": "postgres", "label": "PostgreSQL 16 Core", "category": "databases", "port": 5432, "domain": "postgres:5432", "aliases": ["postgres", "homelab_postgres", "homelab-postgres"], "prom_job": None},
    {"key": "mysql", "label": "MySQL 8.4 Shared", "category": "databases", "port": 3307, "domain": "mysql:3306", "aliases": ["mysql", "homelab_mysql", "homelab-mysql"], "prom_job": None},
    {"key": "redis", "label": "Redis 7 Cache & Queue", "category": "databases", "port": 6379, "domain": "redis:6379", "aliases": ["redis", "homelab_redis", "homelab-redis"], "prom_job": None},
    # Platform & Ingress
    {"key": "pap-api", "label": "Personal API Platform", "category": "platform", "port": 8000, "domain": "api.homelab", "aliases": ["pap-api", "homelab_pap_api", "pap_api"], "prom_job": "pap-core"},
    {"key": "psm-api", "label": "PSM Control Plane API", "category": "platform", "port": 8001, "domain": "psm-api.homelab", "aliases": ["psm-api", "homelab_psm_api", "psm_api"], "prom_job": "psm-api"},
    {"key": "psm-agent", "label": "PSM Host Agent", "category": "platform", "port": 0, "domain": "internal", "aliases": ["psm-agent", "homelab_psm_agent", "psm_agent"], "prom_job": None},
    {"key": "psm-web", "label": "PSM Web Console", "category": "platform", "port": 3005, "domain": "psm.homelab", "aliases": ["psm-web", "homelab_psm_web", "psm_web"], "prom_job": None},
    {"key": "homelab-router", "label": "Nginx Ingress Router", "category": "platform", "port": 80, "domain": "*.homelab", "aliases": ["homelab-router", "homelab_router", "router"], "prom_job": None},
    {"key": "homelab-dns", "label": "CoreDNS Local Resolver", "category": "platform", "port": 53, "domain": "dns.homelab", "aliases": ["homelab-dns", "homelab_dns", "coredns"], "prom_job": None},
    # Cloud & Object Storage
    {"key": "minio", "label": "MinIO / Silo S3 Storage", "category": "storage_cloud", "port": 9002, "domain": "minio.homelab", "aliases": ["minio", "homelab_minio", "homelab-minio", "silo"], "prom_job": None},
    {"key": "nextcloud", "label": "Nextcloud Personal Cloud", "category": "storage_cloud", "port": 8082, "domain": "cloud.homelab", "aliases": ["nextcloud", "homelab_nextcloud", "homelab-nextcloud"], "prom_job": None},
    # Observability Stack
    {"key": "prometheus", "label": "Prometheus TSDB", "category": "observability", "port": 9090, "domain": "prometheus.homelab", "aliases": ["prometheus", "homelab_prometheus"], "prom_job": "prometheus"},
    {"key": "grafana", "label": "Grafana Dashboards", "category": "observability", "port": 3000, "domain": "grafana.homelab", "aliases": ["grafana", "homelab_grafana"], "prom_job": None},
    {"key": "cadvisor", "label": "cAdvisor Container Stats", "category": "observability", "port": 8081, "domain": "cadvisor.homelab", "aliases": ["cadvisor", "homelab_cadvisor"], "prom_job": "cadvisor"},
    {"key": "uptime-kuma", "label": "Uptime Kuma Monitor", "category": "observability", "port": 3001, "domain": "status.homelab", "aliases": ["uptime-kuma", "homelab_uptime_kuma", "uptime_kuma"], "prom_job": None},
    {"key": "node-exporter", "label": "Host Node Exporter", "category": "observability", "port": 9100, "domain": "node-exporter:9100", "aliases": ["node-exporter", "homelab_node_exporter", "node_exporter"], "prom_job": "node"},
    # Eternal IoT Pipeline
    {"key": "mosquitto", "label": "Mosquitto MQTT Broker", "category": "iot_pipeline", "port": 1883, "domain": "mqtt.homelab", "aliases": ["mosquitto", "homelab_mosquitto"], "prom_job": None},
    {"key": "telegraf", "label": "Telegraf IoT Collector", "category": "iot_pipeline", "port": 9273, "domain": "telegraf:9273", "aliases": ["telegraf", "homelab_telegraf"], "prom_job": "telegraf"},
]

HOMELAB_VOLUME_ROLES: Dict[str, Tuple[List[str], str]] = {
    "postgres_data": (["postgres", "homelab_postgres"], "PostgreSQL 16 Databases (psm, pap_core, eternal_iot)"),
    "mysql_data": (["mysql", "homelab_mysql"], "MySQL 8.4 Databases (fadb, stock_umkm)"),
    "redis_data": (["redis", "homelab_redis"], "Redis 7 AOF Cache & Message Broker"),
    "minio_data": (["minio", "homelab_minio", "silo"], "MinIO / Silo S3 Object Storage Buckets"),
    "nextcloud_data": (["nextcloud", "homelab_nextcloud"], "Nextcloud Personal Cloud User Files"),
    "prometheus_data": (["prometheus", "homelab_prometheus"], "Prometheus 15-Day Metrics TSDB"),
    "grafana_data": (["grafana", "homelab_grafana"], "Grafana Dashboards & Provisioned State"),
    "uptime_kuma_data": (["uptime-kuma", "homelab_uptime_kuma"], "Uptime Kuma Monitors & SQLite History"),
    "mosquitto_data": (["mosquitto", "homelab_mosquitto"], "Mosquitto MQTT Persistent Queue"),
    "psm_storage": (["psm-api", "psm-agent", "homelab_psm_api", "homelab_psm_agent"], "PSM Workspaces, Releases, Uploads & Backups"),
}


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
    """Expose OpenMetrics / Prometheus text format for Homelab Prometheus (:9090) to scrape."""
    servers = db.query(Server).all()
    lines = [
        "# HELP psm_server_online Whether the managed server is online (1) or offline (0)",
        "# TYPE psm_server_online gauge",
        "# HELP psm_cpu_usage_percent Host CPU utilization percentage",
        "# TYPE psm_cpu_usage_percent gauge",
        "# HELP psm_load_avg_1m Host 1-minute load average",
        "# TYPE psm_load_avg_1m gauge",
        "# HELP psm_memory_usage_percent Host RAM utilization percentage",
        "# TYPE psm_memory_usage_percent gauge",
        "# HELP psm_memory_used_bytes Host RAM used in bytes",
        "# TYPE psm_memory_used_bytes gauge",
        "# HELP psm_swap_used_bytes Host Swap used in bytes",
        "# TYPE psm_swap_used_bytes gauge",
        "# HELP psm_swap_total_bytes Host Swap total capacity in bytes",
        "# TYPE psm_swap_total_bytes gauge",
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

        cpu = tele.get("cpu_usage") if tele.get("cpu_usage") is not None else (latest.cpu_usage if latest else 0.0)
        load_list = tele.get("load_avg") or [0.0, 0.0, 0.0]
        load_1m = float(load_list[0]) if isinstance(load_list, list) and load_list else 0.0
        mem = tele.get("memory_usage") if tele.get("memory_usage") is not None else (latest.memory_usage if latest else 0.0)
        mem_used = tele.get("memory_used_bytes") or (latest.memory_used_bytes if latest else 0)
        swap_used = tele.get("swap_used_bytes", 0) or 0
        swap_total = tele.get("swap_total_bytes", 0) or 0
        disk = tele.get("disk_usage") if tele.get("disk_usage") is not None else (latest.disk_usage if latest else 0.0)
        disk_used = tele.get("disk_used_bytes") or (latest.disk_used_bytes if latest else 0)
        disk_total = tele.get("disk_total_bytes") or (latest.disk_total_bytes if latest else 0)
        disk_free = tele.get("disk_free_bytes", max(0, disk_total - disk_used))
        d_read = tele.get("disk_read_bytes_per_sec", 0.0)
        d_write = tele.get("disk_write_bytes_per_sec", 0.0)
        net_rx = tele.get("network_rx") if tele.get("network_rx") is not None else (latest.network_rx if latest else 0.0)
        net_tx = tele.get("network_tx") if tele.get("network_tx") is not None else (latest.network_tx if latest else 0.0)

        lines.append(f"psm_cpu_usage_percent{{{labels}}} {cpu}")
        lines.append(f"psm_load_avg_1m{{{labels}}} {load_1m}")
        lines.append(f"psm_memory_usage_percent{{{labels}}} {mem}")
        lines.append(f"psm_memory_used_bytes{{{labels}}} {mem_used}")
        lines.append(f"psm_swap_used_bytes{{{labels}}} {swap_used}")
        lines.append(f"psm_swap_total_bytes{{{labels}}} {swap_total}")
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


def _parse_size_string_to_bytes(raw: str) -> int:
    if not raw:
        return 0
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*$", raw.strip())
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).upper()
    mult = {
        "B": 1,
        "KB": 1000,
        "KIB": 1024,
        "MB": 1000**2,
        "MIB": 1024**2,
        "GB": 1000**3,
        "GIB": 1024**3,
        "TB": 1000**4,
        "TIB": 1024**4,
    }.get(unit, 1)
    return int(val * mult)


def _normalize_container_stats(raw_stats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in raw_stats or []:
        cname = str(item.get("name") or "").strip()
        if not cname:
            continue
        cpu_str = str(item.get("cpu_percent") or "0%").replace("%", "").strip()
        try:
            cpu_val = round(float(cpu_str), 2)
        except ValueError:
            cpu_val = 0.0

        mem_pct_str = str(item.get("mem_percent") or "0%").replace("%", "").strip()
        try:
            mem_pct_val = round(float(mem_pct_str), 2)
        except ValueError:
            mem_pct_val = 0.0

        mem_usage_raw = str(item.get("mem_usage") or "0B / 0B")
        parts = [p.strip() for p in mem_usage_raw.split("/")]
        mem_used_b = _parse_size_string_to_bytes(parts[0]) if parts else 0
        mem_limit_b = _parse_size_string_to_bytes(parts[1]) if len(parts) > 1 else 0

        normalized.append({
            "id": item.get("id", ""),
            "name": cname,
            "cpu_percent": cpu_val,
            "mem_percent": mem_pct_val,
            "mem_used_bytes": mem_used_b,
            "mem_limit_bytes": mem_limit_b,
            "mem_usage": mem_usage_raw,
            "net_io": item.get("net_io", "0B / 0B"),
            "block_io": item.get("block_io", "0B / 0B"),
        })
    normalized.sort(key=lambda x: (x["cpu_percent"], x["mem_used_bytes"]), reverse=True)
    return normalized


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
    mem_avail_total = 0
    mem_cap_total = 0
    swap_used_total = 0
    swap_cap_total = 0
    disk_used_total = 0
    disk_cap_total = 0
    net_rx_total = 0.0
    net_tx_total = 0.0
    disk_read_total = 0.0
    disk_write_total = 0.0
    primary_load_avg: List[float] = [0.0, 0.0, 0.0]
    primary_cpu_cores = 1
    primary_uptime = 0
    active_metrics_count = 0

    notif_cfg = load_notification_settings()
    cpu_thresh = float(notif_cfg.get("cpu_threshold", 90.0))
    mem_thresh = float(notif_cfg.get("memory_threshold", 90.0))
    disk_thresh = float(notif_cfg.get("disk_threshold", 90.0))

    alerts = []
    primary_server = servers[0] if servers else None
    primary_tele: Dict[str, Any] = {}

    for idx, s in enumerate(servers):
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
        live_tele = LATEST_TELEMETRY.get(s.id) or ({} if idx > 0 else _get_live_or_cached_telemetry(s.id))
        if idx == 0:
            primary_tele = live_tele

        has_live = bool(live_tele and (live_tele.get("cpu_usage") is not None or live_tele.get("memory_total_bytes")))
        if has_live or latest_metric:
            c_val = float(live_tele.get("cpu_usage")) if live_tele.get("cpu_usage") is not None else (latest_metric.cpu_usage if latest_metric else 0.0)
            m_val = float(live_tele.get("memory_usage")) if live_tele.get("memory_usage") is not None else (latest_metric.memory_usage if latest_metric else 0.0)
            d_val = float(live_tele.get("disk_usage")) if live_tele.get("disk_usage") is not None else (latest_metric.disk_usage if latest_metric else 0.0)
            m_used = int(live_tele.get("memory_used_bytes") or (latest_metric.memory_used_bytes if latest_metric else 0) or 0)
            m_cap = int(live_tele.get("memory_total_bytes") or (latest_metric.memory_total_bytes if latest_metric else 0) or 0)
            m_avail = int(live_tele.get("memory_available_bytes") or max(0, m_cap - m_used))
            sw_used = int(live_tele.get("swap_used_bytes") or live_tele.get("swap_used") or 0)
            sw_cap = int(live_tele.get("swap_total_bytes") or live_tele.get("swap_total") or 0)
            d_used = int(live_tele.get("disk_used_bytes") or (latest_metric.disk_used_bytes if latest_metric else 0) or 0)
            d_cap = int(live_tele.get("disk_total_bytes") or (latest_metric.disk_total_bytes if latest_metric else 0) or 0)

            cpu_sum += c_val
            mem_sum += m_val
            disk_sum += d_val
            mem_used_total += m_used
            mem_avail_total += m_avail
            mem_cap_total += m_cap
            swap_used_total += sw_used
            swap_cap_total += sw_cap
            disk_used_total += d_used
            disk_cap_total += d_cap
            net_rx_total += float(live_tele.get("network_rx") or live_tele.get("network_rx_bytes_per_sec") or (latest_metric.network_rx if latest_metric else 0.0) or 0.0)
            net_tx_total += float(live_tele.get("network_tx") or live_tele.get("network_tx_bytes_per_sec") or (latest_metric.network_tx if latest_metric else 0.0) or 0.0)
            disk_read_total += float(live_tele.get("disk_read_bytes_per_sec") or 0.0)
            disk_write_total += float(live_tele.get("disk_write_bytes_per_sec") or 0.0)
            active_metrics_count += 1

            if idx == 0:
                raw_load = live_tele.get("load_avg")
                if isinstance(raw_load, list) and len(raw_load) >= 3:
                    primary_load_avg = [round(float(x), 2) for x in raw_load[:3]]
                primary_cpu_cores = int(live_tele.get("cpu_cores") or 1)
                primary_uptime = int(live_tele.get("uptime") or live_tele.get("uptime_seconds") or (latest_metric.uptime if latest_metric else 0) or 0)

            rec_ts = live_tele.get("recorded_at") or (latest_metric.recorded_at.isoformat() if latest_metric else datetime.now(timezone.utc).isoformat())
            if c_val >= cpu_thresh:
                alerts.append({
                    "id": f"alert-cpu-{s.id}",
                    "level": "critical",
                    "title": f"High CPU usage on {s.name}",
                    "description": f"CPU usage reached {c_val:.1f}% (threshold: {cpu_thresh:.0f}%)",
                    "server_id": s.id,
                    "timestamp": rec_ts,
                })
            if m_val >= mem_thresh:
                alerts.append({
                    "id": f"alert-mem-{s.id}",
                    "level": "critical",
                    "title": f"High RAM usage on {s.name}",
                    "description": f"RAM usage reached {m_val:.1f}% (threshold: {mem_thresh:.0f}%)",
                    "server_id": s.id,
                    "timestamp": rec_ts,
                })
            if d_val >= disk_thresh:
                alerts.append({
                    "id": f"alert-disk-{s.id}",
                    "level": "warning",
                    "title": f"High Disk usage on {s.name}",
                    "description": f"Root disk usage reached {d_val:.1f}% (threshold: {disk_thresh:.0f}%)",
                    "server_id": s.id,
                    "timestamp": rec_ts,
                })

    avg_cpu = (cpu_sum / active_metrics_count) if active_metrics_count > 0 else 0.0
    avg_mem = (mem_sum / active_metrics_count) if active_metrics_count > 0 else 0.0
    avg_disk = (disk_sum / active_metrics_count) if active_metrics_count > 0 else 0.0
    swap_percent = round((swap_used_total / swap_cap_total) * 100.0, 1) if swap_cap_total > 0 else 0.0

    all_containers = db.query(Container).all()
    total_containers = len(all_containers)
    running_containers = sum(1 for c in all_containers if c.status == "running")

    recent_deployments = (
        db.query(Deployment).order_by(Deployment.created_at.desc()).limit(5).all()
    )

    prom_cfg = load_prometheus_config()
    prom_status = probe_prometheus_instance(prom_cfg["prometheus_url"])
    prom_jobs_up = {
        str(t.get("job") or "").lower()
        for t in (prom_status.get("active_targets") or [])
        if t.get("health") == "up"
    }

    # Build Homelab 18-Service Ecosystem Health Matrix
    container_by_clean_name: Dict[str, Container] = {}
    for c in all_containers:
        clean_n = c.name.strip().lstrip("/").lower()
        container_by_clean_name[clean_n] = c

    ecosystem_items: List[Dict[str, Any]] = []
    running_eco_count = 0
    for svc in HOMELAB_ECOSYSTEM_CATALOG:
        matched_c: Optional[Container] = None
        for alias in svc["aliases"]:
            a_low = alias.lower()
            if a_low in container_by_clean_name:
                matched_c = container_by_clean_name[a_low]
                break
            for cname, cobj in container_by_clean_name.items():
                if cname == a_low or cname.endswith(f"_{a_low}") or cname.endswith(f"-{a_low}"):
                    matched_c = cobj
                    break
            if matched_c:
                break

        if matched_c:
            svc_status = "running" if matched_c.status == "running" else "stopped"
            cname_out = matched_c.name
        elif svc["key"] == "psm-api":
            svc_status = "running"
            cname_out = "homelab_psm_api"
        elif svc["prom_job"] and svc["prom_job"].lower() in prom_jobs_up:
            svc_status = "running"
            cname_out = svc["aliases"][0]
        else:
            svc_status = "standby"
            cname_out = svc["aliases"][0]

        if svc_status == "running":
            running_eco_count += 1

        ecosystem_items.append({
            "key": svc["key"],
            "label": svc["label"],
            "category": svc["category"],
            "port": svc["port"],
            "domain": svc["domain"],
            "container_name": cname_out,
            "status": svc_status,
            "prometheus_scraped": bool(svc["prom_job"] and svc["prom_job"].lower() in prom_jobs_up),
        })

    top_containers = _normalize_container_stats(primary_tele.get("container_stats") or [])

    alert_rules = [
        {
            "id": "rule-cpu",
            "label": f"High CPU Utilization (> {cpu_thresh:.0f}%)",
            "current_value": f"{avg_cpu:.1f}%",
            "threshold": cpu_thresh,
            "status": "firing" if avg_cpu >= cpu_thresh else "ok",
            "enabled": True,
        },
        {
            "id": "rule-memory",
            "label": f"High RAM Utilization (> {mem_thresh:.0f}%)",
            "current_value": f"{avg_mem:.1f}%",
            "threshold": mem_thresh,
            "status": "firing" if avg_mem >= mem_thresh else "ok",
            "enabled": True,
        },
        {
            "id": "rule-disk",
            "label": f"Root Disk Capacity (> {disk_thresh:.0f}%)",
            "current_value": f"{avg_disk:.1f}%",
            "threshold": disk_thresh,
            "status": "firing" if avg_disk >= disk_thresh else "ok",
            "enabled": True,
        },
        {
            "id": "rule-heartbeat",
            "label": f"Agent Heartbeat Watchdog (> {settings.HEARTBEAT_TIMEOUT_SECONDS}s)",
            "current_value": f"{online_count}/{max(total_servers, 1)} online",
            "threshold": settings.HEARTBEAT_TIMEOUT_SECONDS,
            "status": "firing" if offline_count > 0 else "ok",
            "enabled": bool(notif_cfg.get("notify_server_offline", True)),
        },
    ]

    return {
        "primary_server_id": primary_server.id if primary_server else None,
        "primary_server_name": primary_server.name if primary_server else "Homelab-Primary",
        "servers": {
            "total": total_servers,
            "online": online_count,
            "offline": offline_count,
            "degraded": degraded_count,
        },
        "resources": {
            "avg_cpu_percent": round(avg_cpu, 1),
            "cpu_cores": primary_cpu_cores,
            "load_avg": primary_load_avg,
            "uptime_seconds": primary_uptime,
            "avg_memory_percent": round(avg_mem, 1),
            "memory_used_bytes": mem_used_total,
            "memory_available_bytes": mem_avail_total,
            "memory_total_bytes": mem_cap_total,
            "swap_used_bytes": swap_used_total,
            "swap_total_bytes": swap_cap_total,
            "swap_percent": swap_percent,
            "avg_disk_percent": round(avg_disk, 1),
            "disk_used_bytes": disk_used_total,
            "disk_total_bytes": disk_cap_total,
            "disk_read_bytes_per_sec": round(disk_read_total, 1),
            "disk_write_bytes_per_sec": round(disk_write_total, 1),
            "network_rx_bytes_per_sec": round(net_rx_total, 1),
            "network_tx_bytes_per_sec": round(net_tx_total, 1),
        },
        "containers": {
            "total": total_containers,
            "running": running_containers,
            "stopped": total_containers - running_containers,
        },
        "homelab_services": {
            "total": len(ecosystem_items),
            "running": running_eco_count,
            "items": ecosystem_items,
        },
        "top_containers": top_containers[:15],
        "prometheus": prom_status,
        "recent_deployments_count": len(recent_deployments),
        "alert_rules": alert_rules,
        "notifications_enabled": bool(notif_cfg.get("enabled", False)),
        "alerts": alerts,
    }


def _compute_dir_footprint(dir_path: str, max_entries: int = 1500) -> Dict[str, int]:
    total_size = 0
    files_count = 0
    scanned = 0
    if not dir_path or not os.path.isdir(dir_path):
        return {"bytes": 0, "files": 0}
    try:
        stack = [dir_path]
        while stack and scanned < max_entries:
            curr = stack.pop()
            try:
                with os.scandir(curr) as it:
                    for entry in it:
                        scanned += 1
                        if scanned > max_entries:
                            break
                        if entry.is_symlink():
                            continue
                        if entry.is_file(follow_symlinks=False):
                            files_count += 1
                            try:
                                total_size += entry.stat(follow_symlinks=False).st_size
                            except OSError:
                                pass
                        elif entry.is_dir(follow_symlinks=False) and entry.name not in (".git", "node_modules", "vendor", ".next"):
                            stack.append(entry.path)
            except OSError:
                pass
    except Exception:
        pass
    return {"bytes": total_size, "files": files_count}


def _load_custom_s3_buckets() -> List[Dict[str, Any]]:
    try:
        if os.path.exists(S3_BUCKETS_REGISTRY_FILE):
            with open(S3_BUCKETS_REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return []


def _save_custom_s3_buckets(items: List[Dict[str, Any]]) -> None:
    try:
        os.makedirs(os.path.dirname(S3_BUCKETS_REGISTRY_FILE), exist_ok=True)
        with open(S3_BUCKETS_REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
    except Exception:
        pass


def _probe_minio_status(db: Session, force_refresh: bool = False) -> Dict[str, Any]:
    now_ts = time.time()
    is_testing = os.getenv("TESTING", "").lower() == "true"
    if not force_refresh and _MINIO_STATUS_CACHE["data"] and now_ts < _MINIO_STATUS_CACHE["expires_at"]:
        return _MINIO_STATUS_CACHE["data"]

    default_buckets = [
        {"name": "images", "access": "public-read", "purpose": "Static assets & image uploads"},
        {"name": "stock-umkm", "access": "public-read", "purpose": "Laravel / UMKM product catalog media"},
        {"name": "homelab-storage", "access": "private", "purpose": "Shared Homelab object storage"},
        {"name": "backups", "access": "private", "purpose": "PSM & Database disaster-recovery archives"},
    ]
    bucket_map: Dict[str, Dict[str, Any]] = {b["name"]: dict(b) for b in default_buckets}

    for custom_b in _load_custom_s3_buckets():
        bname = str(custom_b.get("name") or "").strip().lower()
        if bname:
            bucket_map[bname] = {
                "name": bname,
                "access": "public-read" if custom_b.get("public_read", True) else "private",
                "purpose": custom_b.get("purpose", "Custom project S3 bucket"),
            }

    # Discover project-referenced S3 buckets from EnvironmentVariable keys
    try:
        s3_vars = (
            db.query(EnvironmentVariable)
            .filter(EnvironmentVariable.key.in_(["AWS_BUCKET", "S3_BUCKET", "S3_BUCKET_NAME", "MINIO_BUCKET"]))
            .all()
        )
        for ev in s3_vars:
            val = (decrypt_secret(ev.encrypted_value) or "").strip().lower()
            if val and re.match(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", val) and val not in bucket_map:
                bucket_map[val] = {
                    "name": val,
                    "access": "public-read",
                    "purpose": "Connected PSM Project S3 Bucket",
                }
    except Exception:
        pass

    endpoint_env = os.getenv("MINIO_ENDPOINT", "http://minio:9000").strip().rstrip("/")
    online = False
    resolved_endpoint = endpoint_env

    if not is_testing:
        candidates = [endpoint_env, "http://minio:9000", f"http://{settings.SERVER_LAN_IP}:9000", "http://127.0.0.1:9000"]
        seen_c = set()
        for cand in candidates:
            if not cand or cand in seen_c:
                continue
            seen_c.add(cand)
            try:
                req = urllib.request.Request(
                    f"{cand}/minio/health/live",
                    headers={"User-Agent": "PSM-Storage-Monitor/1.0"},
                )
                with urllib.request.urlopen(req, timeout=0.8) as res:
                    if res.status in (200, 204):
                        online = True
                        resolved_endpoint = cand
                        break
            except Exception:
                continue

        # Also check if MinIO container is running in inventory DB when internal network differs
        if not online:
            minio_c = (
                db.query(Container)
                .filter(Container.status == "running")
                .all()
            )
            if any("minio" in c.name.lower() or "silo" in c.name.lower() for c in minio_c):
                online = True

    result = {
        "online": online,
        "endpoint_internal": "http://minio:9000",
        "resolved_endpoint": resolved_endpoint,
        "api_port": 9000,
        "console_port": 9002,
        "domain_api": "s3.homelab",
        "domain_console": "minio.homelab",
        "buckets": list(bucket_map.values()),
        "buckets_count": len(bucket_map),
    }
    _MINIO_STATUS_CACHE["data"] = result
    _MINIO_STATUS_CACHE["expires_at"] = now_ts + (2.0 if is_testing else 20.0)
    return result


def _resolve_volume_metadata(v_name: str, v_driver: str, running_container_names: List[str]) -> Dict[str, Any]:
    raw_driver = v_driver or "local"
    clean_driver = raw_driver
    attached: List[str] = []

    if "|u:" in raw_driver:
        clean_driver, enc = raw_driver.split("|u:", 1)
        attached = [x.strip() for x in enc.split(",") if x.strip()]
    elif "|used:" in raw_driver:
        clean_driver, enc = raw_driver.split("|used:", 1)
        attached = [x.strip() for x in enc.split(",") if x.strip()]

    v_low = v_name.strip().lower()
    stripped = re.sub(r"^(homelab_|psm_)", "", v_low)
    role = "Docker Persistent Volume"

    known = HOMELAB_VOLUME_ROLES.get(v_low) or HOMELAB_VOLUME_ROLES.get(stripped)
    if known:
        expected_containers, role = known
        if not attached:
            for exp in expected_containers:
                for rc in running_container_names:
                    if rc == exp or rc.endswith(f"_{exp}") or rc.endswith(f"-{exp}"):
                        if rc not in attached:
                            attached.append(rc)
            # Control plane itself always mounts psm_storage & uses postgres_data in production
            if stripped == "psm_storage" and not attached and os.path.isdir(settings.STORAGE_BASE_DIR):
                attached.append("psm-api")

    if not attached and len(stripped) >= 4:
        for rc in running_container_names:
            rc_clean = re.sub(r"^(homelab_|psm_)", "", rc)
            if stripped == rc_clean or stripped.startswith(f"{rc_clean}_") or stripped.endswith(f"_{rc_clean}"):
                attached.append(rc)

    is_used = len(attached) > 0
    return {
        "driver": clean_driver or "local",
        "attached_containers": attached,
        "role": role,
        "in_use": is_used,
        "status": "in-use" if is_used else "unused",
    }


@router.get("/storage")
def get_storage_telemetry(
    server_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return comprehensive Homelab storage utilization, disk I/O, accurate Docker volume mounts, PSM footprint & MinIO S3 status."""
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

    # Docker Volumes (Accurate Mount & Homelab Service Matching — no false index-based positives)
    db_volumes = db.query(Volume).filter(Volume.server_id == sid).all() if target_server else []
    server_containers = db.query(Container).filter(Container.server_id == sid).all() if target_server else []
    running_container_names = [
        c.name.strip().lstrip("/").lower()
        for c in server_containers
        if c.status == "running"
    ]

    volumes_list = []
    if db_volumes:
        for v in db_volumes:
            meta = _resolve_volume_metadata(v.name, v.driver, running_container_names)
            volumes_list.append({
                "id": v.id,
                "name": v.name,
                "driver": meta["driver"],
                "mountpoint": v.mountpoint,
                "role": meta["role"],
                "attached_containers": meta["attached_containers"],
                "status": meta["status"],
                "in_use": meta["in_use"],
            })
    else:
        default_homelab_vols = [
            ("vol-pg", "homelab_postgres_data", ["homelab_postgres"], "PostgreSQL 16 Databases (psm, pap_core, eternal_iot)"),
            ("vol-mysql", "homelab_mysql_data", ["homelab_mysql"], "MySQL 8.4 Databases (fadb, stock_umkm)"),
            ("vol-redis", "homelab_redis_data", ["homelab_redis"], "Redis 7 AOF Cache & Message Broker"),
            ("vol-minio", "homelab_minio_data", ["homelab_minio"], "MinIO / Silo S3 Object Storage Buckets"),
            ("vol-prom", "homelab_prometheus_data", ["homelab_prometheus"], "Prometheus 15-Day Metrics TSDB"),
            ("vol-graf", "homelab_grafana_data", ["homelab_grafana"], "Grafana Dashboards & Provisioned State"),
            ("vol-psm", "homelab_psm_storage", ["homelab_psm_api", "homelab_psm_agent"], "PSM Workspaces, Releases, Uploads & Backups"),
        ]
        for vid, vname, attached_c, vrole in default_homelab_vols:
            volumes_list.append({
                "id": vid,
                "name": vname,
                "driver": "local",
                "mountpoint": f"/var/lib/docker/volumes/{vname}/_data",
                "role": vrole,
                "attached_containers": attached_c,
                "status": "in-use",
                "in_use": True,
            })

    used_volumes_count = sum(1 for v in volumes_list if v["in_use"])
    unused_volumes_count = len(volumes_list) - used_volumes_count

    # PSM Storage Footprint (/opt/psm subdirectories + Docker images count)
    base_dir = settings.STORAGE_BASE_DIR
    ws_fp = _compute_dir_footprint(os.path.join(base_dir, "workspaces"))
    up_fp = _compute_dir_footprint(os.path.join(base_dir, "uploads"))
    bk_fp = _compute_dir_footprint(os.path.join(base_dir, "backups"))
    docker_images_count = db.query(DockerImage).filter(DockerImage.server_id == sid).count() if target_server else 0

    psm_footprint = {
        "base_dir": base_dir,
        "workspaces_bytes": ws_fp["bytes"],
        "workspaces_files": ws_fp["files"],
        "uploads_bytes": up_fp["bytes"],
        "uploads_files": up_fp["files"],
        "backups_bytes": bk_fp["bytes"],
        "backups_files": bk_fp["files"],
        "total_psm_bytes": ws_fp["bytes"] + up_fp["bytes"] + bk_fp["bytes"],
        "docker_images_count": docker_images_count,
    }

    minio_status = _probe_minio_status(db)

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
        "psm_footprint": psm_footprint,
        "minio": minio_status,
        "recorded_at": tele.get("recorded_at", datetime.now(timezone.utc).isoformat()),
    }


@router.post("/storage/s3/buckets")
def create_minio_s3_bucket(
    payload: S3BucketCreateRequest,
    current_user: User = Depends(require_role("owner", "admin", "operator")),
    db: Session = Depends(get_db),
):
    """Create and register an S3 bucket in Homelab MinIO / Silo Object Storage."""
    bucket = (payload.bucket_name or "").strip().lower()
    if not re.match(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", bucket) or ".." in bucket:
        raise HTTPException(
            status_code=400,
            detail="Nama bucket S3 harus 3-63 karakter (huruf kecil, angka, titik, atau tanda hubung).",
        )

    provisioned_live = False
    is_testing = os.getenv("TESTING", "").lower() == "true"
    root_user = os.getenv("MINIO_ROOT_USER", "admin")
    root_pass = os.getenv("MINIO_ROOT_PASSWORD", "admin12345")

    if not is_testing:
        for cname in ("homelab_minio", "minio"):
            try:
                anon_cmd = f" && mc anonymous set download local/{bucket}" if payload.public_read else ""
                cmd = [
                    "docker", "exec", cname, "sh", "-c",
                    f"mc alias set local http://127.0.0.1:9000 {root_user} {root_pass} >/dev/null 2>&1 "
                    f"&& mc mb --ignore-existing local/{bucket} >/dev/null 2>&1{anon_cmd}"
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if proc.returncode == 0:
                    provisioned_live = True
                    break
            except Exception:
                continue

    existing = _load_custom_s3_buckets()
    existing = [b for b in existing if str(b.get("name") or "").lower() != bucket]
    existing.append({
        "name": bucket,
        "public_read": payload.public_read,
        "purpose": "Custom Homelab Project Bucket",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_custom_s3_buckets(existing)
    minio_status = _probe_minio_status(db, force_refresh=True)

    return {
        "status": "created",
        "bucket_name": bucket,
        "public_read": payload.public_read,
        "provisioned_on_minio": provisioned_live,
        "s3_endpoint_internal": "http://minio:9000",
        "public_url_lan": f"http://{settings.SERVER_LAN_IP}:9000/{bucket}",
        "public_url_domain": f"http://s3.homelab/{bucket}",
        "minio": minio_status,
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
