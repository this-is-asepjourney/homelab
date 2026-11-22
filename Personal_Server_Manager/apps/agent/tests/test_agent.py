import pytest
import os
import zipfile
from apps.agent.app.collectors.metrics import get_metrics_snapshot, get_system_info
from apps.agent.app.jobs.handlers import ALLOWED_JOB_TYPES, handle_server_ping
from apps.agent.app.deployment.builder import DeploymentEngine

def test_system_and_metrics_collector():
    sys_info = get_system_info()
    assert "os_name" in sys_info
    assert "hostname" in sys_info

    metrics = get_metrics_snapshot()
    assert "cpu_usage" in metrics
    assert "memory_usage" in metrics
    assert "disk_usage" in metrics
    assert 0 <= metrics["cpu_usage"] <= 100
    assert 0 <= metrics["memory_usage"] <= 100

def test_allowed_job_types():
    assert "server.ping" in ALLOWED_JOB_TYPES
    assert "container.start" in ALLOWED_JOB_TYPES
    assert "system.prune" in ALLOWED_JOB_TYPES
    assert "deployment.git" in ALLOWED_JOB_TYPES
    assert "arbitrary.shell" not in ALLOWED_JOB_TYPES

def test_write_env_file(tmp_path):
    DeploymentEngine.write_env_file(str(tmp_path), {"PORT": "8080", "NODE_ENV": "production"})
    env_file = tmp_path / ".env"
    assert env_file.exists()
    content = env_file.read_text()
    assert "PORT=8080" in content
    assert "NODE_ENV=production" in content

def test_server_ping_handler():
    logs = []
    res = handle_server_ping({}, lambda level, msg: logs.append((level, msg)))
    assert res["status"] == "pong"
    assert len(logs) == 1

def test_safe_zip_extraction(tmp_path):
    zip_path = tmp_path / "test.zip"
    extract_dir = tmp_path / "extracted"

    # Create benign zip
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("index.js", "console.log('hello world');")

    success = DeploymentEngine.safe_extract_zip(str(zip_path), str(extract_dir))
    assert success is True
    assert (extract_dir / "index.js").exists()

def test_deployment_port_extraction_and_env(tmp_path):
    df_path = tmp_path / "Dockerfile"
    df_path.write_text("FROM alpine\nEXPOSE 3000\nCMD ['echo', 'hi']\n")

    logs = []
    args = DeploymentEngine._extract_port_args(str(df_path), str(tmp_path), lambda lvl, msg: logs.append(msg))
    assert "-p" in args
    assert "3000:3000" in args

    # Check env override
    env_file = tmp_path / ".env"
    env_file.write_text("PORT=8085\n")
    args_env = DeploymentEngine._extract_port_args(str(df_path), str(tmp_path), lambda lvl, msg: logs.append(msg))
    assert any(p in args_env for p in ["8085:8085", "8085:3000"])

    # Missing Dockerfile handling
    res = DeploymentEngine.build_and_run_dockerfile(str(tmp_path), "MissingDockerfile", "tag:v1", "app", lambda l, m: None)
    assert res is False


def test_postgres_auto_connect_and_dockerfile_injection(tmp_path):
    # 1. Test .env.example preservation + PostgreSQL auto-connect override
    env_example = tmp_path / ".env.example"
    env_example.write_text("APP_NAME=StockUMKM\nAPP_KEY=base64:123\nDB_CONNECTION=mysql\nDB_DATABASE=old_db\n")

    logs = []
    merged_env = DeploymentEngine.ensure_project_postgres_db(
        project_name="Stock-UMKM",
        env_vars={"DB_CONNECTION": "mysql"},
        db_config={"db_name": "stock_umkm", "user": "appuser", "password": "secret", "port": 5432},
        log_callback=lambda lvl, msg: logs.append(msg),
    )
    assert merged_env["DB_CONNECTION"] == "pgsql"
    assert merged_env["DB_DATABASE"] == "stock_umkm"
    assert merged_env["POSTGRES_DB"] == "stock_umkm"
    assert "postgresql://" in merged_env["DATABASE_URL"]

    DeploymentEngine.write_env_file(str(tmp_path), merged_env)
    written_env = (tmp_path / ".env").read_text()
    assert "APP_NAME=StockUMKM" in written_env
    assert "DB_CONNECTION=pgsql" in written_env
    assert "DB_DATABASE=stock_umkm" in written_env

    # 2. Test PHP Dockerfile auto-injection of pdo_pgsql
    df = tmp_path / "Dockerfile"
    df.write_text(
        "FROM php:8.4-apache\n"
        "RUN docker-php-ext-install pdo pdo_mysql\n"
        "EXPOSE 8080\n"
        "ENTRYPOINT [\"/entrypoint.sh\"]\n"
    )
    DeploymentEngine.prepare_dockerfile_for_postgres(str(tmp_path), "Dockerfile", lambda lvl, msg: logs.append(msg))
    updated_df = df.read_text()
    assert "pdo_pgsql" in updated_df
    assert "libpq-dev" in updated_df


