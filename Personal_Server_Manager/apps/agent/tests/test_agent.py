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


def test_parse_port_mappings_avoids_live_docker_port_and_excludes_self(tmp_path, monkeypatch):
    df_path = tmp_path / "Dockerfile"
    df_path.write_text("FROM node:20-alpine\nEXPOSE 3000\n")

    def fake_live(exclude_container_name=None):
        if exclude_container_name and "stock-umkm" in exclude_container_name:
            return {}
        return {3100: "Container Docker: stock-umkm (Port 3100)"}

    monkeypatch.setattr(DeploymentEngine, "_collect_live_host_ports", staticmethod(fake_live))
    monkeypatch.setattr(DeploymentEngine, "_is_host_port_open", staticmethod(lambda port, probe_hosts=None: False))

    logs = []
    cli_args, sdk_ports, primary_cp = DeploymentEngine.parse_port_mappings(
        explicit_mappings="3100:3000",
        full_df_path=str(df_path),
        workspace_dir=str(tmp_path),
        log_callback=lambda lvl, msg: logs.append((lvl, msg)),
        avoid_reserved_host_ports=True,
        exclude_container_name="financial-app",
    )
    assert primary_cp == 3000
    assert sdk_ports == {"3000/tcp": 3101}
    assert "3101:3000" in cli_args
    assert any("stock-umkm" in msg for _, msg in logs)

    # Same port bound by self (`stock-umkm`) must be retained when replacing `stock-umkm`
    df_stock = tmp_path / "Dockerfile.stock"
    df_stock.write_text("FROM php:8.4-apache\nEXPOSE 80\n")
    logs_self = []
    cli_self, sdk_self, primary_self_cp = DeploymentEngine.parse_port_mappings(
        explicit_mappings="3100:80",
        full_df_path=str(df_stock),
        workspace_dir=str(tmp_path),
        log_callback=lambda lvl, msg: logs_self.append((lvl, msg)),
        avoid_reserved_host_ports=True,
        exclude_container_name="stock-umkm",
    )
    assert primary_self_cp == 80
    assert sdk_self == {"80/tcp": 3100}
    assert "3100:80" in cli_self


