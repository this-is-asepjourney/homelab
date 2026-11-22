import os
import re
import socket
from typing import Dict, List, Optional, Tuple
from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.api.app.models.entities import Project

# Port sistem inti homelab (harus sinkron dengan homelab/docker-compose.yml)
CORE_RESERVED_PORTS: Dict[int, str] = {
    53: "CoreDNS (:53)",
    80: "Nginx Ingress Router (:80)",
    1883: "Mosquitto MQTT (:1883)",
    3000: "Grafana (:3000)",
    3001: "Uptime Kuma (:3001)",
    3005: "PSM Console Web (:3005)",
    3306: "MySQL 8.4 (:3306)",
    5432: "PostgreSQL 16 (:5432)",
    6379: "Redis 7 (:6379)",
    8000: "PAP Core API Gateway (:8000)",
    8001: "PSM Control Plane API (:8001)",
    8080: "Nextcloud (:8080)",
    8081: "cAdvisor (:8081)",
    9000: "MinIO S3 API (:9000)",
    9002: "MinIO Web Console (:9002)",
    9090: "Prometheus (:9090)",
    9100: "Node Exporter (:9100)",
    9273: "Telegraf Exporter (:9273)",
}


def default_container_port_for_framework(framework: Optional[str]) -> int:
    """Return standard internal container port based on framework preset."""
    fw = (framework or "").strip().lower()
    if any(k in fw for k in ("fastapi", "django", "python")):
        return 8000
    if any(k in fw for k in ("laravel", "php", "golang")) or fw == "go":
        return 8080
    if "flask" in fw:
        return 5000
    if any(k in fw for k in ("static", "vite", "nginx", "html")):
        return 80
    return 3000


def _collect_docker_host_ports(exclude_container_name: Optional[str] = None) -> Dict[int, str]:
    """Best-effort inspection of host ports bound by running Docker containers."""
    docker_ports: Dict[int, str] = {}
    sanitized_exclude = (
        re.sub(r"[^a-zA-Z0-9_.-]", "-", exclude_container_name.strip().lower())
        if exclude_container_name
        else None
    )
    try:
        import docker

        client = docker.from_env(timeout=1.5)
        for cont in client.containers.list():
            cname = (cont.name or "").strip()
            if sanitized_exclude and cname.lower() == sanitized_exclude:
                continue
            ports_map = (
                cont.attrs.get("NetworkSettings", {}).get("Ports")
                or getattr(cont, "ports", None)
                or {}
            )
            for bindings in ports_map.values():
                if not bindings:
                    continue
                for b in bindings:
                    raw_hp = str(b.get("HostPort") or "").strip()
                    if raw_hp.isdigit():
                        hp = int(raw_hp)
                        if hp not in docker_ports:
                            docker_ports[hp] = f"Container Docker: {cname} (Port {hp})"
    except Exception:
        pass
    return docker_ports


def get_reserved_ports_map(
    db: Session,
    exclude_project_id: Optional[str] = None,
    exclude_container_name: Optional[str] = None,
    include_docker: bool = True,
) -> Dict[int, str]:
    """
    Build map of reserved/in-use host ports across projects, Docker containers, and core services.
    CORE_RESERVED_PORTS always takes final precedence so core ports like 3000 (Grafana)
    cannot be masked by an old project record.
    """
    reserved: Dict[int, str] = {}

    projects = db.query(Project).all()
    for p in projects:
        if exclude_project_id and p.id == exclude_project_id:
            continue
        if p.port_mappings:
            for entry in p.port_mappings.split(","):
                entry = entry.strip()
                if ":" in entry:
                    hp_str = entry.split(":", 1)[0].strip()
                else:
                    hp_str = entry
                if hp_str.isdigit():
                    hp = int(hp_str)
                    reserved[hp] = f"Project: {p.name} (Port {hp})"

    if include_docker:
        for hp, label in _collect_docker_host_ports(exclude_container_name).items():
            if hp not in reserved:
                reserved[hp] = label

    # Always enforce core homelab system ports on top (e.g. 3000 = Grafana)
    reserved.update(CORE_RESERVED_PORTS)
    return reserved


