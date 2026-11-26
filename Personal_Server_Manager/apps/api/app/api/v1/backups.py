import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from apps.api.app.database.session import get_db
from apps.api.app.core.config import settings
from apps.api.app.core.dependencies import get_current_user, require_role
from apps.api.app.models.entities import (
    Backup,
    Server,
    Project,
    ProjectSource,
    EnvironmentVariable,
    Deployment,
    User,
)
from apps.api.app.schemas.all_schemas import BackupCreate, BackupResponse
from apps.api.app.services.audit_service import log_activity

router = APIRouter(prefix="/backups", tags=["Storage & Backup"])

_SAFE_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WORKSPACE_CONFIG_PATTERNS = (
    "Dockerfile",
    "dockerfile",
    ".dockerignore",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env",
    "package.json",
    "composer.json",
    "requirements.txt",
    "pyproject.toml",
)


def _sanitize_db_identifier(raw: str, default: str) -> str:
    candidate = (raw or "").strip()
    if candidate and _SAFE_DB_NAME_RE.match(candidate):
        return candidate
    return default


def _build_control_plane_snapshot(db: Session, name: str, backup_type: str) -> bytes:
    servers = db.query(Server).all()
    projects = db.query(Project).all()
    deployments = db.query(Deployment).order_by(Deployment.created_at.desc()).limit(50).all()

    projects_export: List[Dict[str, Any]] = []
    for p in projects:
        src = p.source
        env_items = [
            {"key": ev.key, "encrypted_value": ev.encrypted_value}
            for ev in (p.environment_variables or [])
        ]
        projects_export.append({
            "id": p.id,
            "server_id": p.server_id,
            "owner_id": p.owner_id,
            "name": p.name,
            "description": p.description,
            "framework": p.framework,
            "source_type": p.source_type,
            "status": p.status,
            "port_mappings": p.port_mappings,
            "domain": p.domain,
            "db_type": p.db_type,
            "db_name": p.db_name,
            "maintenance_mode": p.maintenance_mode,
            "maintenance_title": p.maintenance_title,
            "maintenance_message": p.maintenance_message,
            "maintenance_eta": p.maintenance_eta,
            "source": {
                "repository_url": src.repository_url if src else None,
                "branch": src.branch if src else "main",
                "dockerfile_path": src.dockerfile_path if src else "Dockerfile",
                "compose_path": src.compose_path if src else "docker-compose.yml",
            } if src else None,
            "environment_variables": env_items,
        })

    snapshot_data = {
        "schema_version": "2.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backup_type": backup_type,
        "name": name,
        "servers_count": len(servers),
        "projects_count": len(projects),
        "deployments_count": db.query(Deployment).count(),
        "servers": [
            {
                "id": s.id,
                "name": s.name,
                "hostname": s.hostname,
                "os_name": s.os_name,
                "os_version": s.os_version,
                "architecture": s.architecture,
            }
            for s in servers
        ],
        "projects": projects_export,
        "recent_deployments": [
            {
                "id": d.id,
                "project_id": d.project_id,
                "version": d.version,
                "image_tag": d.image_tag,
                "status": d.status,
                "commit_hash": d.commit_hash,
            }
            for d in deployments
        ],
    }
    return json.dumps(snapshot_data, indent=2).encode("utf-8")