def test_ensure_project_db_explicit_postgres_priority(tmp_path):
    # Simulate a workspace with .env.example defaulting to MySQL (e.g. Laravel or generic template)
    env_example = tmp_path / ".env.example"
    env_example.write_text(
        "APP_NAME=Laravel\n"
        "DB_CONNECTION=mysql\n"
        "DB_HOST=127.0.0.1\n"
        "DB_PORT=3306\n"
        "DB_DATABASE=laravel\n"
        "DB_USERNAME=root\n"
        "DB_PASSWORD=\n"
        "MYSQL_DATABASE=laravel\n"
    )

    logs = []
    # User explicitly chose PostgreSQL in the deployment configuration
    merged_env = DeploymentEngine.ensure_project_db(
        project_name="my-cool-app",
        env_vars={"MYSQL_HOST": "localhost", "DB_CONNECTION": "mysql"},
        db_config={"db_type": "postgres", "db_name": "my_cool_app_db", "user": "postgres", "password": "securepassword"},
        workspace_dir=str(tmp_path),
        log_callback=lambda lvl, msg: logs.append(msg),
    )

    # 1. PostgreSQL variables must be enforced
    assert merged_env["DB_CONNECTION"] == "pgsql"
    assert merged_env["DB_PORT"] == "5432"
    assert merged_env["POSTGRES_DB"] == "my_cool_app_db"
    assert "postgresql://" in merged_env["DATABASE_URL"]

    # 2. Conflicting MySQL variables must be purged
    assert "MYSQL_HOST" not in merged_env
    assert "MYSQL_DATABASE" not in merged_env


def test_auto_wire_silo_storage_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv("HOST_IP", raising=False)
    monkeypatch.delenv("SERVER_LAN_IP", raising=False)
    monkeypatch.delenv("MINIO_HOST", raising=False)
    monkeypatch.delenv("MINIO_PORT", raising=False)
    monkeypatch.delenv("MINIO_ROOT_USER", raising=False)
    monkeypatch.delenv("MINIO_ROOT_PASSWORD", raising=False)

    # 1. Default wiring when Laravel uses FILESYSTEM_DISK=s3
    logs = []
    merged_default = {"FILESYSTEM_DISK": "s3"}
    DeploymentEngine._auto_wire_silo_storage(
        project_name="Stock-UMKM",
        merged_env=merged_default,
        env_example_hint={"AWS_BUCKET": ""},
        log_callback=lambda lvl, msg: logs.append((lvl, msg)),
    )
    assert merged_default["AWS_BUCKET"] == "stock-umkm"
    assert merged_default["AWS_ENDPOINT"] == "http://minio:9000"
    assert merged_default["AWS_URL"] == "http://192.168.25.105:9000/stock-umkm"
    assert merged_default["AWS_USE_PATH_STYLE_ENDPOINT"] == "true"
    assert merged_default["AWS_ACCESS_KEY_ID"] == "admin"
    assert merged_default["AWS_SECRET_ACCESS_KEY"] == "admin12345"

    # 2. Existing user-configured AWS_BUCKET & AWS_URL must be preserved
    merged_custom = {
        "FILESYSTEM_DISK": "s3",
        "AWS_BUCKET": "images",
        "AWS_URL": "http://100.78.113.53:9000/images",
    }
    DeploymentEngine._auto_wire_silo_storage(
        project_name="Stock-UMKM",
        merged_env=merged_custom,
        env_example_hint={"AWS_BUCKET": "ignored-default"},
        log_callback=lambda lvl, msg: logs.append((lvl, msg)),
    )
    assert merged_custom["AWS_BUCKET"] == "images"
    assert merged_custom["AWS_URL"] == "http://100.78.113.53:9000/images"
    assert merged_custom["AWS_ENDPOINT"] == "http://minio:9000"