def find_next_available_port(
    db: Session,
    start_port: int = 3100,
    exclude_project_id: Optional[str] = None,
    exclude_container_name: Optional[str] = None,
    include_docker: bool = True,
) -> Tuple[int, Dict[int, str]]:
    """Find the lowest free host port starting at `start_port` (>= 3100) avoiding reserved/bound ports."""
    reserved = get_reserved_ports_map(
        db=db,
        exclude_project_id=exclude_project_id,
        exclude_container_name=exclude_container_name,
        include_docker=include_docker,
    )

    candidate = max(start_port, 3100)
    while candidate < 65535:
        if candidate not in reserved:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.05)
            try:
                if sock.connect_ex(("127.0.0.1", candidate)) != 0:
                    break
            except Exception:
                break
            finally:
                sock.close()
        candidate += 1

    return candidate, reserved


def validate_or_allocate_port_mappings(
    db: Session,
    port_mappings: Optional[str],
    framework: Optional[str] = None,
    exclude_project_id: Optional[str] = None,
    exclude_container_name: Optional[str] = None,
    auto_fix_reserved: bool = False,
    include_docker: bool = True,
) -> Tuple[str, Optional[str]]:
    """
    Validate or allocate non-conflicting HOST:CONTAINER port mappings.
    - When `port_mappings` is empty/None: auto-allocates `<next_free>:<framework_container_port>`.
    - When a host port collides with `reserved`:
      * `auto_fix_reserved=False`: raises HTTPException(400) with safe port suggestion.
      * `auto_fix_reserved=True`: remaps host port automatically and returns notice text.
    """
    next_free, reserved = find_next_available_port(
        db=db,
        exclude_project_id=exclude_project_id,
        exclude_container_name=exclude_container_name,
        include_docker=include_docker,
    )
    default_cp = default_container_port_for_framework(framework)

    if not port_mappings or not port_mappings.strip():
        return f"{next_free}:{default_cp}", None

    raw_entries = [item.strip() for item in port_mappings.split(",") if item.strip()]
    if not raw_entries:
        return f"{next_free}:{default_cp}", None

    normalized_entries: List[str] = []
    notices: List[str] = []
    used_in_batch: set = set()

    for entry in raw_entries:
        parts = [part.strip() for part in entry.split(":")]
        if len(parts) == 1:
            hp_str, cp_str = parts[0], str(default_cp)
        elif len(parts) == 2:
            hp_str, cp_str = parts[0], parts[1]
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Format port mapping tidak valid: '{entry}'. Gunakan format HOST_PORT:CONTAINER_PORT (contoh {next_free}:{default_cp}).",
            )

        if not hp_str.isdigit() or not cp_str.isdigit():
            raise HTTPException(
                status_code=400,
                detail=f"Port harus berupa angka valid pada mapping '{entry}'.",
            )

        hp = int(hp_str)
        cp = int(cp_str)
        if not (1 <= hp <= 65535) or not (1 <= cp <= 65535):
            raise HTTPException(
                status_code=400,
                detail=f"Nomor port harus antara 1 hingga 65535 (diterima '{entry}').",
            )

        conflict_reason = reserved.get(hp) or (f"mapping duplikat ({hp})" if hp in used_in_batch else None)
        if conflict_reason:
            safe_hp = max(next_free, 3100)
            while safe_hp < 65535 and (safe_hp in reserved or safe_hp in used_in_batch):
                safe_hp += 1
            if auto_fix_reserved:
                notices.append(
                    f"Port host {hp} bentrok dengan {conflict_reason} — otomatis dialihkan ke {safe_hp}:{cp}."
                )
                hp = safe_hp
                next_free = safe_hp + 1
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Port host {hp} sudah digunakan oleh {conflict_reason}. Silakan gunakan port aman seperti {safe_hp}:{cp}.",
                )

        used_in_batch.add(hp)
        normalized_entries.append(f"{hp}:{cp}")

    notice_text = " ".join(notices) if notices else None
    return ", ".join(normalized_entries), notice_text
