import base64
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from apps.api.app.core.config import settings
from apps.api.app.database.session import get_db
from apps.api.app.core.security import encrypt_secret, decrypt_secret
from apps.api.app.core.dependencies import get_current_user, require_role
from apps.api.app.models.entities import (
    Project,
    ProjectSource,
    EnvironmentVariable,
    Server,
    Deployment,
    DeploymentLog,
    Container,
    User,
)
from apps.api.app.schemas.all_schemas import (
    ProjectCreate,
    ProjectUpdate,
    ProjectMaintenanceRequest,
    ProjectResponse,
    ProjectDatabaseProvisionRequest,
    DockerfileGenerateRequest,
    DockerfileUpdateRequest,
    EnvironmentVariableUpsertRequest,
    EnvironmentUpdateRequest,
    EnvironmentResetRequest,
    EnvironmentItemResponse,
    DeploymentResponse,
    ContainerResponse,
)
from apps.api.app.services.audit_service import log_activity
from apps.api.app.services.maintenance_service import (
    render_maintenance_html,
    apply_container_maintenance,
)
from apps.api.app.services.pap_client import (
    extract_host_port,
    remove_domain_routing,
    resolve_effective_domain,
    sync_all_project_domains,
    sync_domain_routing,
    sync_psm_to_pap,
)
from apps.api.app.services.job_service import create_and_dispatch_job
from apps.api.app.services.github_service import get_stored_token
from apps.api.app.services.db_provision_service import (
    provision_and_sync_project_db,
    resolve_project_db_name,
    ensure_postgres_database,
    build_postgres_env_vars,
    ensure_mysql_database,
    build_mysql_env_vars,
    build_sqlite_env_vars,
    list_all_postgres_databases,
    list_all_mysql_databases,
)
from apps.api.app.services.port_service import (
    find_next_available_port,
    validate_or_allocate_port_mappings,
)

router = APIRouter(prefix="/projects", tags=["Project Management"])

def mask_secret(val: str) -> str:
    if not val:
        return ""
    if len(val) <= 4:
        return "****"
    return val[:2] + "****" + val[-2:]

