"""Service for monitoring homelab infrastructure and PAP operational state with parallel cached probes."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.hub_cache import hub_cache
from app.integrations.minio import MinioS3Adapter
from app.integrations.mqtt import MqttAdapter
from app.integrations.prometheus import PrometheusAdapter
from app.integrations.psm import PsmAdapter
from app.integrations.stock_umkm import StockUmkmAdapter
from app.modules.monitoring.repository import MonitoringRepository


class MonitoringService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = MonitoringRepository(db)
        self.settings = get_settings()

    def get_services_status(self) -> list[dict[str, Any]]:
        db_ok = False
        try:
            self.db.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False

        def _probe_upstreams() -> dict[str, dict[str, Any]]:
            return hub_cache.run_parallel(
                {
                    "prom": lambda: PrometheusAdapter().check_health(),
                    "mqtt": lambda: MqttAdapter().check_health(),
                    "stock": lambda: StockUmkmAdapter().check_health(),
                    "psm": lambda: PsmAdapter().check_health(),
                    "minio": lambda: MinioS3Adapter().check_health(),
                },
                timeout=1.5,
            )

        upstreams = hub_cache.get_or_compute(
            "monitoring:upstreams",
            _probe_upstreams,
            ttl_sec=self.settings.HUB_CACHE_TTL_SECONDS,
            stale_ttl_sec=self.settings.HUB_STALE_TTL_SECONDS,
        )

        prom_status = upstreams.get("prom", {})
        mqtt_status = upstreams.get("mqtt", {})
        stock_status = upstreams.get("stock", {})
        psm_status = upstreams.get("psm", {})
        minio_status = upstreams.get("minio", {})

        return [
            {
                "name": "pap_core_database",
                "status": "healthy" if db_ok else "unhealthy",
                "reachable": db_ok,
            },
            {
                "name": "personal_server_manager",
                "port": 8001,
                **psm_status,
            },
            {
                "name": "minio_s3_vault",
                "port": 9000,
                **minio_status,
            },
            {
                "name": "iot_dashboard_fleet",
                "port": 9273,
                "status": "healthy",
                "reachable": True,
            },
            {
                "name": "prometheus",
                "port": 9090,
                "status": "healthy" if prom_status.get("reachable") else "standby",
                **prom_status,
            },
            {
                "name": "mosquitto_mqtt",
                "port": 1883,
                **mqtt_status,
            },
            {
                "name": "stock_umkm_adapter",
                **stock_status,
            },
        ]

    def get_overview(self) -> dict[str, Any]:
        devices_total, devices_online = self.repo.count_devices()
        return {
            "projects_total": self.repo.count_projects(),
            "active_clients_total": self.repo.count_active_clients(),
            "devices_total": devices_total,
            "devices_online": devices_online,
            "webhooks_active": self.repo.count_active_webhooks(),
            "services": self.get_services_status(),
        }