def test_build_and_run_dockerfile_auto_retries_on_port_already_allocated(tmp_path, monkeypatch):
    import shutil
    import subprocess

    df_path = tmp_path / "Dockerfile"
    df_path.write_text("FROM node:20-alpine\nEXPOSE 3000\nCMD [\"node\", \"server.js\"]\n")

    monkeypatch.setattr(DeploymentEngine, "_collect_live_host_ports", staticmethod(lambda exclude_container_name=None: {}))
    monkeypatch.setattr(DeploymentEngine, "_is_host_port_open", staticmethod(lambda port, probe_hosts=None: False))
    monkeypatch.setattr(DeploymentEngine, "_resolve_psm_network_name", staticmethod(lambda: "homelab"))
    monkeypatch.setattr(DeploymentEngine, "run_post_deploy_db_migrations", staticmethod(lambda *args, **kwargs: None))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)

    rm_calls = []

    class _Completed:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, *args, **kwargs):
        if len(cmd) >= 3 and cmd[1] == "rm":
            rm_calls.append(cmd)
        return _Completed(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_cmds = []

    class _FakePopen:
        def __init__(self, cmd, *args, **kwargs):
            self.cmd = cmd
            if cmd[1] == "build":
                self.stdout = iter(["#1 DONE\n"])
                self.returncode = 0
            elif cmd[1] == "run":
                run_cmds.append(cmd)
                if len(run_cmds) == 1:
                    self.stdout = iter([
                        "6acf4a9f\n",
                        "docker: Error response from daemon: Bind for 0.0.0.0:3100 failed: port is already allocated\n",
                    ])
                    self.returncode = 125
                else:
                    self.stdout = iter(["newcontainerid123\n"])
                    self.returncode = 0
            else:
                self.stdout = iter([])
                self.returncode = 0

        def wait(self):
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    logs = []
    effective_out = []
    ok = DeploymentEngine.build_and_run_dockerfile(
        workspace_dir=str(tmp_path),
        dockerfile_path="Dockerfile",
        image_tag="financial-app:v4",
        container_name="financial-app",
        log_callback=lambda lvl, msg: logs.append((lvl, msg)),
        port_mappings="3100:3000",
        effective_ports_out=effective_out,
    )

    assert ok is True
    assert len(run_cmds) == 2
    assert "3100:3000" in run_cmds[0]
    assert "3101:3000" in run_cmds[1]
    assert len(rm_calls) >= 2  # initial cleanup + post-collision cleanup before retry
    assert effective_out == ["3101:3000"]
    assert any("3101" in msg and "percobaan 2/5" in msg for lvl, msg in logs if lvl == "warning")


def test_classify_build_line_and_extract_excerpt():
    assert DeploymentEngine._classify_build_line("ERR_PNPM_OUTDATED_LOCKFILE Cannot install") == "error"
    assert DeploymentEngine._classify_build_line("Module not found: Can't resolve 'dagre'") == "error"
    assert DeploymentEngine._classify_build_line("npm error code EUSAGE") == "error"
    assert DeploymentEngine._classify_build_line("[WARN] deprecated glob@10.5.0") == "warning"
    assert DeploymentEngine._classify_build_line("#9 [builder 3/6] COPY . .") == "info"

    lines = [
        "#9 [builder 3/6] RUN pnpm install --frozen-lockfile",
        "ERR_PNPM_OUTDATED_LOCKFILE Cannot install with \"frozen-lockfile\"",
        "ERROR: failed to solve: process \"/bin/sh -c pnpm install --frozen-lockfile\" did not complete successfully: exit code: 1",
    ]
    excerpt = DeploymentEngine._extract_build_failure_excerpt(lines, max_lines=3)
    assert any("ERR_PNPM_OUTDATED_LOCKFILE" in ln for ln in excerpt)


def test_attempt_build_auto_heal_patches_lockfile_and_skips_unrelated(tmp_path):
    df_pnpm = tmp_path / "Dockerfile"
    df_pnpm.write_text("FROM node:22-alpine\nRUN pnpm install --frozen-lockfile\n")
    logs = []
    healed, kind = DeploymentEngine._attempt_build_auto_heal(
        workspace_dir=str(tmp_path),
        dockerfile_path="Dockerfile",
        build_output='ERR_PNPM_OUTDATED_LOCKFILE Cannot install with "frozen-lockfile"',
        log_callback=lambda lvl, msg: logs.append((lvl, msg)),
    )
    assert healed is True
    assert kind == "lockfile_fallback"
    assert "--no-frozen-lockfile" in df_pnpm.read_text()
    assert "--frozen-lockfile" not in df_pnpm.read_text()

    df_npm = tmp_path / "Dockerfile.npm"
    df_npm.write_text("FROM node:20-alpine\nRUN npm ci\n")
    healed_npm, kind_npm = DeploymentEngine._attempt_build_auto_heal(
        workspace_dir=str(tmp_path),
        dockerfile_path="Dockerfile.npm",
        build_output="npm error `npm ci` can only install packages when your package.json and package-lock.json are in sync with package-lock.json",
        log_callback=lambda lvl, msg: logs.append((lvl, msg)),
    )
    assert healed_npm is True
    assert kind_npm == "lockfile_fallback"
    assert "npm install --legacy-peer-deps" in df_npm.read_text()

    healed_none, kind_none = DeploymentEngine._attempt_build_auto_heal(
        workspace_dir=str(tmp_path),
        dockerfile_path="Dockerfile",
        build_output="SyntaxError: Unexpected token '}'",
        log_callback=lambda lvl, msg: logs.append((lvl, msg)),
    )
    assert healed_none is False
    assert kind_none == ""


def test_realtime_log_streamer_batches_and_flushes(monkeypatch):
    import time
    from apps.agent.app.jobs.executor import _RealtimeLogStreamer

    sent_batches = []

    class _Resp:
        status_code = 200

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def post(self, url, json=None, headers=None):
            sent_batches.append((url, json["logs"]))
            return _Resp()

        def close(self):
            pass

    monkeypatch.setattr("apps.agent.app.jobs.executor.httpx.Client", _FakeClient)

    streamer = _RealtimeLogStreamer(job_id="job-123", flush_interval=2.0, max_batch_size=3)
    streamer.enqueue({"sequence": 1, "level": "info", "message": "line 1"})
    streamer.enqueue({"sequence": 2, "level": "info", "message": "line 2"})
    streamer.enqueue({"sequence": 3, "level": "info", "message": "line 3"})
    time.sleep(0.1)
    streamer.enqueue({"sequence": 4, "level": "error", "message": "boom"})
    time.sleep(0.1)
    streamer.close()

    flattened = [entry for _, batch in sent_batches for entry in batch]
    assert [e["sequence"] for e in flattened] == [1, 2, 3, 4]
    assert len(sent_batches) >= 2


def test_serialized_deployment_guard_and_build_throttling(tmp_path, monkeypatch):
    import shutil
    import threading
    import time
    from apps.agent.app.jobs.executor import is_deployment_active, serialized_deployment_guard
    from apps.agent.app.docker.client import DockerManager

    # 1. Serialized deployment guard prevents concurrent builds and emits queue stage notice
    assert is_deployment_active() is False
    order = []
    logs_second = []
    first_entered = threading.Event()
    release_first = threading.Event()

    def worker_one():
        with serialized_deployment_guard():
            assert is_deployment_active() is True
            order.append("one_start")
            first_entered.set()
            release_first.wait(timeout=2.0)
            order.append("one_end")

    def worker_two():
        first_entered.wait(timeout=2.0)
        with serialized_deployment_guard(lambda lvl, msg: logs_second.append((lvl, msg))):
            order.append("two_start")

    t1 = threading.Thread(target=worker_one)
    t2 = threading.Thread(target=worker_two)
    t1.start()
    t2.start()
    first_entered.wait(timeout=2.0)
    time.sleep(0.08)

    # While deployment is active, DockerManager.get_all_container_stats skips live `docker stats` CLI call
    dm = DockerManager()
    dm._stats_cache = [{"name": "cached-app", "cpu_percent": "1.2"}]
    assert dm.get_all_container_stats(force=False) == [{"name": "cached-app", "cpu_percent": "1.2"}]

    release_first.set()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert is_deployment_active() is False
    assert order == ["one_start", "one_end", "two_start"]
    assert any("[STAGE:QUEUE]" in msg for _, msg in logs_second)

    # 2. Low-priority CPU/IO command wrapper adds ionice & nice when present on Linux
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in ("nice", "ionice") else None,
    )
    wrapped = DeploymentEngine._wrap_low_priority_cmd(["docker", "build", "."])
    assert wrapped[:6] == ["/usr/bin/ionice", "-c", "2", "-n", "7", "/usr/bin/nice"]
    assert wrapped[-3:] == ["docker", "build", "."]

    # 3. Custom Node.js Dockerfile gets NODE_OPTIONS heap cap injected automatically
    custom_df = tmp_path / "Dockerfile"
    custom_df.write_text("FROM node:20-alpine\nCOPY . .\nRUN npm run build\nCMD [\"npm\", \"start\"]\n")
    prep_logs = []
    DeploymentEngine.prepare_dockerfile_environment(
        workspace_dir=str(tmp_path),
        dockerfile_path="Dockerfile",
        log_callback=lambda lvl, msg: prep_logs.append((lvl, msg)),
    )
    patched = custom_df.read_text()
    assert 'ENV NODE_OPTIONS="--max-old-space-size=' in patched
    assert patched.index("NODE_OPTIONS") < patched.index("RUN npm run build")

    # 4. Monorepo pnpm --filter build also receives NODE_OPTIONS heap cap
    mono_df = tmp_path / "Dockerfile.mono"
    mono_df.write_text("FROM node:22-alpine AS builder\nRUN pnpm --filter web build\n")
    DeploymentEngine.prepare_dockerfile_environment(
        workspace_dir=str(tmp_path),
        dockerfile_path="Dockerfile.mono",
        log_callback=lambda lvl, msg: prep_logs.append((lvl, msg)),
    )
    assert "NODE_OPTIONS" in mono_df.read_text()

    # 5. Auto-heal strips `--mount=type=cache` when legacy builder rejects BuildKit mount syntax
    bk_df = tmp_path / "Dockerfile.archflow"
    bk_df.write_text(
        "FROM node:22-alpine\n"
        "RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store pnpm install --frozen-lockfile\n"
    )
    healed_bk, kind_bk = DeploymentEngine._attempt_build_auto_heal(
        workspace_dir=str(tmp_path),
        dockerfile_path="Dockerfile.archflow",
        build_output="the --mount option requires BuildKit. Refer to https://docs.docker.com/go/buildkit/",
        log_callback=lambda lvl, msg: prep_logs.append((lvl, msg)),
    )
    assert healed_bk is True
    assert kind_bk == "buildkit_mount_fallback"
    cleaned_bk = bk_df.read_text()
    assert "--mount=type=cache" not in cleaned_bk
    assert "RUN pnpm install --frozen-lockfile" in cleaned_bk