def sanitize_git_url_and_branch(repo_url: Optional[str], branch: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not repo_url:
        return repo_url, branch
    clean_url = repo_url.strip()
    clean_branch = (branch or "").strip()
    if "/tree/" in clean_url:
        parts = clean_url.split("/tree/", 1)
        clean_url = parts[0].rstrip("/")
        extracted_branch = parts[1].strip("/").split("/")[0]
        if extracted_branch and (not clean_branch or clean_branch in ("main", "master")):
            clean_branch = extracted_branch
    if clean_url and not clean_url.endswith(".git") and "github.com" in clean_url:
        clean_url = clean_url.rstrip("/") + ".git"
    return clean_url, (clean_branch or "main")

_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_SAFE_DB_ENV_KEYS = {
    "DB_CONNECTION", "DB_HOST", "DB_PORT", "DB_DATABASE", "DB_NAME",
    "DB_USERNAME", "DB_USER", "MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE",
    "MYSQL_USER", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
    "POSTGRES_USER", "SQLITE_DATABASE", "SQLITE_DB_PATH",
}


def _normalize_db_engine(db_type: Optional[str]) -> str:
    val = (db_type or "postgres").strip().lower()
    if val in ("mysql", "mariadb"):
        return "mysql"
    if val == "sqlite":
        return "sqlite"
    if val in ("none", "stateless", "false"):
        return "none"
    return "postgres"


def _get_safe_project_env_map(db: Session, project_id: str) -> Dict[str, str]:
    """Decrypt only non-secret DB metadata keys for inspection and matching."""
    rows = (
        db.query(EnvironmentVariable)
        .filter(
            EnvironmentVariable.project_id == project_id,
            EnvironmentVariable.key.in_(_SAFE_DB_ENV_KEYS),
        )
        .all()
    )
    safe_map: Dict[str, str] = {}
    for item in rows:
        try:
            safe_map[item.key] = decrypt_secret(item.encrypted_value)
        except Exception:
            pass
    return safe_map


def _inspect_project_runtime(p: Project, db: Session) -> Dict[str, Any]:
    """Inspect Docker container status/networks/non-secret DB env and latest deployment for a project."""
    latest_dep = (
        db.query(Deployment)
        .filter(Deployment.project_id == p.id)
        .order_by(Deployment.version.desc())
        .first()
    )
    runtime: Dict[str, Any] = {
        "container_exists": False,
        "container_running": False,
        "container_name": p.name,
        "container_state": "not_created",
        "container_networks": [],
        "container_db_env": {},
        "latest_deployment_version": latest_dep.version if latest_dep else None,
        "latest_deployment_status": latest_dep.status if latest_dep else None,
    }
    try:
        from apps.api.app.services.docker_service import DockerEngineService

        matched = DockerEngineService.find_container_by_project(p.name, p.id)
        if matched:
            cid = matched.get("full_id") or matched.get("docker_id") or matched.get("name")
            inspected = DockerEngineService.inspect_container(cid) if cid else {}
            runtime["container_exists"] = True
            runtime["container_name"] = inspected.get("name") or matched.get("name") or p.name
            runtime["container_state"] = inspected.get("state") or matched.get("state") or "unknown"
            runtime["container_running"] = bool(
                inspected.get("running", "Up" in str(matched.get("status", "")))
            )
            runtime["container_networks"] = inspected.get("networks") or []
            runtime["container_db_env"] = inspected.get("db_env") or {}
    except Exception:
        pass
    return runtime


def _build_connected_project_entry(
    p: Project,
    runtime: Dict[str, Any],
    current_project_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "framework": p.framework,
        "status": p.status,
        "domain": resolve_effective_domain(p.domain, p.name),
        "port_mappings": p.port_mappings or "",
        "container_exists": bool(runtime.get("container_exists", False)),
        "container_name": runtime.get("container_name") or p.name,
        "container_running": bool(runtime.get("container_running", False)),
        "container_state": runtime.get("container_state") or "not_created",
        "container_networks": runtime.get("container_networks") or [],
        "latest_deployment_version": runtime.get("latest_deployment_version"),
        "latest_deployment_status": runtime.get("latest_deployment_status"),
        "is_current_project": bool(current_project_id and p.id == current_project_id),
    }


def _resolve_server_ips(server: Optional[Server]) -> Tuple[str, Optional[str]]:
    """Return (lan_ip, tailscale_ip) avoiding container hostnames or loopback."""
    hostname = (server.hostname or "").strip() if server else ""
    if hostname and _IPV4_RE.match(hostname) and hostname not in ("127.0.0.1", "0.0.0.0"):
        lan_ip = hostname
    else:
        lan_ip = settings.SERVER_LAN_IP
    ts_ip = settings.SERVER_TS_IP or None
    return lan_ip, ts_ip


def _build_project_response(p: Project, deployment_id: Optional[str] = None) -> ProjectResponse:
    src = p.source
    lan_ip, ts_ip = _resolve_server_ips(p.server)
    port_mappings = p.port_mappings or ""
    host_port = extract_host_port(port_mappings) if port_mappings else None
    effective_domain = resolve_effective_domain(p.domain, p.name)
    access_urls: Dict[str, Optional[str]] = {
        "domain": f"http://{effective_domain}",
        "lan_ip": f"http://{lan_ip}:{host_port}" if host_port else None,
        "tailscale_ip": f"http://{ts_ip}:{host_port}" if (ts_ip and host_port) else None,
    }
    return ProjectResponse(
        id=p.id,
        server_id=p.server_id,
        server_name=p.server.name if p.server else "Unknown Server",
        owner_id=p.owner_id,
        name=p.name,
        description=p.description,
        framework=p.framework,
        source_type=p.source_type,
        status=p.status,
        repository_url=src.repository_url if src else None,
        branch=src.branch if src else None,
        dockerfile_path=src.dockerfile_path if src else None,
        compose_path=src.compose_path if src else None,
        port_mappings=port_mappings or None,
        domain=effective_domain,
        db_type=p.db_type or "none",
        db_name=p.db_name,
        maintenance_mode=bool(getattr(p, "maintenance_mode", False) or p.status == "maintenance"),
        maintenance_title=getattr(p, "maintenance_title", None),
        maintenance_message=getattr(p, "maintenance_message", None),
        maintenance_eta=getattr(p, "maintenance_eta", None),
        server_ip=lan_ip,
        server_tailscale_ip=ts_ip,
        access_urls=access_urls,
        deployment_id=deployment_id,
        created_at=p.created_at,
    )


def _sync_project_maintenance_targets(p: Project, retry_after: int = 60) -> Dict[str, Any]:
    eff_domain = resolve_effective_domain(p.domain, p.name)
    hp = extract_host_port(p.port_mappings)
    is_maint = bool(getattr(p, "maintenance_mode", False) or p.status == "maintenance")
    html_content = render_maintenance_html(
        project_name=p.name,
        domain=eff_domain,
        title=getattr(p, "maintenance_title", None),
        message=getattr(p, "maintenance_message", None),
        eta=getattr(p, "maintenance_eta", None),
    )
    router_synced = sync_domain_routing(
        domain=eff_domain,
        target_port=hp,
        project_name=p.name,
        maintenance_mode=is_maint,
        maintenance_title=getattr(p, "maintenance_title", None),
        maintenance_message=getattr(p, "maintenance_message", None),
        maintenance_eta=getattr(p, "maintenance_eta", None),
        retry_after=retry_after,
    )
    container_sync = apply_container_maintenance(
        project=p,
        enabled=is_maint,
        html_content=html_content,
        retry_after=retry_after,
    )
    return {
        "eff_domain": eff_domain,
        "target_port": hp,
        "router_synced": router_synced,
        "container_sync": container_sync,
    }


@router.get("", response_model=List[ProjectResponse])
def list_projects(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return [_build_project_response(p) for p in projects]

@router.get("/next-port")
def get_next_available_port(
    db: Session = Depends(get_db)
):
    """Detects active and reserved ports in the homelab and returns a safe, unallocated host port."""
    candidate, reserved_ports = find_next_available_port(db)
    return {
        "success": True,
        "recommended_host_port": candidate,
        "reserved_ports": reserved_ports,
    }


@router.post("/sync-domains")
def sync_all_domains_endpoint(
    current_user: User = Depends(require_role("owner", "admin", "operator")),
    db: Session = Depends(get_db)
):
    """Synchronize all registered projects' domains and virtual hosts to PAP DNS & Ingress Router."""
    return sync_all_project_domains(db)


@router.get("/databases/list")
def get_all_postgres_databases(
    current_user: User = Depends(get_current_user),
):
    """List all databases present on the PostgreSQL server."""
    return {"databases": list_all_postgres_databases()}


@router.get("/databases/overview")
def get_databases_overview(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return PostgreSQL, MySQL, and SQLite databases mapped to connected PSM projects and live containers."""
    db_entries: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for item in list_all_postgres_databases():
        name = str(item.get("db_name") or "")
        if not name:
            continue
        db_entries[("postgres", name.lower())] = {
            "db_type": "postgres",
            "db_name": name,
            "host": item.get("host") or settings.POSTGRES_HOST or "postgres",
            "port": int(item.get("port") or 5432),
            "size_bytes": int(item.get("size_bytes") or 0),
            "tables_count": int(item.get("tables_count") or 0),
            "active_connections": int(item.get("active_connections") or 0),
            "exists_on_server": True,
            "connected_projects": [],
        }

    for item in list_all_mysql_databases():
        name = str(item.get("db_name") or "")
        if not name:
            continue
        db_entries[("mysql", name.lower())] = {
            "db_type": "mysql",
            "db_name": name,
            "host": item.get("host") or os.getenv("MYSQL_HOST", "mysql"),
            "port": int(item.get("port") or 3306),
            "size_bytes": int(item.get("size_bytes") or 0),
            "tables_count": int(item.get("tables_count") or 0),
            "active_connections": int(item.get("active_connections") or 0),
            "exists_on_server": True,
            "connected_projects": [],
        }

    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    for proj in projects:
        engine = _normalize_db_engine(proj.db_type)
        if engine == "none":
            continue
        safe_env = _get_safe_project_env_map(db, proj.id)
        resolved_name = resolve_project_db_name(proj, safe_env)
        runtime = _inspect_project_runtime(proj, db)
        entry_key = (engine, resolved_name.lower())
        if entry_key not in db_entries:
            db_entries[entry_key] = {
                "db_type": engine,
                "db_name": resolved_name,
                "host": (
                    "localhost"
                    if engine == "sqlite"
                    else (os.getenv("MYSQL_HOST", "mysql") if engine == "mysql" else (settings.POSTGRES_HOST or "postgres"))
                ),
                "port": 0 if engine == "sqlite" else (3306 if engine == "mysql" else 5432),
                "size_bytes": 0,
                "tables_count": 0,
                "active_connections": 0,
                "exists_on_server": False,
                "connected_projects": [],
            }
        db_entries[entry_key]["connected_projects"].append(_build_connected_project_entry(proj, runtime))

    db_list: List[Dict[str, Any]] = []
    for entry in db_entries.values():
        connected = entry["connected_projects"]
        entry["connected_projects_count"] = len(connected)
        entry["running_projects_count"] = sum(1 for cp in connected if cp.get("container_running"))
        entry["has_active_deployment"] = len(connected) > 0
        db_list.append(entry)

    db_list.sort(
        key=lambda d: (
            -(1 if d["running_projects_count"] > 0 else 0),
            -d["connected_projects_count"],
            d["db_type"],
            (d["db_name"] or "").lower(),
        )
    )

    return {
        "databases": db_list,
        "summary": {
            "total_databases": len(db_list),
            "connected_databases": sum(1 for d in db_list if d["connected_projects_count"] > 0),
            "active_deployed_links": sum(d["connected_projects_count"] for d in db_list),
            "engines": {
                "postgres": sum(1 for d in db_list if d["db_type"] == "postgres"),
                "mysql": sum(1 for d in db_list if d["db_type"] == "mysql"),
                "sqlite": sum(1 for d in db_list if d["db_type"] == "sqlite"),
            },
        },
    }


@router.post("", response_model=ProjectResponse)
async def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db)
):
    server = db.query(Server).filter(Server.id == payload.server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Target server not found")

    project_domain = resolve_effective_domain(payload.domain, payload.name)
    project_db_type = getattr(payload, "db_type", "postgres") or "none"
    resolved_ports, port_notice = validate_or_allocate_port_mappings(
        db,
        payload.port_mappings,
        framework=payload.framework,
        exclude_container_name=payload.name,
        auto_fix_reserved=True,
    )

    project = Project(
        server_id=payload.server_id,
        owner_id=current_user.id,
        name=payload.name,
        description=payload.description,
        framework=payload.framework,
        source_type=payload.source_type,
        port_mappings=resolved_ports,
        domain=project_domain,
        db_type=project_db_type,
        db_name=getattr(payload, "database_name", None),
        status="active"
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    # Create project source config
    sanitized_repo, sanitized_branch = sanitize_git_url_and_branch(payload.repository_url, payload.branch)
    source = ProjectSource(
        project_id=project.id,
        repository_url=sanitized_repo,
        branch=sanitized_branch or "main",
        dockerfile_path=payload.dockerfile_path or "Dockerfile",
        compose_path=payload.compose_path or "docker-compose.yml"
    )
    db.add(source)
    db.commit()

    # Save initial environment variables if provided
    if payload.environment_variables:
        for item in payload.environment_variables:
            if item.key and item.key.strip():
                encrypted = encrypt_secret(item.value or "")
                db_var = EnvironmentVariable(
                    project_id=project.id,
                    key=item.key.strip(),
                    encrypted_value=encrypted
                )
                db.add(db_var)
        db.commit()

    # Auto-provision & auto-connect database for the project (respecting explicit db_type)
    db_info = {}
    if getattr(payload, "auto_provision_db", True):
        try:
            db_info, _ = provision_and_sync_project_db(
                db=db,
                project=project,
                custom_db_name=getattr(payload, "database_name", None),
                db_type=project.db_type,
                override_existing_env=True,
            )
        except Exception:
            pass

    # Register Local DNS & Ingress Routing
    if project.domain:
        try:
            from apps.api.app.services.pap_client import sync_domain_routing
            hp = extract_host_port(project.port_mappings)
            sync_domain_routing(domain=project.domain, target_port=hp, project_name=project.name)
        except Exception:
            pass

    # Optional immediate deployment for Git projects
    deployment_id = None
    if payload.source_type == "git" and payload.repository_url and getattr(payload, "auto_deploy", True):
        try:
            version = 1
            image_tag = f"{project.name.lower().replace(' ', '-')}:v{version}"
            now = datetime.now(timezone.utc)
            deployment = Deployment(
                project_id=project.id,
                version=version,
                commit_hash="HEAD",
                image_tag=image_tag,
                status="building",
                triggered_by=current_user.id,
                started_at=now
            )
            db.add(deployment)
            db.commit()
            db.refresh(deployment)
            deployment_id = deployment.id

            db.add(
                DeploymentLog(
                    deployment_id=deployment.id,
                    sequence=1,
                    level="info",
                    message=f"Deployment v1 dimulai otomatis via Git ({source.repository_url} @ {source.branch})"
                )
            )
            if port_notice:
                db.add(
                    DeploymentLog(
                        deployment_id=deployment.id,
                        sequence=2,
                        level="warning",
                        message=f"[Port-Guard] {port_notice}",
                    )
                )
            db.commit()

            # Retrieve all stored decrypted environment variables
            env_items = db.query(EnvironmentVariable).filter(EnvironmentVariable.project_id == project.id).all()
            full_env = {}
            for item in env_items:
                try:
                    full_env[item.key] = decrypt_secret(item.encrypted_value)
                except Exception:
                    pass

            job_payload = {
                "deployment_id": deployment.id,
                "project_id": project.id,
                "project_name": project.name,
                "repository_url": source.repository_url,
                "branch": source.branch or "main",
                "commit_hash": None,
                "dockerfile_path": source.dockerfile_path or "Dockerfile",
                "compose_path": source.compose_path or "docker-compose.yml",
                "port_mappings": project.port_mappings,
                "image_tag": image_tag,
                "version": version,
                "db_type": project.db_type,
                "environment_variables": full_env,
                "database_config": db_info,
                "git_token": get_stored_token(),
            }

            await create_and_dispatch_job(
                db=db,
                server_id=project.server_id,
                job_type="deployment.git",
                payload=job_payload,
                deployment_id=deployment.id,
                priority=20
            )
        except Exception:
            pass

    log_activity(
        db,
        action="project.create",
        resource_type="project",
        resource_id=project.id,
        user_id=current_user.id,
        details={
            "name": project.name,
            "source_type": project.source_type,
            "port_mappings": project.port_mappings,
            "database": db_info.get("db_name"),
            "db_auto_created": db_info.get("created", False),
            "auto_deployed": deployment_id is not None,
            "pap_synced": bool(sync_psm_to_pap(db)),
        }
    )

    return _build_project_response(project, deployment_id=deployment_id)

@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return _build_project_response(p)

async def execute_project_redeploy(
    db: Session,
    project: Project,
    current_user_id: str
) -> DeploymentResponse:
    src = project.source
    if src and src.repository_url:
        s_url, s_branch = sanitize_git_url_and_branch(src.repository_url, src.branch)
        if s_url != src.repository_url or s_branch != src.branch:
            src.repository_url = s_url
            src.branch = s_branch
            db.commit()
            db.refresh(src)

    last_deployment = db.query(Deployment).filter(
        Deployment.project_id == project.id
    ).order_by(Deployment.version.desc()).first()
    version = (last_deployment.version + 1) if (last_deployment and last_deployment.version) else 1

    clean_proj_name = project.name.lower().replace(" ", "-")
    image_tag = f"{clean_proj_name}:v{version}"
    now = datetime.now(timezone.utc)

    deployment = Deployment(
        project_id=project.id,
        version=version,
        commit_hash="HEAD" if (src and src.repository_url) else None,
        image_tag=image_tag,
        status="building",
        triggered_by=current_user_id,
        started_at=now
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)

    source_desc = f"Git ({src.repository_url} @ {src.branch or 'main'})" if (src and src.repository_url) else "Server Archive"
    initial_log = DeploymentLog(
        deployment_id=deployment.id,
        sequence=1,
        level="info",
        message=f"Deployment v{version} triggered via Project Update / Redeploy to Server ({source_desc})"
    )
    db.add(initial_log)
    db.commit()

    safe_ports, port_notice = validate_or_allocate_port_mappings(
        db,
        project.port_mappings,
        framework=project.framework,
        exclude_project_id=project.id,
        exclude_container_name=project.name,
        auto_fix_reserved=True,
    )
    if safe_ports != project.port_mappings:
        project.port_mappings = safe_ports
        db.commit()
    if port_notice:
        db.add(
            DeploymentLog(
                deployment_id=deployment.id,
                sequence=2,
                level="warning",
                message=f"[Port-Guard] {port_notice}",
            )
        )
        db.commit()

    # Auto-provision DB if missing while preserving user-edited environment variables
    db_info, env_vars = provision_and_sync_project_db(
        db, project, custom_db_name=project.db_name, db_type=project.db_type, override_existing_env=False
    )

    # Collect all stored decrypted environment variables
    env_items = db.query(EnvironmentVariable).filter(EnvironmentVariable.project_id == project.id).all()
    full_env = dict(env_vars or {})
    for item in env_items:
        try:
            full_env[item.key] = decrypt_secret(item.encrypted_value)
        except Exception:
            pass

    # Build job payload depending on source type
    if src and src.repository_url:
        job_type = "deployment.git"
        job_payload = {
            "deployment_id": deployment.id,
            "project_id": project.id,
            "project_name": project.name,
            "framework": project.framework,
            "repository_url": src.repository_url,
            "branch": src.branch or "main",
            "commit_hash": None,
            "dockerfile_path": src.dockerfile_path or "Dockerfile",
            "compose_path": src.compose_path or "docker-compose.yml",
            "port_mappings": project.port_mappings,
            "image_tag": image_tag,
            "version": version,
            "db_type": project.db_type,
            "environment_variables": full_env,
            "database_config": db_info,
            "git_token": get_stored_token(),
        }
    elif src and src.upload_object_key and os.path.exists(src.upload_object_key):
        job_type = "deployment.zip"
        job_payload = {
            "deployment_id": deployment.id,
            "project_id": project.id,
            "project_name": project.name,
            "framework": project.framework,
            "zip_path": src.upload_object_key,
            "image_tag": image_tag,
            "version": version,
            "dockerfile_path": src.dockerfile_path or "Dockerfile",
            "compose_path": src.compose_path or "docker-compose.yml",
            "port_mappings": project.port_mappings,
            "db_type": project.db_type,
            "environment_variables": full_env,
            "database_config": db_info,
            "git_token": get_stored_token(),
        }
    else:
        raise HTTPException(
            status_code=400,
            detail="Project tidak memiliki repositori Git maupun file archive ZIP yang tersimpan di server. Silakan deploy rilis baru dengan Git atau upload file ZIP terlebih dahulu."
        )

    # Ensure DNS & Ingress Virtual Host routing is synchronized on every redeploy
    try:
        eff_domain = resolve_effective_domain(project.domain, project.name)
        hp = extract_host_port(project.port_mappings)
        sync_domain_routing(
            domain=eff_domain,
            target_port=hp,
            project_name=project.name,
            maintenance_mode=bool(getattr(project, "maintenance_mode", False)),
            maintenance_title=getattr(project, "maintenance_title", None),
            maintenance_message=getattr(project, "maintenance_message", None),
            maintenance_eta=getattr(project, "maintenance_eta", None),
        )
    except Exception:
        pass

    job = await create_and_dispatch_job(
        db=db,
        server_id=project.server_id,
        job_type=job_type,
        payload=job_payload,
        deployment_id=deployment.id,
        priority=20
    )

    log_activity(
        db,
        action="project.redeploy",
        resource_type="deployment",
        resource_id=deployment.id,
        user_id=current_user_id,
        details={"project": project.name, "version": version, "job_id": job.id, "job_type": job_type}
    )

    return DeploymentResponse(
        id=deployment.id,
        project_id=project.id,
        project_name=project.name,
        version=version,
        commit_hash=deployment.commit_hash,
        image_tag=deployment.image_tag,
        status=deployment.status,
        triggered_by=current_user_id,
        started_at=deployment.started_at,
        finished_at=None,
        duration_seconds=0,
        created_at=deployment.created_at,
    )


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db)
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    old_domain = resolve_effective_domain(p.domain, p.name)

    if payload.name is not None:
        p.name = payload.name
    if payload.description is not None:
        p.description = payload.description
    if payload.framework is not None:
        p.framework = payload.framework
    if payload.port_mappings is not None:
        resolved_ports, _ = validate_or_allocate_port_mappings(
            db,
            payload.port_mappings,
            framework=payload.framework or p.framework,
            exclude_project_id=p.id,
            exclude_container_name=p.name,
            auto_fix_reserved=bool(payload.redeploy),
        )
        p.port_mappings = resolved_ports
    if payload.domain is not None:
        p.domain = resolve_effective_domain(payload.domain, p.name) if payload.domain.strip() else None
    if payload.db_type is not None:
        p.db_type = payload.db_type
        # Immediately re-provision and sync environment variables when database engine is updated
        try:
            from apps.api.app.services.db_provision_service import provision_and_sync_project_db
            provision_and_sync_project_db(
                db=db,
                project=p,
                custom_db_name=payload.db_name or p.db_name,
                db_type=p.db_type,
                override_existing_env=True
            )
        except Exception:
            pass
    if payload.db_name is not None:
        p.db_name = payload.db_name
    if payload.maintenance_mode is not None:
        p.maintenance_mode = bool(payload.maintenance_mode)
        p.status = "maintenance" if p.maintenance_mode else "active"
    if payload.maintenance_title is not None:
        p.maintenance_title = payload.maintenance_title.strip() or None
    if payload.maintenance_message is not None:
        p.maintenance_message = payload.maintenance_message.strip() or None
    if payload.maintenance_eta is not None:
        p.maintenance_eta = payload.maintenance_eta.strip() or None

    src = p.source
    if src:
        if payload.repository_url is not None or payload.branch is not None:
            raw_url = payload.repository_url if payload.repository_url is not None else src.repository_url
            raw_branch = payload.branch if payload.branch is not None else src.branch
            s_url, s_branch = sanitize_git_url_and_branch(raw_url, raw_branch)
            if payload.repository_url is not None:
                src.repository_url = s_url
            if payload.branch is not None or (s_branch and s_branch != raw_branch):
                src.branch = s_branch
        if payload.dockerfile_path is not None:
            src.dockerfile_path = payload.dockerfile_path
        if payload.compose_path is not None:
            src.compose_path = payload.compose_path

    db.commit()
    db.refresh(p)

    # Sync DNS, Ingress Routing & direct container maintenance if domain/port/maintenance changed
    new_domain = resolve_effective_domain(p.domain, p.name)
    maint_touched = any(
        val is not None
        for val in (
            payload.maintenance_mode,
            payload.maintenance_title,
            payload.maintenance_message,
            payload.maintenance_eta,
        )
    )
    if payload.port_mappings is not None or payload.domain is not None or maint_touched:
        try:
            if old_domain and old_domain != new_domain:
                remove_domain_routing(old_domain)
            _sync_project_maintenance_targets(p)
        except Exception:
            pass

    log_activity(
        db,
        action="project.update",
        resource_type="project",
        resource_id=p.id,
        user_id=current_user.id
    )

    deployment_id = None
    if payload.redeploy:
        try:
            dep = await execute_project_redeploy(db=db, project=p, current_user_id=current_user.id)
            deployment_id = dep.id
        except Exception:
            pass

    return _build_project_response(p, deployment_id=deployment_id)


@router.post("/{project_id}/maintenance")
def set_project_maintenance(
    project_id: str,
    payload: ProjectMaintenanceRequest,
    current_user: User = Depends(require_role("owner", "admin", "operator")),
    db: Session = Depends(get_db),
):
    """Enable or disable Maintenance Mode for a project and configure its maintenance page."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    p.maintenance_mode = bool(payload.enabled)
    p.status = "maintenance" if payload.enabled else "active"
    if payload.title is not None:
        p.maintenance_title = payload.title.strip() or None
    if payload.message is not None:
        p.maintenance_message = payload.message.strip() or None
    if payload.eta is not None:
        p.maintenance_eta = payload.eta.strip() or None

    db.commit()
    db.refresh(p)

    sync_res = _sync_project_maintenance_targets(p, retry_after=payload.retry_after)
    container_sync = sync_res["container_sync"]

    log_activity(
        db,
        action="project.maintenance_toggle",
        resource_type="project",
        resource_id=p.id,
        user_id=current_user.id,
        details={
            "project": p.name,
            "enabled": p.maintenance_mode,
            "router_synced": sync_res["router_synced"],
            "container_driver": container_sync.get("driver"),
        },
    )

    state_label = "DIAKTIFKAN (HTTP 503)" if p.maintenance_mode else "DINONAKTIFKAN (ONLINE)"
    extra_note = (
        " Container Laravel juga masuk mode maintenance (IP:Port langsung)."
        if container_sync.get("applied")
        else (
            " Berlaku pada domain Nginx Ingress (:80)."
            if sync_res["router_synced"]
            else ""
        )
    )

    return {
        "success": True,
        "project": _build_project_response(p),
        "maintenance_mode": p.maintenance_mode,
        "maintenance_title": p.maintenance_title,
        "maintenance_message": p.maintenance_message,
        "maintenance_eta": p.maintenance_eta,
        "router_synced": sync_res["router_synced"],
        "container_sync": container_sync,
        "preview_url": f"/api/v1/projects/{p.id}/maintenance/preview",
        "message": f"Mode Maintenance project '{p.name}' {state_label}.{extra_note}",
    }


@router.get("/{project_id}/maintenance/preview", response_class=HTMLResponse)
def preview_project_maintenance_page(
    project_id: str,
    title: Optional[str] = None,
    message: Optional[str] = None,
    eta: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Render public HTML preview of this project's maintenance page (no secrets exposed)."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    eff_domain = resolve_effective_domain(p.domain, p.name)
    html_content = render_maintenance_html(
        project_name=p.name,
        domain=eff_domain,
        title=title if (title is not None and title != "") else p.maintenance_title,
        message=message if (message is not None and message != "") else p.maintenance_message,
        eta=eta if (eta is not None and eta != "") else p.maintenance_eta,
    )
    return HTMLResponse(content=html_content, status_code=200)


@router.post("/{project_id}/sync-domain")
def sync_project_domain_endpoint(
    project_id: str,
    current_user: User = Depends(require_role("owner", "admin", "operator")),
    db: Session = Depends(get_db)
):
    """Synchronize this project's domain virtual host route to PAP DNS & Ingress Router."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    eff_domain = resolve_effective_domain(p.domain, p.name)
    if p.domain != eff_domain:
        p.domain = eff_domain
        db.commit()
        db.refresh(p)

    hp = extract_host_port(p.port_mappings)
    synced = sync_domain_routing(
        domain=eff_domain,
        target_port=hp,
        project_name=p.name,
        maintenance_mode=bool(getattr(p, "maintenance_mode", False)),
        maintenance_title=getattr(p, "maintenance_title", None),
        maintenance_message=getattr(p, "maintenance_message", None),
        maintenance_eta=getattr(p, "maintenance_eta", None),
    )
    resp = _build_project_response(p)
    return {
        "success": synced,
        "domain": eff_domain,
        "target_port": hp,
        "access_urls": resp.access_urls,
        "message": (
            f"Domain '{eff_domain}' disinkronkan ke port {hp}."
            if synced
            else f"Route lokal disiapkan untuk '{eff_domain}' -> port {hp}."
        ),
    }


@router.post("/{project_id}/redeploy", response_model=DeploymentResponse)
async def redeploy_project(
    project_id: str,
    current_user: User = Depends(require_role("owner", "admin", "operator")),
    db: Session = Depends(get_db)
):
    """
    Trigger immediate redeployment of project to the server container
    using current settings, database configuration, and environment variables.
    """
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return await execute_project_redeploy(db=db, project=p, current_user_id=current_user.id)

@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db)
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    # Remove Local DNS & Ingress Routing
    eff_domain = resolve_effective_domain(p.domain, p.name)
    if eff_domain:
        try:
            remove_domain_routing(eff_domain)
        except Exception:
            pass

    db.delete(p)
    db.commit()

    log_activity(
        db,
        action="project.delete",
        resource_type="project",
        resource_id=project_id,
        user_id=current_user.id
    )
    return {"message": "Project deleted successfully"}

@router.get("/{project_id}/environment", response_model=List[EnvironmentItemResponse])
def get_project_environment(
    project_id: str,
    reveal: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    can_reveal = bool(reveal and getattr(current_user, "role", "") in ("owner", "admin"))
    items = (
        db.query(EnvironmentVariable)
        .filter(EnvironmentVariable.project_id == project_id)
        .order_by(EnvironmentVariable.key.asc())
        .all()
    )
    results = []
    for item in items:
        plain_val: Optional[str] = None
        try:
            decrypted = decrypt_secret(item.encrypted_value)
            masked = mask_secret(decrypted)
            if can_reveal:
                plain_val = decrypted
        except Exception:
            masked = "********"
        results.append(
            EnvironmentItemResponse(
                key=item.key,
                masked_value=masked,
                value=plain_val,
                updated_at=item.updated_at,
            )
        )
    return results


@router.put("/{project_id}/environment")
def update_project_environment(
    project_id: str,
    payload: EnvironmentUpdateRequest,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db)
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    deduped: Dict[str, str] = {}
    for item in payload.variables:
        clean_key = (item.key or "").strip()
        if not clean_key:
            continue
        if not _ENV_KEY_RE.match(clean_key):
            raise HTTPException(
                status_code=400,
                detail=f"Nama key environment tidak valid: '{clean_key}'",
            )
        deduped[clean_key] = item.value if item.value is not None else ""

    # Replace variables with encrypted values
    db.query(EnvironmentVariable).filter(EnvironmentVariable.project_id == project_id).delete()

    for clean_key, val in deduped.items():
        db.add(
            EnvironmentVariable(
                project_id=project_id,
                key=clean_key,
                encrypted_value=encrypt_secret(val),
            )
        )

    db.commit()

    log_activity(
        db,
        action="project.environment_update",
        resource_type="project",
        resource_id=project_id,
        user_id=current_user.id,
        details={"keys_updated": list(deduped.keys())}
    )
    return {"message": "Environment variables encrypted and updated", "count": len(deduped)}


@router.post("/{project_id}/environment/item", response_model=EnvironmentItemResponse)
def upsert_project_environment_item(
    project_id: str,
    payload: EnvironmentVariableUpsertRequest,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    clean_key = (payload.key or "").strip()
    if not clean_key or not _ENV_KEY_RE.match(clean_key):
        raise HTTPException(
            status_code=400,
            detail=f"Nama key environment tidak valid: '{clean_key or '(kosong)'}'",
        )

    val_str = payload.value if payload.value is not None else ""
    enc = encrypt_secret(val_str)
    now = datetime.now(timezone.utc)
    orig_key = (payload.original_key or "").strip()

    if orig_key and orig_key != clean_key:
        orig_row = (
            db.query(EnvironmentVariable)
            .filter(EnvironmentVariable.project_id == project_id, EnvironmentVariable.key == orig_key)
            .first()
        )
        target_row = (
            db.query(EnvironmentVariable)
            .filter(EnvironmentVariable.project_id == project_id, EnvironmentVariable.key == clean_key)
            .first()
        )
        if target_row:
            target_row.encrypted_value = enc
            target_row.updated_at = now
            if orig_row and orig_row.id != target_row.id:
                db.delete(orig_row)
            row = target_row
        elif orig_row:
            orig_row.key = clean_key
            orig_row.encrypted_value = enc
            orig_row.updated_at = now
            row = orig_row
        else:
            row = EnvironmentVariable(
                project_id=project_id, key=clean_key, encrypted_value=enc, updated_at=now
            )
            db.add(row)
    else:
        row = (
            db.query(EnvironmentVariable)
            .filter(EnvironmentVariable.project_id == project_id, EnvironmentVariable.key == clean_key)
            .first()
        )
        if row:
            row.encrypted_value = enc
            row.updated_at = now
        else:
            row = EnvironmentVariable(
                project_id=project_id, key=clean_key, encrypted_value=enc, updated_at=now
            )
            db.add(row)

    db.commit()
    db.refresh(row)

    log_activity(
        db,
        action="project.environment_upsert",
        resource_type="project",
        resource_id=project_id,
        user_id=current_user.id,
        details={
            "key": clean_key,
            "renamed_from": orig_key if (orig_key and orig_key != clean_key) else None,
        },
    )

    return EnvironmentItemResponse(
        key=row.key,
        masked_value=mask_secret(val_str),
        value=val_str,
        updated_at=row.updated_at,
    )


def _is_masked_corrupted_value(val: Optional[str]) -> bool:
    return isinstance(val, str) and "****" in val


def _extract_container_port(port_mappings: Optional[str], framework: str) -> int:
    if port_mappings:
        first_entry = port_mappings.split(",")[0].strip()
        if ":" in first_entry:
            right = first_entry.split(":", 1)[1].split("/", 1)[0].strip()
            try:
                port_num = int(right)
                if port_num > 0:
                    return port_num
            except Exception:
                pass
    fw = (framework or "").lower()
    if "laravel" in fw or "php" in fw or "static" in fw:
        return 80
    if "fastapi" in fw or "django" in fw:
        return 8000
    if "flask" in fw:
        return 5000
    return 3000


def _read_workspace_env_example(p: Project) -> Dict[str, str]:
    raw_name = (p.name or "").strip()
    if not raw_name:
        return {}
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw_name.lower()).strip("-")
    name_variants = [n for n in dict.fromkeys([raw_name, slug]) if n]
    base_dirs = [
        os.path.join(settings.STORAGE_BASE_DIR, "workspaces"),
        "/opt/psm/workspaces",
        settings.RELEASE_DIR,
    ]
    parsed: Dict[str, str] = {}
    for base in base_dirs:
        if not base:
            continue
        for variant in name_variants:
            candidate = os.path.join(base, variant, ".env.example")
            if not os.path.isfile(candidate):
                continue
            try:
                with open(candidate, "r", encoding="utf-8", errors="ignore") as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        clean_k = k.strip()
                        if not clean_k or not _ENV_KEY_RE.match(clean_k):
                            continue
                        clean_v = v.strip()
                        if (
                            len(clean_v) >= 2
                            and (
                                (clean_v.startswith('"') and clean_v.endswith('"'))
                                or (clean_v.startswith("'") and clean_v.endswith("'"))
                            )
                        ):
                            clean_v = clean_v[1:-1]
                        parsed[clean_k] = clean_v
                if parsed:
                    return parsed
            except Exception:
                continue
    return parsed


def _build_framework_default_env(
    p: Project,
    existing_env: Dict[str, str],
    app_env: Optional[str],
    app_url: Optional[str],
    preserve_app_key: bool,
    regenerate_app_key: bool,
) -> Dict[str, str]:
    defaults = _read_workspace_env_example(p)
    fw = (p.framework or "").lower()
    host_port = extract_host_port(p.port_mappings)
    container_port = _extract_container_port(p.port_mappings, p.framework)
    resolved_url = (app_url or "").strip() or (
        f"http://{settings.SERVER_LAN_IP}:{host_port}"
        if host_port
        else f"http://{resolve_effective_domain(p.domain, p.name)}"
    )

    if "laravel" in fw or "php" in fw:
        target_app_env = (app_env or "").strip() or "local"
        existing_app_key = existing_env.get("APP_KEY", "")
        keep_existing_key = (
            not regenerate_app_key
            and preserve_app_key
            and existing_app_key.startswith("base64:")
            and not _is_masked_corrupted_value(existing_app_key)
            and len(existing_app_key) >= 20
        )
        app_key = (
            existing_app_key
            if keep_existing_key
            else "base64:" + base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        )
        existing_name = (existing_env.get("APP_NAME") or "").strip()
        clean_app_name = (
            existing_name
            if existing_name and not _is_masked_corrupted_value(existing_name)
            else p.name
        )
        defaults.update({
            "APP_NAME": clean_app_name,
            "APP_ENV": target_app_env,
            "APP_DEBUG": "true" if target_app_env == "local" else "false",
            "APP_KEY": app_key,
            "APP_URL": resolved_url,
            "LOG_CHANNEL": "stderr",
            "SESSION_DRIVER": "file",
            "CACHE_STORE": "file",
            "QUEUE_CONNECTION": "sync",
        })
    elif any(tok in fw for tok in ("next", "node", "react", "vite")):
        target_node_env = (app_env or "").strip() or "production"
        defaults.update({
            "NODE_ENV": target_node_env,
            "PORT": str(container_port),
            ("NEXT_PUBLIC_APP_URL" if "next" in fw else "APP_URL"): resolved_url,
        })
    else:
        target_app_env = (app_env or "").strip() or "production"
        defaults.update({
            "APP_ENV": target_app_env,
            "PORT": str(container_port),
            "APP_URL": resolved_url,
        })

    return defaults


@router.post("/{project_id}/environment/reset")
def reset_project_environment(
    project_id: str,
    payload: EnvironmentResetRequest,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Repair corrupted/misconfigured environment variables or cleanly re-setup defaults + DB credentials."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    mode = (payload.mode or "repair").strip().lower()
    if mode not in ("repair", "clean"):
        raise HTTPException(status_code=400, detail="Mode setup ulang harus 'repair' atau 'clean'")

    existing_rows = (
        db.query(EnvironmentVariable)
        .filter(EnvironmentVariable.project_id == project_id)
        .all()
    )
    existing_env: Dict[str, str] = {}
    for row in existing_rows:
        try:
            existing_env[row.key] = decrypt_secret(row.encrypted_value)
        except Exception:
            existing_env[row.key] = "********"

    corrupted_keys = [k for k, v in existing_env.items() if _is_masked_corrupted_value(v)]
    defaults = _build_framework_default_env(
        p=p,
        existing_env=existing_env,
        app_env=payload.app_env,
        app_url=payload.app_url,
        preserve_app_key=payload.preserve_app_key,
        regenerate_app_key=payload.regenerate_app_key,
    )

    fw = (p.framework or "").lower()
    is_laravel = "laravel" in fw or "php" in fw
    is_node = any(tok in fw for tok in ("next", "node", "react", "vite"))

    if mode == "clean":
        rebuilt_env = dict(defaults)
    else:
        rebuilt_env = {
            k: v for k, v in existing_env.items() if not _is_masked_corrupted_value(v)
        }
        for k, v in defaults.items():
            if k not in rebuilt_env or not str(rebuilt_env[k]).strip():
                rebuilt_env[k] = v

        if payload.app_env and payload.app_env.strip():
            if is_laravel:
                rebuilt_env["APP_ENV"] = defaults["APP_ENV"]
                rebuilt_env["APP_DEBUG"] = defaults["APP_DEBUG"]
            elif is_node:
                rebuilt_env["NODE_ENV"] = defaults["NODE_ENV"]
            else:
                rebuilt_env["APP_ENV"] = defaults["APP_ENV"]

        if payload.app_url and payload.app_url.strip():
            url_key = "NEXT_PUBLIC_APP_URL" if ("next" in fw and not is_laravel) else "APP_URL"
            rebuilt_env[url_key] = defaults[url_key]

        if is_laravel and (
            payload.regenerate_app_key
            or not rebuilt_env.get("APP_KEY")
            or _is_masked_corrupted_value(existing_env.get("APP_KEY"))
        ):
            rebuilt_env["APP_KEY"] = defaults["APP_KEY"]

    db.query(EnvironmentVariable).filter(EnvironmentVariable.project_id == project_id).delete()
    for clean_key, val in rebuilt_env.items():
        if not clean_key or not _ENV_KEY_RE.match(clean_key):
            continue
        db.add(
            EnvironmentVariable(
                project_id=project_id,
                key=clean_key,
                encrypted_value=encrypt_secret(str(val)),
            )
        )
    db.commit()
    db.expire_all()

    if payload.include_db_vars and _normalize_db_engine(p.db_type) != "none":
        provision_and_sync_project_db(
            db=db,
            project=p,
            custom_db_name=p.db_name,
            db_type=p.db_type,
            override_existing_env=True,
        )

    final_rows = (
        db.query(EnvironmentVariable)
        .filter(EnvironmentVariable.project_id == project_id)
        .order_by(EnvironmentVariable.key.asc())
        .all()
    )
    final_items = []
    for row in final_rows:
        try:
            decrypted = decrypt_secret(row.encrypted_value)
            masked = mask_secret(decrypted)
        except Exception:
            decrypted = ""
            masked = "********"
        final_items.append(
            EnvironmentItemResponse(
                key=row.key,
                masked_value=masked,
                value=decrypted,
                updated_at=row.updated_at,
            ).model_dump()
        )

    mode_label = "Reset Bersih" if mode == "clean" else "Perbaikan & Sinkronisasi"
    log_activity(
        db,
        action="project.environment_reset",
        resource_type="project",
        resource_id=project_id,
        user_id=current_user.id,
        details={
            "mode": mode,
            "count": len(final_items),
            "cleared_corrupted_keys": corrupted_keys,
        },
    )

    return {
        "message": f"Environment berhasil di-setup ulang ({mode_label}) dengan {len(final_items)} variabel.",
        "mode": mode,
        "count": len(final_items),
        "cleared_corrupted_keys": corrupted_keys,
        "variables": final_items,
    }


@router.delete("/{project_id}/environment/{env_key}")
def delete_project_environment_item(
    project_id: str,
    env_key: str,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    row = (
        db.query(EnvironmentVariable)
        .filter(EnvironmentVariable.project_id == project_id, EnvironmentVariable.key == env_key)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Environment variable '{env_key}' not found")

    db.delete(row)
    db.commit()

    log_activity(
        db,
        action="project.environment_delete",
        resource_type="project",
        resource_id=project_id,
        user_id=current_user.id,
        details={"key": env_key},
    )
    return {"message": f"Environment variable '{env_key}' deleted", "key": env_key}

@router.get("/{project_id}/deployments", response_model=List[DeploymentResponse])
def get_project_deployments(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    deployments = db.query(Deployment).filter(
        Deployment.project_id == project_id
    ).order_by(Deployment.created_at.desc()).all()
    return deployments


def _compute_deployment_connection(
    target_engine: str,
    clean_db: Optional[str],
    db_info: Dict[str, Any],
    runtime: Dict[str, Any],
    stored_env: Dict[str, str],
) -> Dict[str, Any]:
    container_db_env = runtime.get("container_db_env") or {}
    has_container_db_keys = any(
        k in container_db_env
        for k in (
            "DB_CONNECTION", "DB_DATABASE", "DB_NAME", "MYSQL_DATABASE",
            "POSTGRES_DB", "SQLITE_DATABASE", "DB_HOST", "MYSQL_HOST", "POSTGRES_HOST",
        )
    )
    if runtime.get("container_exists") and has_container_db_keys:
        eff_env = container_db_env
        env_source = "container"
    elif stored_env:
        eff_env = stored_env
        env_source = "project_env"
    else:
        eff_env = {}
        env_source = "none"

    env_db_connection = (
        eff_env.get("DB_CONNECTION")
        or ("mysql" if eff_env.get("MYSQL_DATABASE") else ("pgsql" if eff_env.get("POSTGRES_DB") else None))
    )
    env_db_host = eff_env.get("DB_HOST") or eff_env.get("MYSQL_HOST") or eff_env.get("POSTGRES_HOST")
    env_db_port = eff_env.get("DB_PORT") or eff_env.get("MYSQL_PORT") or eff_env.get("POSTGRES_PORT")
    env_db_name = (
        eff_env.get("DB_DATABASE")
        or eff_env.get("DB_NAME")
        or eff_env.get("MYSQL_DATABASE")
        or eff_env.get("POSTGRES_DB")
        or eff_env.get("SQLITE_DATABASE")
    )

    if target_engine == "none":
        env_matches_database = True
    else:
        conn_l = (env_db_connection or "").lower().strip()
        engine_ok = (
            (not conn_l)
            or (target_engine == "postgres" and conn_l in ("pgsql", "postgres", "postgresql"))
            or (target_engine == "mysql" and conn_l in ("mysql", "mariadb"))
            or (target_engine == "sqlite" and conn_l == "sqlite")
        )
        target_name = (clean_db or "").strip().lower()
        env_name = (env_db_name or "").strip().lower()
        name_ok = bool(target_name and env_name and (
            env_name == target_name
            or (target_engine == "sqlite" and (env_name.endswith(f"/{target_name}.db") or target_name in env_name))
        ))
        env_matches_database = bool(engine_ok and name_ok)

    db_connected = bool(db_info.get("connected", False))
    container_exists = bool(runtime.get("container_exists", False))
    container_running = bool(runtime.get("container_running", False))
    container_name = runtime.get("container_name") or ""
    active_sessions = int(db_info.get("active_connections", 0) or 0)

    if target_engine == "none":
        conn_status = "stateless"
        is_connected = False
        summary = "Project berjalan dalam mode stateless tanpa koneksi database."
    elif container_running:
        if not db_connected:
            conn_status = "degraded"
            is_connected = False
            summary = (
                f"Container '{container_name}' aktif (RUNNING), tetapi server {target_engine.upper()} "
                f"belum dapat diverifikasi ({db_info.get('error') or 'cek koneksi database'})."
            )
        elif not env_matches_database:
            conn_status = "mismatch"
            is_connected = False
            summary = (
                f"Container '{container_name}' berjalan, tetapi env DB ({env_db_connection or '?'} / "
                f"{env_db_name or '?'}) tidak cocok dengan database '{clean_db}' ({target_engine})."
            )
        else:
            conn_status = "connected"
            is_connected = True
            summary = (
                f"Terhubung aktif! Container '{container_name}' berjalan dan mengarah ke database "
                f"'{clean_db}' ({target_engine.upper()}) dengan {active_sessions} sesi aktif."
            )
    elif container_exists:
        conn_status = "disconnected"
        is_connected = False
        summary = (
            f"Container '{container_name}' berhenti (state: {runtime.get('container_state')}). "
            f"Database '{clean_db}' siap; restart atau deploy ulang container untuk menghubungkan kembali."
        )
    elif db_connected and env_matches_database:
        conn_status = "configured_standby"
        is_connected = False
        summary = (
            f"Database '{clean_db}' & environment sudah tersinkronisasi, menunggu project di-deploy ke container."
        )
    elif db_connected:
        conn_status = "configured_standby"
        is_connected = False
        summary = f"Database '{clean_db}' online di server, namun project belum di-deploy ke container Docker."
    else:
        conn_status = "disconnected"
        is_connected = False
        summary = f"Project belum di-deploy dan server database belum terhubung ({db_info.get('error') or 'offline'})."

    return {
        "is_connected": is_connected,
        "status": conn_status,
        "summary": summary,
        "container_exists": container_exists,
        "container_running": container_running,
        "container_name": container_name,
        "container_state": runtime.get("container_state") or "not_created",
        "container_networks": runtime.get("container_networks") or [],
        "latest_deployment_version": runtime.get("latest_deployment_version"),
        "latest_deployment_status": runtime.get("latest_deployment_status"),
        "env_source": env_source,
        "env_db_connection": env_db_connection,
        "env_db_host": env_db_host,
        "env_db_port": env_db_port,
        "env_db_name": env_db_name,
        "env_matches_database": env_matches_database,
        "active_db_sessions": active_sessions,
    }


@router.get("/{project_id}/database")
def get_project_database_info(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Inspect the database bound to this project (PostgreSQL, MySQL, or SQLite) and its live deployment link."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    target_engine = _normalize_db_engine(p.db_type)
    stored_env = _get_safe_project_env_map(db, p.id)
    db_name = resolve_project_db_name(p, stored_env)
    clean_db: Optional[str] = None

    if target_engine == "mysql":
        db_info = ensure_mysql_database(db_name)
        db_env = build_mysql_env_vars(db_info)
        masked_pass = mask_secret(db_info.get("password", ""))
        user = db_info.get("user", "fadb")
        host = db_info.get("host", settings.SERVER_LAN_IP)
        port = db_info.get("port", 3306)
        clean_db = db_info.get("db_name", db_name)
        conn_url_internal = f"mysql://{user}:{masked_pass}@{host}:{port}/{clean_db}"
        conn_url_external = f"mysql://{user}:{masked_pass}@{db_info.get('external_host', host)}:{port}/{clean_db}"
        raw_url = f"mysql://{user}:{db_info.get('password', '')}@{host}:{port}/{clean_db}"
    elif target_engine == "sqlite":
        db_info, db_env = build_sqlite_env_vars(db_name)
        clean_db = db_info.get("db_name", db_name)
        masked_pass = ""
        conn_url_internal = db_env.get("DATABASE_URL", f"sqlite:////data/{db_name}.db")
        conn_url_external = conn_url_internal
        raw_url = conn_url_internal
    elif target_engine == "none":
        db_info = {
            "db_type": "none",
            "db_name": None,
            "host": "N/A",
            "external_host": "N/A",
            "port": 0,
            "user": "N/A",
            "created": False,
            "connected": False,
            "tables": [],
            "tables_count": 0,
            "size_bytes": 0,
            "active_connections": 0,
        }
        db_env = {}
        masked_pass = ""
        conn_url_internal = "none"
        conn_url_external = "none"
        raw_url = ""
    else:  # postgres default
        db_info = ensure_postgres_database(db_name)
        db_env = build_postgres_env_vars(db_info)
        masked_pass = mask_secret(db_info.get("password", ""))
        user = db_info.get("user", "fadb")
        host = db_info.get("host", settings.SERVER_LAN_IP)
        port = db_info.get("port", 5432)
        clean_db = db_info.get("db_name", db_name)
        conn_url_internal = f"postgresql://{user}:{masked_pass}@{host}:{port}/{clean_db}"
        conn_url_external = f"postgresql://{user}:{masked_pass}@{db_info.get('external_host', host)}:{port}/{clean_db}"
        raw_url = f"postgresql://{user}:{db_info.get('password', '')}@{host}:{port}/{clean_db}"

    runtime = _inspect_project_runtime(p, db)
    deployment_connection = _compute_deployment_connection(
        target_engine=target_engine,
        clean_db=clean_db,
        db_info=db_info,
        runtime=runtime,
        stored_env=stored_env,
    )

    connected_projects: List[Dict[str, Any]] = []
    if target_engine != "none" and clean_db:
        all_projects = db.query(Project).order_by(Project.created_at.desc()).all()
        for other in all_projects:
            if _normalize_db_engine(other.db_type) != target_engine:
                continue
            other_env = stored_env if other.id == p.id else _get_safe_project_env_map(db, other.id)
            other_db = resolve_project_db_name(other, other_env)
            if other_db.lower() == clean_db.lower():
                other_runtime = runtime if other.id == p.id else _inspect_project_runtime(other, db)
                connected_projects.append(
                    _build_connected_project_entry(other, other_runtime, current_project_id=p.id)
                )

    return {
        **db_info,
        "masked_password": masked_pass,
        "masked_connection_url_internal": conn_url_internal,
        "masked_connection_url_external": conn_url_external,
        "connection_url_internal": raw_url,
        "env_preview": {
            k: (mask_secret(v) if "PASS" in k or "URL" in k or "URI" in k else v)
            for k, v in db_env.items()
        },
        "env_full": db_env,
        "deployment_connection": deployment_connection,
        "connected_projects": connected_projects,
    }


@router.post("/{project_id}/database/provision")
def provision_project_database_endpoint(
    project_id: str,
    payload: Optional[ProjectDatabaseProvisionRequest] = None,
    current_user: User = Depends(require_role("owner", "admin", "operator")),
    db: Session = Depends(get_db),
):
    """Ensure the project's database exists (PostgreSQL, MySQL, SQLite) and inject/sync connection environment variables."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    custom_db = payload.db_name if payload else None
    target_type = (payload.db_type if (payload and payload.db_type) else p.db_type) or "postgres"
    override = payload.override_env if payload else True

    db_info, merged_env = provision_and_sync_project_db(
        db=db,
        project=p,
        custom_db_name=custom_db,
        db_type=target_type,
        override_existing_env=override,
    )

    log_activity(
        db,
        action="project.database_provision",
        resource_type="project",
        resource_id=project_id,
        user_id=current_user.id,
        details={
            "project": p.name,
            "db_type": target_type,
            "db_name": db_info.get("db_name"),
            "created": db_info.get("created"),
            "host": db_info.get("host"),
        },
    )

    masked_pass = mask_secret(db_info.get("password", ""))
    return {
        **db_info,
        "masked_password": masked_pass,
        "synced_env_keys": list(merged_env.keys()),
    }


# ══════════════════════════════════════════════════════════════════════════════
# DOCKER CONTAINER RUNTIME & TERMINAL LOGS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{project_id}/container")
def get_project_container_status(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve runtime status, health, and state of the Docker container for this project."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    from apps.api.app.services.docker_service import DockerEngineService

    matched = DockerEngineService.find_container_by_project(p.name, p.id)
    if not matched:
        return {
            "exists": False,
            "container_name": p.name,
            "status": "not_created",
            "state": "not_created",
            "running": False,
            "message": f"Container Docker untuk project '{p.name}' belum aktif atau belum di-deploy.",
        }

    cid = matched.get("full_id") or matched.get("docker_id") or matched.get("name")
    inspected = DockerEngineService.inspect_container(cid)

    return {
        "exists": True,
        "docker_id": matched.get("docker_id"),
        "full_id": matched.get("full_id"),
        "container_name": matched.get("name"),
        "image": matched.get("image"),
        "status": matched.get("status"),
        "state": inspected.get("state", matched.get("state", "unknown")),
        "running": inspected.get("running", "Up" in matched.get("status", "")),
        "restarting": inspected.get("restarting", False),
        "oom_killed": inspected.get("oom_killed", False),
        "exit_code": inspected.get("exit_code"),
        "error": inspected.get("error", ""),
        "started_at": inspected.get("started_at", ""),
        "finished_at": inspected.get("finished_at", ""),
        "ports": inspected.get("ports", matched.get("ports", {})),
        "ip_address": inspected.get("ip_address", ""),
    }


@router.get("/{project_id}/container/logs")
def get_project_container_logs(
    project_id: str,
    tail: int = 200,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve live stdout & stderr terminal logs directly from the Docker container."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    from apps.api.app.services.docker_service import DockerEngineService

    matched = DockerEngineService.find_container_by_project(p.name, p.id)
    if not matched:
        return {
            "container_name": p.name,
            "exists": False,
            "status": "not_created",
            "state": "not_created",
            "logs": f"[PSM] Container '{p.name}' belum ditemukan di Docker daemon.\nKlik tombol 'Deploy Release' atau 'Deploy Latest' untuk membangun dan meluncurkan container aplikasi.",
        }

    cid = matched.get("full_id") or matched.get("docker_id") or matched.get("name")
    raw_logs = DockerEngineService.get_container_logs(cid, tail=tail)
    inspected = DockerEngineService.inspect_container(cid)

    return {
        "container_name": matched.get("name"),
        "docker_id": matched.get("docker_id"),
        "exists": True,
        "status": matched.get("status"),
        "state": inspected.get("state", matched.get("state", "unknown")),
        "running": inspected.get("running", "Up" in matched.get("status", "")),
        "exit_code": inspected.get("exit_code"),
        "error": inspected.get("error", ""),
        "logs": raw_logs,
    }


@router.post("/{project_id}/container/restart")
def restart_project_container_endpoint(
    project_id: str,
    current_user: User = Depends(require_role("owner", "admin", "operator")),
    db: Session = Depends(get_db),
):
    """Restart the Docker container for this project."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    from apps.api.app.services.docker_service import DockerEngineService

    matched = DockerEngineService.find_container_by_project(p.name, p.id)
    if not matched:
        raise HTTPException(status_code=404, detail=f"Container untuk '{p.name}' belum dibuat.")

    cid = matched.get("full_id") or matched.get("docker_id") or matched.get("name")
    success = DockerEngineService.restart_container(cid)

    log_activity(
        db,
        action="project.container_restart",
        resource_type="project",
        resource_id=project_id,
        user_id=current_user.id,
        details={"project": p.name, "container": matched.get("name"), "success": success},
    )

    if not success:
        raise HTTPException(status_code=500, detail="Gagal me-restart container.")

    return {
        "success": True,
        "message": f"Container '{matched.get('name')}' berhasil di-restart.",
    }


def _resolve_project_workspace_dir(project_id: str) -> str:
    import os
    from apps.api.app.core.config import settings

    candidates = [
        os.path.join(settings.STORAGE_BASE_DIR, "psm", "workspaces", project_id),
        os.path.join("./storage/psm/workspaces", project_id),
        os.path.join("./storage/workspaces", project_id),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


@router.get("/dockerfile/templates")
def get_dockerfile_templates_endpoint():
    """List all available professional multi-stage Dockerfile templates."""
    try:
        from apps.api.app.services.dockerfile_generator import get_available_templates
    except ImportError:
        from apps.agent.app.deployment.dockerfile_generator import get_available_templates
    return {
        "success": True,
        "templates": get_available_templates(),
    }


@router.post("/dockerfile/generate")
def generate_dockerfile_preview_endpoint(payload: DockerfileGenerateRequest):
    """Generate professional Dockerfile and .dockerignore for a framework & port."""
    try:
        from apps.api.app.services.dockerfile_generator import (
            generate_dockerfile,
            generate_dockerignore,
            TEMPLATES,
        )
    except ImportError:
        from apps.agent.app.deployment.dockerfile_generator import (
            generate_dockerfile,
            generate_dockerignore,
            TEMPLATES,
        )
    tmpl_key = payload.template_key
    if not tmpl_key:
        fw_clean = (payload.framework or "").lower()
        if "next" in fw_clean:
            tmpl_key = "nextjs"
        elif "vite" in fw_clean or "react" in fw_clean or "vue" in fw_clean:
            tmpl_key = "vite_spa"
        elif "fastapi" in fw_clean:
            tmpl_key = "fastapi"
        elif "django" in fw_clean:
            tmpl_key = "django"
        elif "flask" in fw_clean:
            tmpl_key = "flask"
        elif "laravel" in fw_clean or "php" in fw_clean:
            tmpl_key = "laravel"
        elif "go" in fw_clean:
            tmpl_key = "golang"
        elif "static" in fw_clean or "html" in fw_clean:
            tmpl_key = "static_html"
        else:
            tmpl_key = "node_backend"

    port = payload.port or TEMPLATES.get(tmpl_key, {}).get("default_port", 3000)
    df_content = generate_dockerfile(tmpl_key, port=port, project_name=payload.project_name)
    di_content = generate_dockerignore()

    return {
        "success": True,
        "template_key": tmpl_key,
        "template_name": TEMPLATES.get(tmpl_key, {}).get("name", "Custom"),
        "dockerfile": df_content,
        "dockerignore": di_content,
        "recommended_port": port,
    }


@router.get("/{project_id}/dockerfile")
def get_project_dockerfile_endpoint(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve existing or auto-generated Dockerfile and .dockerignore for this project."""
    import os
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        from apps.api.app.services.dockerfile_generator import (
            generate_dockerfile,
            generate_dockerignore,
            detect_project_stack,
            TEMPLATES,
        )
    except ImportError:
        from apps.agent.app.deployment.dockerfile_generator import (
            generate_dockerfile,
            generate_dockerignore,
            detect_project_stack,
            TEMPLATES,
        )

    ws_dir = _resolve_project_workspace_dir(project_id)
    df_rel = p.source.dockerfile_path if p.source and p.source.dockerfile_path else "Dockerfile"
    full_df = os.path.join(ws_dir, df_rel)
    full_di = os.path.join(ws_dir, ".dockerignore")

    df_content = ""
    di_content = ""
    exists = False
    is_generated = False

    # Check if files physically exist in project workspace
    if os.path.exists(full_df):
        try:
            with open(full_df, "r", encoding="utf-8", errors="ignore") as f:
                df_content = f.read()
            exists = True
        except Exception:
            pass

    if os.path.exists(full_di):
        try:
            with open(full_di, "r", encoding="utf-8", errors="ignore") as f:
                di_content = f.read()
        except Exception:
            pass

    # If no Dockerfile yet, auto-generate high-performance preview
    if not df_content:
        is_generated = True
        stack_info = detect_project_stack(ws_dir) if os.path.exists(ws_dir) else {}
        tmpl_key = stack_info.get("template_key")
        if not tmpl_key:
            fw_clean = (p.framework or "").lower()
            if "next" in fw_clean:
                tmpl_key = "nextjs"
            elif "vite" in fw_clean or "react" in fw_clean or "vue" in fw_clean:
                tmpl_key = "vite_spa"
            elif "fastapi" in fw_clean:
                tmpl_key = "fastapi"
            elif "django" in fw_clean:
                tmpl_key = "django"
            elif "flask" in fw_clean:
                tmpl_key = "flask"
            elif "laravel" in fw_clean or "php" in fw_clean:
                tmpl_key = "laravel"
            elif "go" in fw_clean:
                tmpl_key = "golang"
            elif "static" in fw_clean or "html" in fw_clean:
                tmpl_key = "static_html"
            else:
                tmpl_key = "node_backend"

        port = 3000
        if p.port_mappings and ":" in p.port_mappings:
            try:
                port = int(p.port_mappings.split(":")[-1].split(",")[0].strip())
            except Exception:
                port = 3000

        df_content = generate_dockerfile(tmpl_key, port=port, project_name=p.name)

    if not di_content:
        di_content = generate_dockerignore()

    return {
        "success": True,
        "project_id": p.id,
        "project_name": p.name,
        "framework": p.framework,
        "dockerfile_path": df_rel,
        "dockerfile": df_content,
        "dockerignore": di_content,
        "exists": exists,
        "is_generated": is_generated,
        "workspace_exists": os.path.exists(ws_dir),
    }


@router.put("/{project_id}/dockerfile")
def update_project_dockerfile_endpoint(
    project_id: str,
    payload: DockerfileUpdateRequest,
    current_user: User = Depends(require_role("owner", "admin", "operator")),
    db: Session = Depends(get_db),
):
    """Write or update Dockerfile & .dockerignore directly in the project workspace."""
    import os
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    ws_dir = _resolve_project_workspace_dir(project_id)
    os.makedirs(ws_dir, exist_ok=True)

    target_rel = payload.dockerfile_path or (p.source.dockerfile_path if p.source else "Dockerfile") or "Dockerfile"
    full_df = os.path.join(ws_dir, target_rel)
    os.makedirs(os.path.dirname(full_df), exist_ok=True)

    with open(full_df, "w", encoding="utf-8", newline="\n") as f:
        f.write(payload.dockerfile)

    if payload.dockerignore is not None:
        full_di = os.path.join(ws_dir, ".dockerignore")
        with open(full_di, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload.dockerignore)

    if p.source:
        p.source.dockerfile_path = target_rel
        db.commit()

    log_activity(
        db,
        action="project.dockerfile_update",
        resource_type="project",
        resource_id=project_id,
        user_id=current_user.id,
        details={"project": p.name, "dockerfile_path": target_rel},
    )

    return {
        "success": True,
        "message": f"Dockerfile berhasil disimpan ke workspace project ('{target_rel}').",
        "dockerfile_path": target_rel,
    }



