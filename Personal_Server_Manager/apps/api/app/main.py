import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from apps.api.app.core.config import settings
from apps.api.app.core.security import hash_password, hash_token
import apps.api.app.database.session as db_session
from apps.api.app.database.session import engine, Base, SessionLocal, _auto_ensure_psm_postgres_db, ACTIVE_PSM_DB_INFO
from apps.api.app.models.entities import User, Server, AgentToken, Container, ServerMetric, Project, ProjectSource
from apps.api.app.api.v1.router import api_router
from apps.api.app.websocket.handlers import ws_router

def _wait_for_database(max_attempts: int = 15, delay: float = 2.0):
    """Ensure PostgreSQL database 'psm' exists (creating it if missing) and retry DB connection on startup."""
    if os.getenv("TESTING") == "true" or str(db_session.engine.url).startswith("sqlite"):
        return
    import time
    from sqlalchemy import text, create_engine
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            with db_session.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            safe_url = db_session.engine.url.render_as_string(hide_password=True)
            print(f"[PSM] Database check ({safe_url}) attempt {attempt}/{max_attempts}: {e.__class__.__name__}: {e}", flush=True)
            # Re-run auto-create & auto-resolve for PostgreSQL database 'psm'
            new_url = _auto_ensure_psm_postgres_db(settings.DATABASE_URL)
            if new_url and not new_url.startswith("sqlite"):
                db_session.engine = create_engine(new_url, pool_pre_ping=True)
                db_session.SessionLocal.configure(bind=db_session.engine)
            time.sleep(delay)
    print(f"[PSM] Warning: Could not connect to database after {max_attempts} attempts: {last_err}", flush=True)

def init_db():
    _wait_for_database()
    try:
        Base.metadata.create_all(bind=db_session.engine)
    except Exception as e:
        print(f"[PSM] DB schema init notice: {e}", flush=True)
        return

    # Safe column migration for existing databases (inspect catalog first to avoid ACCESS EXCLUSIVE locks)
    try:
        from sqlalchemy import inspect as sa_inspect, text
        existing_cols = {c["name"] for c in sa_inspect(db_session.engine).get_columns("projects")}
        missing_cols = [
            (col, col_type)
            for col, col_type in [
                ("port_mappings", "VARCHAR(255)"),
                ("domain", "VARCHAR(255)"),
                ("db_type", "VARCHAR(50) DEFAULT 'none'"),
                ("db_name", "VARCHAR(100)"),
                ("maintenance_mode", "BOOLEAN DEFAULT FALSE"),
                ("maintenance_title", "VARCHAR(255)"),
                ("maintenance_message", "TEXT"),
                ("maintenance_eta", "VARCHAR(120)"),
            ]
            if col not in existing_cols
        ]
        if missing_cols:
            with db_session.engine.connect() as conn:
                for col, col_type in missing_cols:
                    try:
                        conn.execute(text(f"ALTER TABLE projects ADD COLUMN IF NOT EXISTS {col} {col_type};"))
                        conn.commit()
                    except Exception:
                        try:
                            conn.execute(text(f"ALTER TABLE projects ADD COLUMN {col} {col_type};"))
                            conn.commit()
                        except Exception:
                            pass
    except Exception:
        pass
    db = db_session.SessionLocal()
    try:
        # Seed default admin user
        admin = db.query(User).filter(User.email == "admin@psm.local").first()
        if not admin:
            admin = User(
                name="PSM Administrator",
                email="admin@psm.local",
                password_hash=hash_password("Admin12345!"),
                role="owner",
                theme="system",
                is_active=True
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)

        # Seed initial local server for homelab if no servers exist
        server_count = db.query(Server).count()
        if server_count == 0:
            local_server = Server(
                owner_id=admin.id,
                name="Homelab-Primary",
                hostname=settings.SERVER_LAN_IP,
                description="Primary Ubuntu Server (HomeLab)",
                os_name="Ubuntu Server",
                os_version="24.04 LTS",
                architecture="x86_64",
                status="online"
            )
            db.add(local_server)
            db.commit()
            db.refresh(local_server)

            # Seed default agent token for Homelab-Primary so built-in agent container can auto-register
            default_agent_token = os.getenv("HOMELAB_AGENT_TOKEN", "psm_agent_homelab_default_token")
            db.add(
                AgentToken(
                    server_id=local_server.id,
                    token_hash=hash_token(default_agent_token),
                    name="homelab-auto-token"
                )
            )
            db.commit()

            # Add demo containers for homelab
            containers = [
                Container(
                    server_id=local_server.id,
                    docker_id="d1a2b3c4d5e6",
                    name="nextcloud-app",
                    image="nextcloud:latest",
                    status="running",
                    state="running",
                    ports=[{"internal": 80, "external": 80, "protocol": "tcp"}]
                ),
                Container(
                    server_id=local_server.id,
                    docker_id="e2b3c4d5e6f7",
                    name="prometheus",
                    image="prom/prometheus:v2.50.0",
                    status="running",
                    state="running",
                    ports=[{"internal": 9090, "external": 9001, "protocol": "tcp"}]
                ),
                Container(
                    server_id=local_server.id,
                    docker_id="f3c4d5e6f7a8",
                    name="grafana",
                    image="grafana/grafana:10.4.0",
                    status="running",
                    state="running",
                    ports=[{"internal": 3000, "external": 3001, "protocol": "tcp"}]
                ),
                Container(
                    server_id=local_server.id,
                    docker_id="a4b5c6d7e8f9",
                    name="uptime-kuma",
                    image="louislam/uptime-kuma:1",
                    status="running",
                    state="running",
                    ports=[{"internal": 3001, "external": 3002, "protocol": "tcp"}]
                )
            ]
            db.add_all(containers)

            # Add sample metrics
            metric = ServerMetric(
                server_id=local_server.id,
                cpu_usage=24.5,
                memory_usage=58.2,
                memory_used_bytes=2415919104, # ~2.25 GB
                memory_total_bytes=4294967296, # 4 GB
                disk_usage=42.0,
                disk_used_bytes=107374182400, # 100 GB
                disk_total_bytes=256000000000, # 256 GB
                network_rx=142050.0,
                network_tx=98200.0,
                uptime=345600 # 4 days
            )
            db.add(metric)

            # Add a sample project
            demo_project = Project(
                server_id=local_server.id,
                owner_id=admin.id,
                name="portfolio-website",
                description="Personal developer website and blog",
                framework="Next.js",
                source_type="git",
                status="active"
            )
            db.add(demo_project)
            db.commit()
            db.refresh(demo_project)

            src = ProjectSource(
                project_id=demo_project.id,
                repository_url="https://github.com/example/portfolio.git",
                branch="main",
                dockerfile_path="Dockerfile",
                compose_path="docker-compose.yml"
            )
            db.add(src)
            db.commit()
        else:
            primary_srv = db.query(Server).filter(Server.name == "Homelab-Primary").first()
            if primary_srv:
                import re
                if not primary_srv.hostname or not re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", primary_srv.hostname.strip()):
                    primary_srv.hostname = settings.SERVER_LAN_IP
                    db.commit()
                default_agent_token = os.getenv("HOMELAB_AGENT_TOKEN", "psm_agent_homelab_default_token")
                tok_hash = hash_token(default_agent_token)
                existing_tok = db.query(AgentToken).filter(
                    AgentToken.server_id == primary_srv.id,
                    AgentToken.token_hash == tok_hash
                ).first()
                if not existing_tok:
                    db.add(AgentToken(server_id=primary_srv.id, token_hash=tok_hash, name="homelab-auto-token"))
                    db.commit()

    finally:
        db.close()


