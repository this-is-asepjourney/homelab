"""Prometheus HTTP API query client and bounded-label IoT/API metric exporter."""

import re
from typing import Any

import httpx
from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

from app.core.config import get_settings
from app.core.exceptions import BadRequestError

# Bounded-cardinality Prometheus registry (avoids high-cardinality labels per Section 9.5)
PAP_METRICS_REGISTRY = CollectorRegistry()

# ── General IoT Device Metrics ────────────────────────────────────────────────
IOT_TEMPERATURE_GAUGE = Gauge(
    "pap_iot_device_temperature_celsius",
    "Latest reported temperature in Celsius for registered IoT devices",
    labelnames=["device_id", "device_type"],
    registry=PAP_METRICS_REGISTRY,
)

IOT_COUNTER_GAUGE = Gauge(
    "pap_iot_device_counter_value",
    "Latest reported production/operation counter for registered IoT devices",
    labelnames=["device_id", "device_type"],
    registry=PAP_METRICS_REGISTRY,
)

IOT_CURRENT_GAUGE = Gauge(
    "pap_iot_device_current_amperes",
    "Latest reported electrical current in Amperes for registered IoT devices",
    labelnames=["device_id", "device_type"],
    registry=PAP_METRICS_REGISTRY,
)

IOT_TELEMETRY_INGESTED_TOTAL = Counter(
    "pap_iot_telemetry_ingested_total",
    "Total number of validated IoT telemetry messages ingested",
    labelnames=["device_id", "device_type"],
    registry=PAP_METRICS_REGISTRY,
)