def test_ensure_project_db_mysql_prisma_normalizes_database_url(tmp_path):
    # Case 1: Prisma / Node.js workspace with pre-injected SQLAlchemy mysql+pymysql:// URL
    prisma_dir = tmp_path / "prisma_app"
    (prisma_dir / "prisma").mkdir(parents=True)
    logs = []
    merged_prisma = DeploymentEngine.ensure_project_db(
        project_name="financial-app",
        env_vars={"DATABASE_URL": "mysql+pymysql://fadb:wokeh123@mysql:3306/financial_app"},
        db_config={"db_type": "mysql", "db_name": "financial_app", "user": "fadb", "password": "wokeh123", "host": "mysql", "port": 3306},
        workspace_dir=str(prisma_dir),
        log_callback=lambda lvl, msg: logs.append((lvl, msg)),
    )
    assert merged_prisma["DATABASE_URL"].startswith("mysql://")
    assert "mysql+pymysql://" not in merged_prisma["DATABASE_URL"]
    assert merged_prisma["SQLALCHEMY_DATABASE_URI"].startswith("mysql+pymysql://")

    # Case 2: Python / SQLAlchemy workspace (no prisma/ or package.json) keeps mysql+pymysql://
    py_dir = tmp_path / "python_app"
    py_dir.mkdir(parents=True)
    merged_py = DeploymentEngine.ensure_project_db(
        project_name="python-api",
        env_vars={},
        db_config={"db_type": "mysql", "db_name": "python_api", "user": "fadb", "password": "wokeh123", "host": "mysql", "port": 3306},
        workspace_dir=str(py_dir),
        log_callback=lambda lvl, msg: logs.append((lvl, msg)),
    )
    assert merged_py["DATABASE_URL"].startswith("mysql+pymysql://")


def test_parse_port_mappings_avoids_reserved_grafana_3000(tmp_path):
    df_path = tmp_path / "Dockerfile"
    df_path.write_text("FROM node:20-alpine\nEXPOSE 3000\n")

    # 1. Explicit 3000:3000 must remap host port away from 3000 (>= 3100) while keeping container 3000
    logs = []
    cli_args, sdk_ports, primary_cp = DeploymentEngine.parse_port_mappings(
        explicit_mappings="3000:3000",
        full_df_path=str(df_path),
        workspace_dir=str(tmp_path),
        log_callback=lambda lvl, msg: logs.append((lvl, msg)),
        avoid_reserved_host_ports=True,
    )
    assert primary_cp == 3000
    mapped_hp = sdk_ports["3000/tcp"]
    assert mapped_hp >= 3100 and mapped_hp != 3000
    assert f"{mapped_hp}:3000" in cli_args
    assert any("Grafana" in msg for _, msg in logs)

    # 2. Fallback from .env PORT=3000 with avoid_reserved_host_ports=True must also remap host port
    (tmp_path / ".env").write_text("PORT=3000\n")
    logs_env = []
    cli_env, sdk_env, primary_env_cp = DeploymentEngine.parse_port_mappings(
        explicit_mappings=None,
        full_df_path=str(df_path),
        workspace_dir=str(tmp_path),
        log_callback=lambda lvl, msg: logs_env.append((lvl, msg)),
        avoid_reserved_host_ports=True,
    )
    assert primary_env_cp == 3000
    assert sdk_env["3000/tcp"] >= 3100
    assert f"{sdk_env['3000/tcp']}:3000" in cli_env

    # 3. Safe non-conflicting port 3105:3000 must be kept unchanged
    logs_safe = []
    cli_safe, sdk_safe, primary_safe_cp = DeploymentEngine.parse_port_mappings(
        explicit_mappings="3105:3000",
        full_df_path=str(df_path),
        workspace_dir=str(tmp_path),
        log_callback=lambda lvl, msg: logs_safe.append((lvl, msg)),
        avoid_reserved_host_ports=True,
    )
    assert primary_safe_cp == 3000
    assert sdk_safe == {"3000/tcp": 3105}
    assert "-p" in cli_safe and "3105:3000" in cli_safe