def _run_post_startup_db_maintenance_sync() -> None:
    """
    Prune expired historical metrics FIRST, then ensure composite indexes in the background
    after Uvicorn is already serving `/health` so large tables never cause container healthcheck timeouts.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import text

    db = db_session.SessionLocal()
    try:
        retention_hours = max(6, int(os.getenv("METRICS_RETENTION_HOURS", "48")))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
        db.query(ServerMetric).filter(ServerMetric.recorded_at < cutoff).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    try:
        with db_session.engine.connect() as conn:
            for idx_sql in [
                "CREATE INDEX IF NOT EXISTS idx_server_metrics_srv_rec ON server_metrics (server_id, recorded_at DESC);",
                "CREATE INDEX IF NOT EXISTS idx_deployment_logs_dep_seq ON deployment_logs (deployment_id, sequence ASC);",
            ]:
                try:
                    conn.execute(text(idx_sql))
                    conn.commit()
                except Exception:
                    pass
    except Exception:
        pass


async def _startup_background_tasks() -> None:
    import asyncio
    from apps.api.app.services.pap_client import sync_all_project_domains

    # Let Uvicorn bind port 8000 and answer Docker healthcheck immediately first
    await asyncio.sleep(3)
    try:
        await asyncio.to_thread(_run_post_startup_db_maintenance_sync)
    except Exception:
        pass

    for delay in (2, 5, 5):
        await asyncio.sleep(delay)
        db = db_session.SessionLocal()
        try:
            res = await asyncio.to_thread(sync_all_project_domains, db)
            if res.get("total", 0) == 0 or res.get("synced", 0) > 0:
                return
        except Exception:
            pass
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    # Fast schema & seed check (<1s when DB is warm)
    await asyncio.to_thread(init_db)
    bg_task = None
    if os.getenv("TESTING") != "true":
        bg_task = asyncio.create_task(_startup_background_tasks())
    yield
    # Shutdown
    if bg_task and not bg_task.done():
        bg_task.cancel()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Personal Server Manager (PSM) - Self-hosted Multi-Server Management Platform",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins in development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(ws_router)

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "PSM Control Plane API",
        "version": settings.VERSION,
        "database": ACTIVE_PSM_DB_INFO,
    }

@app.get("/metrics")
def prometheus_root_metrics():
    """Standard Prometheus scrape endpoint exposing PSM cluster and host metrics."""
    from apps.api.app.api.v1.monitoring import export_prometheus_metrics
    db = SessionLocal()
    try:
        return export_prometheus_metrics(db=db)
    finally:
        db.close()

@app.get("/install-agent.sh")
def get_agent_install_script():
    script_content = """#!/usr/bin/env bash
set -e
echo "========================================="
echo "Personal Server Manager (PSM) Agent Setup"
echo "========================================="

if [ -z "$PSM_SERVER_ID" ] || [ -z "$PSM_TOKEN" ]; then
  echo "Error: PSM_SERVER_ID and PSM_TOKEN environment variables are required."
  exit 1
fi

PSM_CONTROL_PLANE="${PSM_CONTROL_PLANE:-http://localhost:8000}"
INSTALL_DIR="/opt/psm/agent"

echo "Target Control Plane: $PSM_CONTROL_PLANE"
echo "Server ID: $PSM_SERVER_ID"

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Write agent configuration
cat <<EOF > agent.env
SERVER_ID=$PSM_SERVER_ID
AGENT_TOKEN=$PSM_TOKEN
CONTROL_PLANE_URL=$PSM_CONTROL_PLANE
EOF

echo "PSM Agent configured successfully at $INSTALL_DIR/agent.env"
"""
    return Response(content=script_content, media_type="text/x-shellscript")