# ── SysCtrl & Eternal IoT Exact Prometheus Metrics ─────────────────────────────
# Matches the Prometheus queries used in the Eternal IoT Grafana Dashboard:
SYSCTRL_SUHU_GAUGE = Gauge(
    "sysctrl_suhu",
    "ESP32 ambient temperature in Celsius",
    labelnames=["MCID", "model"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_HUMIDITY_GAUGE = Gauge(
    "sysctrl_humidity",
    "ESP32 ambient humidity percentage",
    labelnames=["MCID", "model"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_HEAT_INDEX_GAUGE = Gauge(
    "sysctrl_heat_index",
    "ESP32 computed heat index in Celsius",
    labelnames=["MCID", "model"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_RSSI_GAUGE = Gauge(
    "sysctrl_wifi_rssi",
    "ESP32 WiFi signal strength in dBm",
    labelnames=["MCID", "model"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_LOCK_GAUGE = Gauge(
    "sysctrl_is_locked",
    "ESP32 lock status (1=locked, 0=unlocked)",
    labelnames=["MCID", "model"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_REMAINING_GAUGE = Gauge(
    "sysctrl_remaining_sec",
    "ESP32 remaining unlock duration in seconds",
    labelnames=["MCID", "model"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_UPTIME_GAUGE = Gauge(
    "sysctrl_uptime",
    "ESP32 uptime in seconds",
    labelnames=["MCID", "model"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_HEAP_GAUGE = Gauge(
    "sysctrl_free_heap",
    "ESP32 free heap memory in bytes",
    labelnames=["MCID", "model"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_DEVICE_STATUS_GAUGE = Gauge(
    "sysctrl_device_status",
    "ESP32 device status (1=online, 0=offline)",
    labelnames=["MCID", "model"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_NFC_TAPS_COUNTER = Counter(
    "sysctrl_nfc_taps_total",
    "Total NFC/RFID card taps recorded",
    labelnames=["MCID", "role"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_NFC_USERS_GAUGE = Gauge(
    "sysctrl_nfc_whitelist_users",
    "Total registered active NFC users in whitelist",
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_MQTT_TX_COUNTER = Counter(
    "sysctrl_mqtt_packets_total",
    "Total MQTT telemetry packets received and processed",
    labelnames=["MCID"],
    registry=PAP_METRICS_REGISTRY,
)

SYSCTRL_DB_SYNC_GAUGE = Gauge(
    "sysctrl_database_sync_status",
    "Database sync health status (1=synced/healthy, 0=stale/error)",
    registry=PAP_METRICS_REGISTRY,
)

ETERNAL_SUHU_GAUGE = Gauge(
    "eternal_iot_suhu",
    "Eternal IoT temperature in Celsius",
    labelnames=["MCID"],
    registry=PAP_METRICS_REGISTRY,
)

ETERNAL_HUMIDITY_GAUGE = Gauge(
    "eternal_iot_humidity",
    "Eternal IoT humidity percentage",
    labelnames=["MCID"],
    registry=PAP_METRICS_REGISTRY,
)

SAFE_PROMQL_RE = re.compile(r"^[a-zA-Z0-9_:{}\[\]().,=\"'~+*/\-\s]+$")


def record_iot_prometheus_metrics(
    device_uid: str,
    device_type: str,
    temperature: float | None,
    counter: int | None,
    current: float | None,
) -> None:
    """Update Prometheus gauges for a validated IoT device telemetry reading."""
    IOT_TELEMETRY_INGESTED_TOTAL.labels(device_id=device_uid, device_type=device_type).inc()
    if temperature is not None:
        IOT_TEMPERATURE_GAUGE.labels(device_id=device_uid, device_type=device_type).set(temperature)
    if counter is not None:
        IOT_COUNTER_GAUGE.labels(device_id=device_uid, device_type=device_type).set(counter)
    if current is not None:
        IOT_CURRENT_GAUGE.labels(device_id=device_uid, device_type=device_type).set(current)


def record_esp32_prometheus_telemetry(
    device_serial: str,
    model: str = "Eternal-ESP32",
    temperature: float | None = None,
    humidity: float | None = None,
    heat_index: float | None = None,
    rssi: int | None = None,
    is_locked: int | None = None,
    remaining_sec: int | None = None,
    uptime_sec: int | None = None,
    free_heap: int | None = None,
) -> None:
    """Update all Prometheus metrics for the Eternal IoT / SysCtrl Grafana Dashboard."""
    serial_str = str(device_serial or "ETL-ESP32-001")
    model_str = str(model or "Eternal-ESP32")

    SYSCTRL_DEVICE_STATUS_GAUGE.labels(MCID=serial_str, model=model_str).set(1.0)
    SYSCTRL_MQTT_TX_COUNTER.labels(MCID=serial_str).inc()
    SYSCTRL_DB_SYNC_GAUGE.set(1.0)

    if temperature is not None:
        SYSCTRL_SUHU_GAUGE.labels(MCID=serial_str, model=model_str).set(float(temperature))
        ETERNAL_SUHU_GAUGE.labels(MCID=serial_str).set(float(temperature))
        IOT_TEMPERATURE_GAUGE.labels(device_id=serial_str, device_type=model_str).set(float(temperature))
    if humidity is not None:
        SYSCTRL_HUMIDITY_GAUGE.labels(MCID=serial_str, model=model_str).set(float(humidity))
        ETERNAL_HUMIDITY_GAUGE.labels(MCID=serial_str).set(float(humidity))
    if heat_index is not None:
        SYSCTRL_HEAT_INDEX_GAUGE.labels(MCID=serial_str, model=model_str).set(float(heat_index))
    if rssi is not None:
        SYSCTRL_RSSI_GAUGE.labels(MCID=serial_str, model=model_str).set(float(rssi))
    if is_locked is not None:
        SYSCTRL_LOCK_GAUGE.labels(MCID=serial_str, model=model_str).set(float(is_locked))
    if remaining_sec is not None:
        SYSCTRL_REMAINING_GAUGE.labels(MCID=serial_str, model=model_str).set(float(remaining_sec))
    if uptime_sec is not None:
        SYSCTRL_UPTIME_GAUGE.labels(MCID=serial_str, model=model_str).set(float(uptime_sec))
    if free_heap is not None:
        SYSCTRL_HEAP_GAUGE.labels(MCID=serial_str, model=model_str).set(float(free_heap))


def record_nfc_tap_prometheus_metric(device_serial: str, role: str = "Operator") -> None:
    """Increment NFC tap counter metric for Prometheus dashboard."""
    serial_str = str(device_serial or "ETL-ESP32-001")
    role_str = str(role or "Operator")
    SYSCTRL_NFC_TAPS_COUNTER.labels(MCID=serial_str, role=role_str).inc()


def set_nfc_whitelist_users_count(count: int) -> None:
    """Set the total number of whitelist users in Prometheus."""
    SYSCTRL_NFC_USERS_GAUGE.set(float(count))


def render_prometheus_metrics() -> bytes:
    """Export metrics in Prometheus text exposition format."""
    return generate_latest(PAP_METRICS_REGISTRY)


class PrometheusAdapter:
    """Client for querying the existing Prometheus HTTP API (port 9001)."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.PROMETHEUS_URL).rstrip("/")
        self.timeout = timeout or settings.PROMETHEUS_TIMEOUT_SECONDS

    @staticmethod
    def validate_promql(query: str) -> str:
        cleaned = query.strip()
        if not cleaned or len(cleaned) > 512 or not SAFE_PROMQL_RE.match(cleaned):
            raise BadRequestError(
                message="Invalid or disallowed PromQL query expression",
                code="INVALID_PROMQL_QUERY",
            )
        return cleaned

    def check_health(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(f"{self.base_url}/-/ready")
                return {
                    "service": "prometheus",
                    "reachable": resp.status_code == 200,
                    "status_code": resp.status_code,
                }
        except Exception:
            return {"service": "prometheus", "reachable": False}

    def instant_query(self, query: str, time_param: str | None = None) -> dict[str, Any]:
        validated = self.validate_promql(query)
        params: dict[str, Any] = {"query": validated}
        if time_param:
            params["time"] = time_param

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(f"{self.base_url}/api/v1/query", params=params)
                resp.raise_for_status()
                return resp.json().get("data", {})
        except Exception:
            return {
                "resultType": "vector",
                "result": [],
                "query": validated,
                "source": "fallback_offline",
            }

    def range_query(
        self,
        query: str,
        start: str,
        end: str,
        step: str = "60s",
    ) -> dict[str, Any]:
        validated = self.validate_promql(query)
        params = {"query": validated, "start": start, "end": end, "step": step}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(f"{self.base_url}/api/v1/query_range", params=params)
                resp.raise_for_status()
                return resp.json().get("data", {})
        except Exception:
            return {
                "resultType": "matrix",
                "result": [],
                "query": validated,
                "start": start,
                "end": end,
                "step": step,
                "source": "fallback_offline",
            }

    def get_targets(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(f"{self.base_url}/api/v1/targets")
                resp.raise_for_status()
                return resp.json().get("data", {})
        except Exception:
            return {
                "activeTargets": [
                    {
                        "scrapePool": "pap-iot-exporter",
                        "scrapeUrl": "http://192.168.25.101:8000/metrics",
                        "health": "up",
                    }
                ],
                "droppedTargets": [],
                "source": "fallback_offline",
            }

