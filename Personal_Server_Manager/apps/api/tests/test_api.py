import pytest
from fastapi.testclient import TestClient
from apps.api.app.main import app, init_db

@pytest.fixture(scope="session", autouse=True)
def setup_database():
    init_db()

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

def get_auth_token(client):
    login_res = client.post("/api/v1/auth/login", json={
        "email": "admin@psm.local",
        "password": "Admin12345!"
    })
    return login_res.json()["access_token"]

def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

def test_admin_login(client):
    res = client.post("/api/v1/auth/login", json={
        "email": "admin@psm.local",
        "password": "Admin12345!"
    })
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["user"]["email"] == "admin@psm.local"

def test_servers_list(client):
    token = get_auth_token(client)
    res = client.get("/api/v1/servers", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    servers = res.json()
    assert len(servers) >= 1
    server_names = [s["name"] for s in servers]
    assert "Homelab-Primary" in server_names

def test_create_server_and_agent_register(client):
    token = get_auth_token(client)
    # 1. Create server
    create_res = client.post(
        "/api/v1/servers",
        json={"name": "Worker-Node-01", "hostname": "worker01.local"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert create_res.status_code == 200
    server_info = create_res.json()
    server_id = server_info["server_id"]
    agent_token = server_info["agent_token"]

    # 2. Register agent
    reg_res = client.post(
        "/api/v1/agents/register",
        json={
            "server_id": server_id,
            "agent_token": agent_token,
            "agent_version": "0.1.0",
            "os_name": "Ubuntu Server",
            "architecture": "x86_64"
        }
    )
    assert reg_res.status_code == 200
    assert reg_res.json()["status"] == "registered"

    # 3. Heartbeat
    hb_res = client.post(
        "/api/v1/agents/heartbeat",
        json={"agent_id": "test-agent", "server_id": server_id, "status": "online"}
    )
    assert hb_res.status_code == 200

    # 4. Metrics
    met_res = client.post(
        "/api/v1/agents/metrics",
        json={
            "server_id": server_id,
            "cpu_usage": 15.2,
            "memory_usage": 44.0,
            "disk_usage": 35.0
        }
    )
    assert met_res.status_code == 200

    # 5. Inventory sync
    inv_res = client.post(
        "/api/v1/agents/inventory",
        json={
            "server_id": server_id,
            "containers": [
                {"docker_id": "abc123def456", "name": "test-nginx", "image": "nginx:alpine", "status": "running", "ports": []}
            ],
            "images": [
                {"id": "img123456789", "repository": "nginx", "tag": "alpine", "size": "42 MB", "created": "Today"}
            ],
            "volumes": [
                {"name": "test-vol", "driver": "local", "mountpoint": "/var/lib/docker/volumes/test-vol/_data"}
            ],
            "networks": [
                {"name": "bridge", "driver": "bridge", "scope": "local"}
            ]
        },
        headers={"x-agent-token": agent_token, "x-server-id": server_id}
    )
    assert inv_res.status_code == 200
    assert inv_res.json()["status"] == "synced"

    # 6. Rotate & Revoke agent token
    rot_res = client.post(
        f"/api/v1/servers/{server_id}/agent/rotate",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert rot_res.status_code == 200
    assert "agent_token" in rot_res.json()

    rev_res = client.post(
        f"/api/v1/servers/{server_id}/agent/revoke",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert rev_res.status_code == 200

def test_project_and_git_deployment(client):
    token = get_auth_token(client)
    # Get server
    servers = client.get("/api/v1/servers", headers={"Authorization": f"Bearer {token}"}).json()
    server_id = servers[0]["id"]

    # Create project
    proj_res = client.post(
        "/api/v1/projects",
        json={
            "server_id": server_id,
            "name": "sample-backend-api",
            "framework": "FastAPI",
            "source_type": "git",
            "repository_url": "https://github.com/example/api.git",
            "branch": "main"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert proj_res.status_code == 200
    project = proj_res.json()
    project_id = project["id"]

    # Trigger Git deployment
    dep_res = client.post(
        "/api/v1/deployments/git",
        json={"project_id": project_id, "branch": "main"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert dep_res.status_code == 200
    deployment = dep_res.json()
    assert deployment["status"] in ["preparing", "building", "queued"]
    assert deployment["version"] >= 1

def test_users_and_backups_crud(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Users CRUD
    u_create = client.post(
        "/api/v1/users",
        json={
            "name": "Homelab Operator",
            "email": "operator1@psm.local",
            "password": "OperatorPassword123!",
            "role": "operator"
        },
        headers=headers
    )
    assert u_create.status_code == 200
    user_id = u_create.json()["id"]

    u_list = client.get("/api/v1/users", headers=headers)
    assert u_list.status_code == 200
    assert any(u["id"] == user_id for u in u_list.json())

    u_update = client.patch(f"/api/v1/users/{user_id}", json={"role": "viewer"}, headers=headers)
    assert u_update.status_code == 200
    assert u_update.json()["role"] == "viewer"

    u_del = client.delete(f"/api/v1/users/{user_id}", headers=headers)
    assert u_del.status_code == 200

    # Backups CRUD
    b_create = client.post(
        "/api/v1/backups",
        json={"name": "Daily DB Snapshot", "backup_type": "database"},
        headers=headers
    )
    assert b_create.status_code == 200
    backup_id = b_create.json()["id"]

    b_list = client.get("/api/v1/backups", headers=headers)
    assert b_list.status_code == 200
    assert len(b_list.json()) >= 1

    b_restore = client.post(f"/api/v1/backups/{backup_id}/restore", headers=headers)
    assert b_restore.status_code == 200

    b_del = client.delete(f"/api/v1/backups/{backup_id}", headers=headers)
    assert b_del.status_code == 200

def test_docker_prune_and_github_webhook(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Prune
    servers = client.get("/api/v1/servers", headers=headers).json()
    server_id = servers[0]["id"]
    prune_res = client.post(f"/api/v1/servers/{server_id}/prune", headers=headers)
    assert prune_res.status_code == 200
    assert "job_id" in prune_res.json()

    # Webhook
    projects = client.get("/api/v1/projects", headers=headers).json()
    project_id = projects[0]["id"]
    wh_res = client.post(
        f"/api/v1/deployments/webhook/{project_id}",
        json={"ref": "refs/heads/main", "after": "a1b2c3d4e5f6"}
    )
    assert wh_res.status_code == 200
    assert wh_res.json()["status"] == "triggered"

def test_templates_catalog_and_deploy(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. List templates
    res = client.get("/api/v1/templates", headers=headers)
    assert res.status_code == 200
    templates = res.json()
    assert len(templates) >= 6
    template_ids = [t["id"] for t in templates]
    assert "vaultwarden" in template_ids
    assert "adguard-home" in template_ids
    assert "redis" in template_ids

    # 2. Get specific template
    vw = client.get("/api/v1/templates/vaultwarden", headers=headers)
    assert vw.status_code == 200
    assert vw.json()["name"] == "Vaultwarden"

    # 3. Deploy template
    servers = client.get("/api/v1/servers", headers=headers).json()
    server_id = servers[0]["id"]
    deploy_res = client.post(
        "/api/v1/templates/deploy",
        json={
            "server_id": server_id,
            "template_id": "redis",
            "project_name": "Homelab-Cache",
            "port": 6380
        },
        headers=headers
    )
    assert deploy_res.status_code == 200
    data = deploy_res.json()
    assert data["status"] == "dispatched"
    assert "project_id" in data
    assert "job_id" in data

def test_notifications_and_alerts(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Update settings
    upd_res = client.post(
        "/api/v1/notifications/settings",
        json={
            "enabled": True,
            "discord_webhook_url": "https://discord.com/api/webhooks/mock/123",
            "telegram_bot_token": "123456:ABC-DEF",
            "telegram_chat_id": "-10012345678",
            "cpu_threshold": 85.0
        },
        headers=headers
    )
    assert upd_res.status_code == 200
    assert upd_res.json()["settings"]["cpu_threshold"] == 85.0

    # 2. Get settings
    get_res = client.get("/api/v1/notifications/settings", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["enabled"] is True

    # 3. Test alert dispatch
    test_res = client.post("/api/v1/notifications/test", headers=headers)
    assert test_res.status_code == 200
    assert test_res.json()["status"] == "tested"

    # 4. View alerts
    alerts_res = client.get("/api/v1/notifications/alerts", headers=headers)
    assert alerts_res.status_code == 200
    assert len(alerts_res.json()) >= 1

def test_audit_export_and_backup_download(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Export CSV
    csv_res = client.get("/api/v1/audit-logs/export?format=csv", headers=headers)
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers.get("content-type", "")
    assert "ID,Timestamp" in csv_res.text

    # 2. Export JSON
    json_res = client.get("/api/v1/audit-logs/export?format=json", headers=headers)
    assert json_res.status_code == 200
    assert isinstance(json_res.json(), list)

    # 3. Create and download backup
    create_bk = client.post(
        "/api/v1/backups",
        json={"name": "test-export-snapshot", "backup_type": "database"},
        headers=headers
    )
    assert create_bk.status_code == 200
    bk_id = create_bk.json()["id"]

    dl_res = client.get(f"/api/v1/backups/{bk_id}/download", headers=headers)
    assert dl_res.status_code == 200
    assert len(dl_res.content) > 0

def test_container_inspect(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    servers = client.get("/api/v1/servers", headers=headers).json()
    server_id = servers[0]["id"]

    inspect_res = client.get(f"/api/v1/servers/{server_id}/containers/test-nginx/inspect", headers=headers)
    assert inspect_res.status_code == 200
    data = inspect_res.json()
    assert "state" in data
    assert "networks" in data
    assert "mounts" in data


def test_prometheus_storage_and_network_endpoints(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Prometheus OpenMetrics scrape endpoints
    prom_metrics = client.get("/metrics")
    assert prom_metrics.status_code == 200
    assert "psm_server_online" in prom_metrics.text
    assert "psm_disk_free_bytes" in prom_metrics.text

    # 2. Prometheus status & config
    prom_status = client.get("/api/v1/monitoring/prometheus/status", headers=headers)
    assert prom_status.status_code == 200
    assert "connected" in prom_status.json()

    prom_cfg = client.post(
        "/api/v1/monitoring/prometheus/config",
        json={"prometheus_url": "http://host.docker.internal:9001"},
        headers=headers,
    )
    assert prom_cfg.status_code == 200

    # 3. Storage telemetry (used vs unused + disk R/W)
    storage_res = client.get("/api/v1/monitoring/storage", headers=headers)
    assert storage_res.status_code == 200
    st_data = storage_res.json()
    assert "summary" in st_data
    assert "used_bytes" in st_data["summary"]
    assert "free_bytes" in st_data["summary"]
    assert "read_bytes_per_sec" in st_data["summary"]
    assert "write_bytes_per_sec" in st_data["summary"]
    assert "partitions" in st_data
    assert "volumes" in st_data

    # 4. Network telemetry (bandwidth, interfaces, listening ports)
    net_res = client.get("/api/v1/monitoring/network", headers=headers)
    assert net_res.status_code == 200
    net_data = net_res.json()
    assert "bandwidth" in net_data
    assert "interfaces" in net_data
    assert "listening_ports" in net_data
    assert "connection_stats" in net_data


def test_project_postgres_auto_provision(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    servers = client.get("/api/v1/servers", headers=headers).json()
    server_id = servers[0]["id"]

    # Create project with auto PostgreSQL provisioning
    proj_res = client.post(
        "/api/v1/projects",
        json={
            "server_id": server_id,
            "name": "Stock-UMKM",
            "framework": "Laravel",
            "source_type": "git",
            "repository_url": "https://github.com/this-is-asepjourney/stock_umkm",
            "branch": "main",
            "auto_provision_db": True,
        },
        headers=headers,
    )
    assert proj_res.status_code == 200
    proj = proj_res.json()
    project_id = proj["id"]

    # Check project database endpoint
    db_res = client.get(f"/api/v1/projects/{project_id}/database", headers=headers)
    assert db_res.status_code == 200
    db_data = db_res.json()
    assert db_data["db_name"] == "stock_umkm"
    assert "connection_url_internal" in db_data
    assert "env_full" in db_data
    assert db_data["env_full"]["DB_CONNECTION"] == "pgsql"
    assert db_data["env_full"]["DB_DATABASE"] == "stock_umkm"

    # Trigger manual database provision with custom name
    prov_res = client.post(
        f"/api/v1/projects/{project_id}/database/provision",
        json={"db_name": "stock_umkm_prod", "override_env": True},
        headers=headers,
    )
    assert prov_res.status_code == 200
    prov_data = prov_res.json()
    assert prov_data["db_name"] == "stock_umkm_prod"
    assert "DATABASE_URL" in prov_data["synced_env_keys"]

    # Verify project environment variables were automatically populated
    env_res = client.get(f"/api/v1/projects/{project_id}/environment", headers=headers)
    assert env_res.status_code == 200
    env_keys = [item["key"] for item in env_res.json()]
    assert "DATABASE_URL" in env_keys
    assert "DB_CONNECTION" in env_keys
    assert "POSTGRES_DB" in env_keys




def test_pap_main_api_and_iot_fleet_integration(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Check PAP Main API hub status from PSM
    status_res = client.get("/api/v1/pap/status", headers=headers)
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["connected"] is True
    assert status_data["main_api"]["name"] == "Personal API Platform"
    slugs = [p["slug"] for p in status_data["connected_projects"]]
    assert "personal-server-manager" in slugs
    assert "iot-dashboard" in slugs

    # 2. Trigger sync from PSM to PAP Main API
    sync_res = client.post("/api/v1/pap/sync", headers=headers)
    assert sync_res.status_code == 200
    assert sync_res.json()["synced"] is True
    assert sync_res.json()["servers_count"] >= 1

    # 3. Query Heatpress IoT Fleet via PAP Main API
    fleet_res = client.get("/api/v1/pap/iot/fleet", headers=headers)
    assert fleet_res.status_code == 200
    fleet_data = fleet_res.json()
    assert fleet_data["category"] == "Heatpress"
    assert fleet_data["summary"]["total_devices"] >= 3


def test_project_access_urls_and_sync_domain(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    srv_res = client.get("/api/v1/servers", headers=headers)
    assert srv_res.status_code == 200
    server_id = srv_res.json()[0]["id"]

    # Create project with explicit port_mappings and custom domain
    create_res = client.post(
        "/api/v1/projects",
        json={
            "server_id": server_id,
            "name": "pos-main",
            "framework": "Laravel",
            "source_type": "git",
            "repository_url": "https://github.com/example/pos-main.git",
            "port_mappings": "3100:80",
            "domain": "pos-main.homelab",
            "db_type": "none",
            "auto_deploy": False,
        },
        headers=headers,
    )
    assert create_res.status_code == 200
    proj = create_res.json()
    assert proj["domain"] == "pos-main.homelab"
    assert proj["server_ip"] == "192.168.25.105"
    assert proj["server_tailscale_ip"] == "100.78.113.53"
    assert proj["access_urls"]["domain"] == "http://pos-main.homelab"
    assert proj["access_urls"]["lan_ip"] == "http://192.168.25.105:3100"
    assert proj["access_urls"]["tailscale_ip"] == "http://100.78.113.53:3100"

    # Trigger single-project domain sync endpoint
    sync_dom_res = client.post(f"/api/v1/projects/{proj['id']}/sync-domain", headers=headers)
    assert sync_dom_res.status_code == 200
    sync_dom_data = sync_dom_res.json()
    assert sync_dom_data["domain"] == "pos-main.homelab"
    assert sync_dom_data["target_port"] == 3100
    assert sync_dom_data["access_urls"]["lan_ip"] == "http://192.168.25.105:3100"


def test_project_environment_crud_and_reveal(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    server_id = client.get("/api/v1/servers", headers=headers).json()[0]["id"]

    proj_res = client.post(
        "/api/v1/projects",
        json={
            "server_id": server_id,
            "name": "env-crud-app",
            "framework": "Laravel",
            "source_type": "git",
            "repository_url": "https://github.com/example/env-crud-app.git",
            "db_type": "none",
            "auto_provision_db": False,
            "auto_deploy": False,
            "environment_variables": [
                {"key": "APP_ENV", "value": "production"},
                {"key": "APP_SECRET", "value": "super_secret_token_987"},
            ],
        },
        headers=headers,
    )
    assert proj_res.status_code == 200
    pid = proj_res.json()["id"]

    # 1. Default (reveal=false) masks values and keeps plaintext value=None
    masked_res = client.get(f"/api/v1/projects/{pid}/environment", headers=headers)
    assert masked_res.status_code == 200
    masked_map = {item["key"]: item for item in masked_res.json()}
    assert masked_map["APP_SECRET"]["value"] is None
    assert masked_map["APP_SECRET"]["masked_value"] == "su****87"

    # 2. reveal=true returns decrypted plaintext values for owner/admin
    reveal_res = client.get(f"/api/v1/projects/{pid}/environment?reveal=true", headers=headers)
    assert reveal_res.status_code == 200
    reveal_map = {item["key"]: item for item in reveal_res.json()}
    assert reveal_map["APP_ENV"]["value"] == "production"
    assert reveal_map["APP_SECRET"]["value"] == "super_secret_token_987"

    # 3. Single-item upsert updates/adds without corrupting other existing secrets
    up_add = client.post(
        f"/api/v1/projects/{pid}/environment/item",
        json={"key": "APP_URL", "value": "http://100.78.113.53:3101"},
        headers=headers,
    )
    assert up_add.status_code == 200
    assert up_add.json()["key"] == "APP_URL"
    assert up_add.json()["value"] == "http://100.78.113.53:3101"

    up_edit = client.post(
        f"/api/v1/projects/{pid}/environment/item",
        json={"key": "APP_ENV", "value": "local", "original_key": "APP_ENV"},
        headers=headers,
    )
    assert up_edit.status_code == 200

    # Verify untouched APP_SECRET still has full plaintext value
    after_edit = client.get(f"/api/v1/projects/{pid}/environment?reveal=true", headers=headers).json()
    after_map = {item["key"]: item["value"] for item in after_edit}
    assert after_map == {
        "APP_ENV": "local",
        "APP_SECRET": "super_secret_token_987",
        "APP_URL": "http://100.78.113.53:3101",
    }

    # 4. Rename key via original_key cleanly removes old key and keeps APP_SECRET intact
    rename_res = client.post(
        f"/api/v1/projects/{pid}/environment/item",
        json={"key": "PUBLIC_BASE_URL", "value": "http://env-crud-app.homelab", "original_key": "APP_URL"},
        headers=headers,
    )
    assert rename_res.status_code == 200
    rename_map = {
        i["key"]: i["value"]
        for i in client.get(f"/api/v1/projects/{pid}/environment?reveal=true", headers=headers).json()
    }
    assert "APP_URL" not in rename_map
    assert rename_map["PUBLIC_BASE_URL"] == "http://env-crud-app.homelab"
    assert rename_map["APP_SECRET"] == "super_secret_token_987"

    # 5. Delete single key removes only that key
    del_res = client.delete(f"/api/v1/projects/{pid}/environment/PUBLIC_BASE_URL", headers=headers)
    assert del_res.status_code == 200
    post_del_map = {
        i["key"]: i["value"]
        for i in client.get(f"/api/v1/projects/{pid}/environment?reveal=true", headers=headers).json()
    }
    assert post_del_map == {"APP_ENV": "local", "APP_SECRET": "super_secret_token_987"}

    # 6. Bulk PUT replaces set cleanly
    bulk_res = client.put(
        f"/api/v1/projects/{pid}/environment",
        json={
            "variables": [
                {"key": "APP_ENV", "value": "staging"},
                {"key": "LOG_LEVEL", "value": "debug"},
            ]
        },
        headers=headers,
    )
    assert bulk_res.status_code == 200
    assert bulk_res.json()["count"] == 2
    bulk_map = {
        i["key"]: i["value"]
        for i in client.get(f"/api/v1/projects/{pid}/environment?reveal=true", headers=headers).json()
    }
    assert bulk_map == {"APP_ENV": "staging", "LOG_LEVEL": "debug"}


def test_project_database_connection_and_overview(client):
    token = get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    server_id = client.get("/api/v1/servers", headers=headers).json()[0]["id"]

    proj_res = client.post(
        "/api/v1/projects",
        json={
            "server_id": server_id,
            "name": "umkm-storefront",
            "framework": "Laravel",
            "source_type": "git",
            "repository_url": "https://github.com/example/umkm-storefront.git",
            "db_type": "mysql",
            "database_name": "umkm_conn_test",
            "auto_provision_db": True,
            "auto_deploy": False,
        },
        headers=headers,
    )
    assert proj_res.status_code == 200
    pid = proj_res.json()["id"]

    # Check per-project database endpoint returns deployment_connection & connected_projects
    db_res = client.get(f"/api/v1/projects/{pid}/database", headers=headers)
    assert db_res.status_code == 200
    db_data = db_res.json()
    assert db_data["db_type"] == "mysql"
    assert db_data["db_name"] == "umkm_conn_test"

    dep_conn = db_data["deployment_connection"]
    for field in (
        "status",
        "is_connected",
        "summary",
        "container_exists",
        "container_running",
        "env_db_name",
        "env_matches_database",
        "active_db_sessions",
    ):
        assert field in dep_conn
    assert dep_conn["env_db_name"] == "umkm_conn_test"
    assert dep_conn["env_matches_database"] is True

    connected = db_data["connected_projects"]
    assert any(cp["id"] == pid and cp["is_current_project"] is True for cp in connected)

    # Check global /databases/overview maps umkm_conn_test to the deployed project record
    ov_res = client.get("/api/v1/projects/databases/overview", headers=headers)
    assert ov_res.status_code == 200
    ov_data = ov_res.json()
    assert ov_data["summary"]["total_databases"] >= 1
    matching_dbs = [
        d for d in ov_data["databases"] if d["db_type"] == "mysql" and d["db_name"] == "umkm_conn_test"
    ]
    assert len(matching_dbs) == 1
    assert any(cp["id"] == pid for cp in matching_dbs[0]["connected_projects"])
