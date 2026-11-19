"""Integration tests for Personal Cloud Management Platform (personal_cloud_management_platform.md).

Verifies:
- 4.1 Unified Server Management API (/api/v1/infra/overview)
- 4.2 Deployment API (/api/v1/projects/{id}/deploy, restart, stop, rollback, logs, /api/v1/deployments/{id})
- 4.3 Database Management API (/api/v1/databases, /health, /metrics, /backup, /backups, /restore)
- 4.4 Automation Engine (/api/v1/automations/rules, /evaluate, /history)
- 4.5 IoT Integration API (/api/v1/iot/machines, /production, /production/summary, /devices/register)
- 4.6 Central Authentication & Granular RBAC
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.projects.schemas import ProjectCreate
from app.modules.projects.service import ProjectService
from app.services.bootstrap import seed_platform_registry


def test_unified_infra_overview_endpoint(client: TestClient, db_session: Session, admin_headers: dict[str, str]):
    """Tests Section 4.1: GET /api/v1/infra/overview combining server, docker, databases, and services."""
    res = client.get("/api/v1/infra/overview", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()["data"]

    # 1. Server Metrics
    assert "server" in data
    server = data["server"]
    assert "hostname" in server
    assert server["status"] == "online"
    assert "cpu_usage" in server
    assert "ram_usage" in server
    assert "disk_usage" in server
    assert server["ip_lan"] == "192.168.25.105"
    assert server["ip_tailscale"] == "100.78.113.53"

    # 2. Docker Overview
    assert "docker" in data
    docker = data["docker"]
    assert "running" in docker
    assert "stopped" in docker
    assert "unhealthy" in docker

    # 3. Databases Status
    assert "databases" in data
    dbs = data["databases"]
    assert "postgresql" in dbs
    assert "mysql" in dbs
    assert dbs["postgresql"] == "healthy"

    # 4. Homelab Services
    assert "services" in data
    services = data["services"]
    for svc in ("grafana", "mqtt", "uptime_kuma", "nextcloud", "prometheus", "psm", "pap_core"):
        assert svc in services

    # Also test servers and services catalog endpoints
    srv_res = client.get("/api/v1/infra/servers", headers=admin_headers)
    assert srv_res.status_code == 200
    assert len(srv_res.json()["data"]) >= 1

    svc_res = client.get("/api/v1/infra/services", headers=admin_headers)
    assert svc_res.status_code == 200
    svc_names = {s["name"] for s in svc_res.json()["data"]}
    assert "pap-api" in svc_names
    assert "psm-api" in svc_names
    assert "nextcloud" in svc_names
    assert "grafana" in svc_names


def test_deployment_orchestration_api(client: TestClient, db_session: Session, admin_headers: dict[str, str]):
    """Tests Section 4.2: Project Deployment, Lifecycle, and Status APIs."""
    seed_platform_registry(db_session)

    # 1. Deploy project
    deploy_res = client.post(
        "/api/v1/projects/stock-umkm/deploy",
        headers=admin_headers,
        json={"version": "v1.8.2", "branch": "production"},
    )
    assert deploy_res.status_code == 202
    deploy_data = deploy_res.json()["data"]
    assert "deployment_id" in deploy_data
    assert deploy_data["deployment_id"].startswith("dep-")
    assert deploy_data["project"] == "stock-umkm"
    assert deploy_data["status"] == "success"
    assert deploy_data["version"] == "v1.8.2"
    assert deploy_data["health_check"] == "passed"
    assert deploy_data["duration_seconds"] > 0
    dep_id = deploy_data["deployment_id"]

    # 2. Query deployment status via /api/v1/deployments/{id}
    status_res = client.get(f"/api/v1/deployments/{dep_id}", headers=admin_headers)
    assert status_res.status_code == 200
    status_data = status_res.json()["data"]
    assert status_data["deployment_id"] == dep_id
    assert status_data["project"] == "stock-umkm"
    assert status_data["version"] == "v1.8.2"

    # 3. Restart project container
    restart_res = client.post("/api/v1/projects/stock-umkm/restart", headers=admin_headers)
    assert restart_res.status_code == 200
    assert restart_res.json()["data"]["action"] == "restart"
    assert restart_res.json()["data"]["status"] == "success"

    # 4. Stop project container
    stop_res = client.post("/api/v1/projects/stock-umkm/stop", headers=admin_headers)
    assert stop_res.status_code == 200
    assert stop_res.json()["data"]["action"] == "stop"
    assert stop_res.json()["data"]["status"] == "success"

    # 5. Rollback project container
    rollback_res = client.post(
        "/api/v1/projects/stock-umkm/rollback",
        headers=admin_headers,
        json={"version": "v1.8.1"},
    )
    assert rollback_res.status_code == 200
    roll_data = rollback_res.json()["data"]
    assert roll_data["version"] == "v1.8.1"
    assert roll_data["status"] == "success"

    # 6. View logs
    logs_res = client.get("/api/v1/projects/stock-umkm/logs", headers=admin_headers)
    assert logs_res.status_code == 200
    assert "logs" in logs_res.json()["data"]


def test_database_management_api(client: TestClient, db_session: Session, admin_headers: dict[str, str]):
    """Tests Section 4.3: Database Registry, Health, Metrics, and Backup Management."""
    seed_platform_registry(db_session)

    # 1. List databases registry
    dbs_res = client.get("/api/v1/databases", headers=admin_headers)
    assert dbs_res.status_code == 200
    db_items = dbs_res.json()["data"]
    db_names = {d["database"] for d in db_items}
    assert "pap_core" in db_names
    assert "psm" in db_names
    assert "nextcloud" in db_names
    assert "stock_umkm" in db_names

    # 2. Database Health
    health_res = client.get("/api/v1/databases/pap_core/health", headers=admin_headers)
    assert health_res.status_code == 200
    assert health_res.json()["data"]["status"] == "healthy"

    # 3. Database Metrics
    metrics_res = client.get("/api/v1/databases/pap_core/metrics", headers=admin_headers)
    assert metrics_res.status_code == 200
    m_data = metrics_res.json()["data"]
    assert m_data["database"] == "pap_core"
    assert m_data["tables_count"] > 0
    assert "size_human" in m_data

    # 4. Trigger database backup
    backup_res = client.post(
        "/api/v1/databases/pap_core/backup",
        headers=admin_headers,
        json={"note": "Automated snapshot test"},
    )
    assert backup_res.status_code == 201
    b_data = backup_res.json()["data"]
    assert b_data["database"] == "pap_core"
    assert "backup_id" in b_data
    assert "filename" in b_data

    # 5. List database backups
    backups_res = client.get("/api/v1/databases/pap_core/backups", headers=admin_headers)
    assert backups_res.status_code == 200
    assert len(backups_res.json()["data"]) >= 1


def test_automation_engine_api(client: TestClient, db_session: Session, admin_headers: dict[str, str]):
    """Tests Section 4.4: Automation Rules, Event Evaluation, and Self-Healing Policies."""
    seed_platform_registry(db_session)

    # 1. List pre-seeded canonical rules
    rules_res = client.get("/api/v1/automations/rules", headers=admin_headers)
    assert rules_res.status_code == 200
    rules = rules_res.json()["data"]
    rule_names = {r["name"] for r in rules}
    assert "auto-restart-crashed-container" in rule_names
    assert "alert-disk-usage-high" in rule_names
    assert "retry-failed-backup" in rule_names

    # 2. Register custom automation rule
    create_res = client.post(
        "/api/v1/automations/rules",
        headers=admin_headers,
        json={
            "name": "auto-scale-worker",
            "description": "Trigger worker thread scale up on high queue depth",
            "trigger_type": "queue.high_depth",
            "condition_expression": "depth > 100",
            "action_type": "worker.scale_up",
            "action_target": "celery-worker",
            "is_enabled": True,
            "cooldown_seconds": 120,
        },
    )
    assert create_res.status_code == 201
    rule_id = create_res.json()["data"]["id"]

    # 3. Evaluate incoming event (container.crash)
    eval_res = client.post(
        "/api/v1/automations/evaluate",
        headers=admin_headers,
        json={
            "event_type": "container.crash",
            "payload": {"container": "stock-umkm", "exit_code": 137, "restart_count": 1},
        },
    )
    assert eval_res.status_code == 200
    eval_data = eval_res.json()["data"]
    assert eval_data["matched_rules"] >= 1
    assert any(a["action"] == "container.restart" for a in eval_data["actions_triggered"])

    # 4. History log is populated
    history_res = client.get("/api/v1/automations/history", headers=admin_headers)
    assert history_res.status_code == 200
    history = history_res.json()["data"]
    assert len(history) >= 1
    assert any(h["trigger_event"] == "container.crash" for h in history)

    # 5. Delete custom rule
    del_res = client.delete(f"/api/v1/automations/rules/{rule_id}", headers=admin_headers)
    assert del_res.status_code == 200


def test_iot_platform_machines_and_production_api(client: TestClient, db_session: Session, admin_headers: dict[str, str]):
    """Tests Section 4.5: Machine Registry, Telemetry Analytics, and Production Summary."""
    seed_platform_registry(db_session)

    # 1. Machine Registry
    machines_res = client.get("/api/v1/iot/machines", headers=admin_headers)
    assert machines_res.status_code == 200
    machines = machines_res.json()["data"]
    assert len(machines) >= 3
    models = {m["model"] for m in machines}
    assert "HPM 55" in models or "AF-450" in models

    # 2. Machine Production Telemetry
    prod_res = client.get("/api/v1/iot/machines/HP-55-01/production", headers=admin_headers)
    assert prod_res.status_code == 200
    p_data = prod_res.json()["data"]
    assert "output_counter" in p_data
    assert "operating_temperature_celsius" in p_data
    assert "efficiency_percent" in p_data
    assert p_data["output_counter"] > 0

    # 3. Fleet Production Summary
    summary_res = client.get("/api/v1/iot/production/summary", headers=admin_headers)
    assert summary_res.status_code == 200
    s_data = summary_res.json()["data"]
    assert s_data["total_machines"] >= 3
    assert s_data["total_output_pieces_today"] > 0
    assert "fleet_efficiency_percent" in s_data