def test_resolve_app_resource_limits(monkeypatch):
    from apps.agent.app.core.config import agent_settings

    # Unbounded defaults / explicit unlimited values return (None, None, None)
    for val in ("", "0", "none", "unlimited", "off"):
        monkeypatch.setattr(agent_settings, "APP_MEMORY_LIMIT", val)
        monkeypatch.setattr(agent_settings, "APP_MEMORY_SWAP", "1024m")
        monkeypatch.setattr(agent_settings, "APP_CPUS", val)
        assert DeploymentEngine._resolve_app_resource_limits() == (None, None, None)

    # Configured limits are parsed and swap is preserved only alongside memory
    monkeypatch.setattr(agent_settings, "APP_MEMORY_LIMIT", "768m")
    monkeypatch.setattr(agent_settings, "APP_MEMORY_SWAP", "1536m")
    monkeypatch.setattr(agent_settings, "APP_CPUS", "1.5")
    assert DeploymentEngine._resolve_app_resource_limits() == ("768m", "1536m", 1.5)

    monkeypatch.setattr(agent_settings, "APP_MEMORY_SWAP", "0")
    monkeypatch.setattr(agent_settings, "APP_CPUS", "invalid")
    assert DeploymentEngine._resolve_app_resource_limits() == ("768m", None, None)