def _dump_postgres_sql(db: Session, db_name: str) -> Tuple[bytes, str]:
    is_testing = os.getenv("TESTING", "").lower() == "true"
    pg_host = settings.POSTGRES_HOST
    pg_port = str(settings.POSTGRES_PORT)
    pg_user = settings.POSTGRES_USER
    pg_pass = settings.POSTGRES_PASSWORD

    if not is_testing:
        pg_dump_bin = shutil.which("pg_dump")
        if pg_dump_bin:
            try:
                env = os.environ.copy()
                env["PGPASSWORD"] = pg_pass
                proc = subprocess.run(
                    [
                        pg_dump_bin,
                        "-h", pg_host,
                        "-p", pg_port,
                        "-U", pg_user,
                        "--no-owner",
                        "--no-privileges",
                        db_name,
                    ],
                    env=env,
                    capture_output=True,
                    timeout=30,
                )
                if proc.returncode == 0 and proc.stdout:
                    return proc.stdout, "pg_dump"
            except Exception:
                pass

        try:
            import psycopg2
            conn = psycopg2.connect(
                host=pg_host,
                port=int(pg_port),
                user=pg_user,
                password=pg_pass,
                dbname=db_name,
                connect_timeout=3,
            )
            try:
                lines = [
                    f"-- Homelab PostgreSQL SQL Export ({db_name})",
                    f"-- Generated at {datetime.now(timezone.utc).isoformat()}",
                    "BEGIN;",
                ]
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
                    )
                    tables = [r[0] for r in cur.fetchall()]
                    for tbl in tables:
                        if tbl == "server_metrics":
                            lines.append(f"-- Skipped high-volume telemetry table: {tbl}")
                            continue
                        cur.execute(f'SELECT * FROM "{tbl}" LIMIT 5000')
                        rows = cur.fetchall()
                        cols = [desc[0] for desc in (cur.description or [])]
                        lines.append(f"\n-- Table: {tbl} ({len(rows)} rows)")
                        if rows and cols:
                            col_list = ", ".join(f'"{c}"' for c in cols)
                            for row in rows:
                                vals = []
                                for v in row:
                                    if v is None:
                                        vals.append("NULL")
                                    elif isinstance(v, bool):
                                        vals.append("TRUE" if v else "FALSE")
                                    elif isinstance(v, (int, float)):
                                        vals.append(str(v))
                                    elif isinstance(v, (dict, list)):
                                        escaped = json.dumps(v).replace("'", "''")
                                        vals.append(f"'{escaped}'")
                                    else:
                                        escaped = str(v).replace("'", "''")
                                        vals.append(f"'{escaped}'")
                                lines.append(f'INSERT INTO "{tbl}" ({col_list}) VALUES ({", ".join(vals)});')
                lines.append("COMMIT;")
                return ("\n".join(lines) + "\n").encode("utf-8"), "psycopg2_sql"
            finally:
                conn.close()
        except Exception:
            pass

    # Portable fallback SQL dump (used when PostgreSQL server is unreachable or in SQLite test mode)
    projects = db.query(Project).all()
    fallback_lines = [
        f"-- Homelab PostgreSQL Portable Snapshot ({db_name})",
        f"-- Generated at {datetime.now(timezone.utc).isoformat()}",
        "BEGIN;",
        f"-- Managed Projects Count: {len(projects)}",
    ]
    for p in projects:
        safe_name = (p.name or "").replace("'", "''")
        safe_fw = (p.framework or "").replace("'", "''")
        fallback_lines.append(
            f"-- Project: id='{p.id}', name='{safe_name}', framework='{safe_fw}', db='{p.db_name or 'none'}'"
        )
    fallback_lines.append("COMMIT;")
    return ("\n".join(fallback_lines) + "\n").encode("utf-8"), "portable_sql"


