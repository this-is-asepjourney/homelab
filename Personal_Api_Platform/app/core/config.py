"""Application configuration and secret reference resolution."""

from functools import lru_cache
import os
from typing import Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "Personal API Platform"
    APP_ENV: str = "development"
    APP_DEBUG: bool = False
    APP_VERSION: str = "1.0.0"

    API_PREFIX: str = "/api/v1"
    HOST_IP: str = "192.168.25.105"
    SERVER_LAN_IP: str = "192.168.25.105"
    SERVER_TS_IP: str = "100.78.113.53"
    PORT: int = 8000

    # Dedicated PAP core database on Homelab PostgreSQL (port 5432)
    PAP_DATABASE_URL: str = (
        "postgresql+psycopg://fadb:wokeh123@192.168.25.105:5432/pap_core"
    )
    ALLOW_PSM_AS_PAP_CORE: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 10
    DB_CONNECT_TIMEOUT: int = 2

    # Central Hub Responsiveness & Caching
    HUB_CACHE_TTL_SECONDS: float = 10.0
    HUB_STALE_TTL_SECONDS: float = 60.0
    PROBE_TIMEOUT_SECONDS: float = 0.45
    BACKUP_STORAGE_DIR: str = "/opt/psm/pap_backups"

    # Homelab PostgreSQL reference (Personal Server Manager, Nextcloud & Eternal IoT)
    POSTGRES_HOST: str = "192.168.25.105"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "fadb"
    POSTGRES_PASSWORD: str = "wokeh123"
    IOT_POSTGRES_DB: str = "eternal_iot"
    NEXTCLOUD_DB_NAME: str = "nextcloud"
    PSM_DATABASE_HOST: str = "192.168.25.105"
    PSM_DATABASE_PORT: int = 5432
    PSM_DATABASE_NAME: str = "psm"
    PSM_DATABASE_USER: str = "fadb"
    PSM_DATABASE_PASSWORD: str = "wokeh123"

    # Homelab Redis Cache & Broker reference
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379

    # Homelab MySQL reference (fadb / stock_umkm)
    STOCK_DB_HOST: str = "192.168.25.105"
    STOCK_DB_PORT: int = 3306
    STOCK_DB_NAME: str = "fadb"
    STOCK_DB_USER: str = "fadb"
    STOCK_DB_PASSWORD: str = "wokeh123"
    STOCK_UMKM_API_URL: str = "http://192.168.25.105:8000/api"
    STOCK_UMKM_API_KEY: str = "pap_key_stock_umkm_default_secret_key_2026"

    # POS Cashier integration reference
    POS_CASHIER_WEBHOOK_URL: str = "http://192.168.25.105:3000/api/webhooks/pap"

    # Connected Homelab Projects (PSM & IoT Dashboard) via PAP Main API
    PSM_API_URL: str = "http://192.168.25.105:8001"
    IOT_DASHBOARD_API_URL: str = "http://192.168.25.105:8088"
    PAP_PSM_SERVICE_KEY: str = "pap_key_psm_control_plane_default_secret_key_2026"
    PAP_IOT_SERVICE_KEY: str = "pap_key_iot_dashboard_default_secret_key_2026"
    ONLINE_THRESHOLD_MINUTES: int = 5

    # Authentication & JWT
    JWT_SECRET: str = "dev-only-jwt-secret-key-change-in-production-32b"
    JWT_ISSUER: str = "personal-api-platform"
    JWT_AUDIENCE: str = "pap-clients"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # InfluxDB v2 Settings (Dedicated IoT Telemetry TSDB)
    INFLUXDB_HOST: str = "influxdb"
    INFLUXDB_PORT: int = 8086
    INFLUXDB_URL: str = "http://influxdb:8086"
    INFLUXDB_USER: str = "appuser"
    INFLUXDB_PASSWORD: str = "admin12345"
    INFLUXDB_ORG: str = "homelab"
    INFLUXDB_BUCKET: str = "eternal_telemetry"
    INFLUXDB_TOKEN: str = "homelab-influxdb-master-auth-token-2026"

    # MinIO / Silo S3 Object Storage Settings
    MINIO_HOST: str = "minio"
    MINIO_PORT: int = 9000
    MINIO_CONSOLE_PORT: int = 9002
    MINIO_URL: str = "http://minio:9000"
    MINIO_PUBLIC_URL: str = "http://192.168.25.105:9000"
    MINIO_CONSOLE_URL: str = "http://192.168.25.105:9002"
    MINIO_ROOT_USER: str = "admin"
    MINIO_ROOT_PASSWORD: str = "admin12345"
    MINIO_DEFAULT_BUCKET: str = "homelab-storage"
    MINIO_BACKUP_BUCKET: str = "backups"

    # Prometheus & Monitoring (port 9090 per docker-compose.yml)
    PROMETHEUS_URL: str = "http://192.168.25.105:9090"
    PROMETHEUS_TIMEOUT_SECONDS: float = 1.5

    # Mosquitto MQTT (port 1883 per docker-homelab-setup.md)
    MQTT_HOST: str = "192.168.25.105"
    MQTT_TAILSCALE_HOST: str = "100.78.113.53"
    MQTT_PORT: int = 1883
    MQTT_USERNAME: str = "useriot"
    MQTT_PASSWORD: str = "25022002"
    MQTT_ENABLED: bool = True

    # Docker Executor (disabled by default in v1.0 per blueprint section 7.3 & 10.3)
    DOCKER_EXECUTOR_ENABLED: bool = False
    DOCKER_EXECUTOR_URL: str = "http://127.0.0.1:2375"

    # Security & Rate limiting
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 120
    CORS_ORIGINS: str = Field(
        default=(
            "http://192.168.25.105,http://192.168.25.105:3000,http://192.168.25.105:3001,"
            "http://192.168.25.105:3005,http://192.168.25.105:8000,http://192.168.25.105:8001,"
            "http://192.168.25.105:8080,http://192.168.25.105:8081,http://192.168.25.105:8088,http://192.168.25.105:9090,"
            "http://100.78.113.53,http://100.78.113.53:3000,http://100.78.113.53:3001,"
            "http://100.78.113.53:3005,http://100.78.113.53:8000,http://100.78.113.53:8001,"
            "http://100.78.113.53:8080,http://100.78.113.53:8081,http://100.78.113.53:8088,http://100.78.113.53:9090,"
            "http://localhost:3000,http://localhost:3001,http://localhost:3005,http://localhost:8000,http://localhost:8001,http://localhost:8080,http://localhost:8088,"
            "http://127.0.0.1:3000,http://127.0.0.1:3005,http://127.0.0.1:8000,http://127.0.0.1:8001,http://127.0.0.1:8088"
        )
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]

    @field_validator("STOCK_DB_USER")
    @classmethod
    def reject_mysql_root_in_production(cls, v: str, info) -> str:
        env = info.data.get("APP_ENV", "development")
        if env == "production" and v.strip().lower() == "root":
            raise ValueError(
                "PAP must not use MySQL 'root' as its runtime identity in production."
            )
        return v

    def validate_core_database_target(self) -> None:
        """Ensure PAP migrations or runtime do not accidentally target 'psm'."""
        parsed = urlparse(self.PAP_DATABASE_URL)
        db_name = (parsed.path or "").lstrip("/")
        if db_name == "psm" and not self.ALLOW_PSM_AS_PAP_CORE:
            raise RuntimeError(
                "Refusing to use 'psm' as PAP_DATABASE_URL without explicit confirmation "
                "(ALLOW_PSM_AS_PAP_CORE=true). Provision 'pap_core' instead."
            )

    def resolve_secret_reference(self, secret_reference: Optional[str]) -> Optional[str]:
        """Resolve a secret reference name from environment variables or settings without storing plaintext in DB."""
        if not secret_reference:
            return None
        ref = secret_reference.strip()
        if ref in os.environ:
            return os.environ[ref]
        if hasattr(self, ref):
            val = getattr(self, ref)
            return str(val) if val is not None else None
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


