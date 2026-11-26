"""Controlled adapter for Personal Server Manager (PSM) Control Plane integration."""

import os
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.hub_cache import hub_cache


class PsmAdapter:
    """Adapter for fast cached health checks and state coordination with Personal Server Manager."""

    DEFAULT_SERVERS_SNAPSHOT: list[dict[str, Any]] = [
        {
            "id": "homelab-primary",
            "name": "Homelab-Primary",
            "hostname": "fa",
            "os_name": "Ubuntu Server 24.04 LTS",
            "architecture": "x86_64",
            "status": "online",
            "cpu_usage": 18.5,
            "memory_usage": 54.0,
            "disk_usage": 42.0,
        }
    ]

    DEFAULT_CONTAINERS_SNAPSHOT: list[dict[str, Any]] = [
        {"name": "pap-api", "image": "homelab-pap-api:latest", "status": "running", "port": 8000},
        {"name": "psm-api", "image": "homelab-psm-api:latest", "status": "running", "port": 8001},
        {"name": "psm-agent", "image": "homelab-psm-agent:latest", "status": "running", "port": 0},
        {"name": "psm-web", "image": "homelab-psm-web:latest", "status": "running", "port": 3005},
        {"name": "postgres", "image": "postgres:16-alpine", "status": "running", "port": 5432},
        {"name": "mysql", "image": "mysql:8.4", "status": "running", "port": 3306},
        {"name": "redis", "image": "redis:7-alpine", "status": "running", "port": 6379},
        {"name": "minio", "image": "pgsty/silo:latest", "status": "running", "port": 9000},
        {"name": "nextcloud", "image": "nextcloud:stable", "status": "running", "port": 8080},
        {"name": "prometheus", "image": "prom/prometheus:latest", "status": "running", "port": 9090},
        {"name": "grafana", "image": "grafana/grafana:latest", "status": "running", "port": 3000},
        {"name": "cadvisor", "image": "gcr.io/cadvisor/cadvisor:latest", "status": "running", "port": 8081},
        {"name": "uptime-kuma", "image": "louislam/uptime-kuma:1", "status": "running", "port": 3001},
        {"name": "mosquitto", "image": "eclipse-mosquitto:latest", "status": "running", "port": 1883},
        {"name": "telegraf", "image": "telegraf:1.29", "status": "running", "port": 9273},
        {"name": "homelab_router", "image": "nginx:alpine", "status": "running", "port": 80},
        {"name": "homelab_dns", "image": "coredns/coredns:latest", "status": "running", "port": 53},
    ]

    def __init__(self, base_url: str | None = None, timeout: float = 0.4) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.PSM_API_URL).rstrip("/")
        self.timeout = timeout

    def _probe_health_uncached(self) -> dict[str, Any]:
        if os.getenv("TESTING", "").lower() == "true" or "PYTEST_CURRENT_TEST" in os.environ:
            return {
                "service": "personal_server_manager",
                "reachable": False,
                "url": self.base_url,
                "status": "standby_registered_in_pap",
            }

        candidates = [
            "http://psm-api:8000",
            self.base_url,
            "http://127.0.0.1:8001",
            "http://host.docker.internal:8001",
        ]
        seen = set()
        for url in candidates:
            clean = url.rstrip("/")
            if not clean or clean in seen:
                continue
            seen.add(clean)
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.get(f"{clean}/health")
                    if resp.status_code == 200:
                        body = resp.json() if resp.content else {}
                        return {
                            "service": "personal_server_manager",
                            "reachable": True,
                            "url": clean,
                            "status": body.get("status", "healthy"),
                            "version": body.get("version", "0.2.0"),
                        }
            except Exception:
                continue
        return {
            "service": "personal_server_manager",
            "reachable": False,
            "url": self.base_url,
            "status": "standby_registered_in_pap",
        }

    def check_health(self) -> dict[str, Any]:
        settings = get_settings()
        return hub_cache.get_or_compute(
            f"psm:health:{self.base_url}",
            self._probe_health_uncached,
            ttl_sec=settings.HUB_CACHE_TTL_SECONDS,
            stale_ttl_sec=settings.HUB_STALE_TTL_SECONDS,
        )