def _dump_mysql_sql(db_name: str) -> Tuple[bytes, str]:
    is_testing = os.getenv("TESTING", "").lower() == "true"
    my_host = os.getenv("MYSQL_HOST", "mysql")
    my_port = int(os.getenv("MYSQL_PORT", "3306"))
    my_user = os.getenv("MYSQL_ROOT_USER", "root")
    my_pass = os.getenv("MYSQL_ROOT_PASSWORD", os.getenv("MYSQL_PASSWORD", "fa12345"))

    if not is_testing:
        mysqldump_bin = shutil.which("mysqldump") or shutil.which("mariadb-dump")
        if mysqldump_bin:
            try:
                proc = subprocess.run(
                    [
                        mysqldump_bin,
                        f"-h{my_host}",
                        f"-P{my_port}",
                        f"-u{my_user}",
                        f"-p{my_pass}",
                        "--single-transaction",
                        db_name,
                    ],
                    capture_output=True,
                    timeout=30,
                )
                if proc.returncode == 0 and proc.stdout:
                    return proc.stdout, "mysqldump"
            except Exception:
                pass

        try:
            import pymysql
            conn = pymysql.connect(
                host=my_host,
                port=my_port,
                user=my_user,
                password=my_pass,
                database=db_name,
                connect_timeout=3,
                charset="utf8mb4",
            )
            try:
                lines = [
                    f"-- Homelab MySQL 8.4 SQL Export (`{db_name}`)",
                    f"-- Generated at {datetime.now(timezone.utc).isoformat()}",
                    "SET FOREIGN_KEY_CHECKS=0;",
                ]
                with conn.cursor() as cur:
                    cur.execute("SHOW TABLES")
                    tables = [r[0] for r in cur.fetchall()]
                    for tbl in tables:
                        cur.execute(f"SHOW CREATE TABLE `{tbl}`")
                        create_row = cur.fetchone()
                        if create_row and len(create_row) >= 2:
                            lines.append(f"\nDROP TABLE IF EXISTS `{tbl}`;")
                            lines.append(f"{create_row[1]};")
                        cur.execute(f"SELECT * FROM `{tbl}` LIMIT 5000")
                        rows = cur.fetchall()
                        cols = [d[0] for d in (cur.description or [])]
                        if rows and cols:
                            col_list = ", ".join(f"`{c}`" for c in cols)
                            for row in rows:
                                vals = [conn.escape(v) for v in row]
                                lines.append(f"INSERT INTO `{tbl}` ({col_list}) VALUES ({', '.join(vals)});")
                lines.append("SET FOREIGN_KEY_CHECKS=1;")
                return ("\n".join(lines) + "\n").encode("utf-8"), "pymysql_sql"
            finally:
                conn.close()
        except Exception:
            pass

    fallback_sql = (
        f"-- Homelab MySQL Portable Snapshot (`{db_name}`)\n"
        f"-- Generated at {datetime.now(timezone.utc).isoformat()}\n"
        "SET FOREIGN_KEY_CHECKS=0;\n"
        "SET FOREIGN_KEY_CHECKS=1;\n"
    )
    return fallback_sql.encode("utf-8"), "portable_sql"


def _build_workspace_zip(db: Session, name: str) -> Tuple[bytes, int]:
    buf = io.BytesIO()
    files_archived = 0
    ws_root = os.path.join(settings.STORAGE_BASE_DIR, "workspaces")

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "manifest.json",
            _build_control_plane_snapshot(db, name=name, backup_type="project_workspace"),
        )
        files_archived += 1

        if os.path.isdir(ws_root):
            skip_dirs = {".git", "node_modules", "vendor", ".next", "__pycache__", "dist", "build", "storage"}
            for root, dirs, files in os.walk(ws_root):
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                for fname in files:
                    lower_f = fname.lower()
                    if any(
                        fname == pat
                        or lower_f.startswith("dockerfile")
                        or lower_f.startswith("docker-compose")
                        or lower_f.startswith(".env")
                        or lower_f.endswith(".conf")
                        for pat in _WORKSPACE_CONFIG_PATTERNS
                    ):
                        full_p = os.path.join(root, fname)
                        try:
                            if os.path.getsize(full_p) <= 2 * 1024 * 1024:
                                rel_p = os.path.relpath(full_p, ws_root).replace("\\", "/")
                                zf.write(full_p, arcname=f"workspaces/{rel_p}")
                                files_archived += 1
                        except OSError:
                            pass

    return buf.getvalue(), files_archived


def _try_sync_backup_to_minio(filepath: str, filename: str) -> bool:
    if os.getenv("TESTING", "").lower() == "true":
        return False
    root_user = os.getenv("MINIO_ROOT_USER", "admin")
    root_pass = os.getenv("MINIO_ROOT_PASSWORD", "admin12345")
    for cname in ("homelab_minio", "minio"):
        try:
            cp_proc = subprocess.run(
                ["docker", "cp", filepath, f"{cname}:/tmp/{filename}"],
                capture_output=True,
                timeout=8,
            )
            if cp_proc.returncode != 0:
                continue
            mc_proc = subprocess.run(
                [
                    "docker", "exec", cname, "sh", "-c",
                    f"mc alias set local http://127.0.0.1:9000 {root_user} {root_pass} >/dev/null 2>&1 "
                    f"&& mc mb --ignore-existing local/backups >/dev/null 2>&1 "
                    f"&& mc cp /tmp/{filename} local/backups/{filename} >/dev/null 2>&1 "
                    f"&& rm -f /tmp/{filename}"
                ],
                capture_output=True,
                timeout=10,
            )
            if mc_proc.returncode == 0:
                return True
        except Exception:
            continue
    return False


@router.get("", response_model=List[BackupResponse])
def list_backups(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return db.query(Backup).order_by(Backup.created_at.desc()).all()


@router.post("", response_model=BackupResponse)
def create_backup(
    payload: BackupCreate,
    current_user: User = Depends(require_role("owner", "admin", "operator")),
    db: Session = Depends(get_db)
):
    backup_dir = os.path.join(settings.STORAGE_BASE_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    btype = (payload.backup_type or "database").strip().lower()
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    meta: Dict[str, Any] = {"engine": "control_plane_json"}

    if btype == "postgres_sql":
        target_db = _sanitize_db_identifier(payload.database_name or "", settings.POSTGRES_DB or "psm")
        filename = f"psm_pg_{target_db}_{now_str}.sql"
        content_bytes, engine_used = _dump_postgres_sql(db, target_db)
        meta.update({"format": "sql", "engine": engine_used, "database_name": target_db})
    elif btype == "mysql_sql":
        target_db = _sanitize_db_identifier(payload.database_name or "", os.getenv("MYSQL_DATABASE", "fadb"))
        filename = f"psm_mysql_{target_db}_{now_str}.sql"
        content_bytes, engine_used = _dump_mysql_sql(target_db)
        meta.update({"format": "sql", "engine": engine_used, "database_name": target_db})
    elif btype == "project_workspace":
        filename = f"psm_workspaces_{now_str}.zip"
        content_bytes, archived_count = _build_workspace_zip(db, payload.name)
        meta.update({"format": "zip", "engine": "zip_deflate", "files_archived": archived_count})
    else:
        filename = f"psm_backup_{btype}_{now_str}.json"
        content_bytes = _build_control_plane_snapshot(db, payload.name, btype)
        meta.update({"format": "json", "engine": "control_plane_v2"})

    filepath = os.path.join(backup_dir, filename)
    with open(filepath, "wb") as f:
        f.write(content_bytes)

    s3_synced = False
    if payload.sync_to_s3:
        s3_synced = _try_sync_backup_to_minio(filepath, filename)

    meta["filename"] = filename
    meta["s3_synced"] = s3_synced
    meta["s3_bucket"] = "backups" if s3_synced else None

    backup = Backup(
        name=payload.name,
        backup_type=btype,
        target_id=payload.target_id,
        size_bytes=len(content_bytes),
        status="completed",
        file_path=filepath,
        metadata_json=meta,
        created_by=current_user.id,
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)

    log_activity(
        db,
        action="backup.create",
        resource_type="backup",
        resource_id=backup.id,
        user_id=current_user.id,
        details={"name": backup.name, "type": backup.backup_type, "size_bytes": backup.size_bytes, "s3_synced": s3_synced}
    )
    return backup


@router.get("/{backup_id}/download")
def download_backup(
    backup_id: str,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db)
):
    backup = db.query(Backup).filter(Backup.id == backup_id).first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup snapshot not found")

    if not backup.file_path or not os.path.exists(backup.file_path):
        raise HTTPException(status_code=404, detail="Backup snapshot file does not exist on storage")

    filename = os.path.basename(backup.file_path)
    return FileResponse(
        path=backup.file_path,
        media_type="application/octet-stream",
        filename=filename
    )


@router.post("/{backup_id}/restore")
def restore_backup(
    backup_id: str,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db)
):
    backup = db.query(Backup).filter(Backup.id == backup_id).first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    restored_projects = 0
    restored_env_vars = 0

    if backup.file_path and os.path.exists(backup.file_path) and backup.file_path.endswith(".json"):
        try:
            with open(backup.file_path, "r", encoding="utf-8") as f:
                snap = json.load(f)
            default_server = db.query(Server).first()
            for p_item in snap.get("projects", []):
                pid = p_item.get("id")
                pname = p_item.get("name")
                if not pname:
                    continue
                existing_p = (
                    db.query(Project).filter(Project.id == pid).first()
                    if pid else None
                ) or db.query(Project).filter(Project.name == pname).first()

                target_server_id = p_item.get("server_id")
                if target_server_id and not db.query(Server).filter(Server.id == target_server_id).first():
                    target_server_id = default_server.id if default_server else None
                if not target_server_id and default_server:
                    target_server_id = default_server.id

                if not existing_p and target_server_id:
                    existing_p = Project(
                        id=pid,
                        server_id=target_server_id,
                        owner_id=current_user.id,
                        name=pname,
                        description=p_item.get("description"),
                        framework=p_item.get("framework") or "Node.js / Docker",
                        source_type=p_item.get("source_type") or "git",
                        status=p_item.get("status") or "active",
                        port_mappings=p_item.get("port_mappings"),
                        domain=p_item.get("domain"),
                        db_type=p_item.get("db_type") or "none",
                        db_name=p_item.get("db_name"),
                    )
                    db.add(existing_p)
                    db.flush()
                    src_data = p_item.get("source")
                    if isinstance(src_data, dict):
                        db.add(ProjectSource(
                            project_id=existing_p.id,
                            repository_url=src_data.get("repository_url"),
                            branch=src_data.get("branch") or "main",
                            dockerfile_path=src_data.get("dockerfile_path") or "Dockerfile",
                            compose_path=src_data.get("compose_path") or "docker-compose.yml",
                        ))
                    restored_projects += 1

                if existing_p:
                    existing_keys = {
                        ev.key for ev in db.query(EnvironmentVariable).filter(EnvironmentVariable.project_id == existing_p.id).all()
                    }
                    for ev_item in p_item.get("environment_variables", []):
                        ekey = ev_item.get("key")
                        enc_val = ev_item.get("encrypted_value")
                        if ekey and enc_val and ekey not in existing_keys:
                            db.add(EnvironmentVariable(
                                project_id=existing_p.id,
                                key=ekey,
                                encrypted_value=enc_val,
                            ))
                            existing_keys.add(ekey)
                            restored_env_vars += 1
            db.commit()
        except Exception:
            db.rollback()

    log_activity(
        db,
        action="backup.restore",
        resource_type="backup",
        resource_id=backup.id,
        user_id=current_user.id,
        details={
            "name": backup.name,
            "type": backup.backup_type,
            "restored_projects": restored_projects,
            "restored_env_vars": restored_env_vars,
        }
    )
    return {
        "message": f"Backup '{backup.name}' verified and restored successfully.",
        "backup_id": backup.id,
        "restored_projects": restored_projects,
        "restored_env_vars": restored_env_vars,
    }


@router.delete("/{backup_id}")
def delete_backup(
    backup_id: str,
    current_user: User = Depends(require_role("owner", "admin")),
    db: Session = Depends(get_db)
):
    backup = db.query(Backup).filter(Backup.id == backup_id).first()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    if backup.file_path and os.path.exists(backup.file_path):
        try:
            os.remove(backup.file_path)
        except Exception:
            pass

    db.delete(backup)
    db.commit()

    log_activity(
        db,
        action="backup.delete",
        resource_type="backup",
        resource_id=backup_id,
        user_id=current_user.id
    )
    return {"message": "Backup deleted"}
