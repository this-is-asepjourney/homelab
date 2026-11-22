import json
import os
import re
import socket
import subprocess
from typing import Any, Dict, List, Optional, Tuple
from fastapi import HTTPException
from sqlalchemy.orm import Session

from apps.api.app.models.entities import Container, Project

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

_CLI_HOST_PORT_RE = re.compile(r"(?:[\d.]+|\[[^\]]*\]):(\d+)->")


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


def _normalize_container_slug(name: Optional[str]) -> Optional[str]:
    """Normalize a project/container name into Docker container slug format for comparisons."""
    if not name:
        return None
    cleaned = name.strip().lstrip("/").lower()
    if not cleaned:
        return None
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", cleaned)


def _extract_host_ports_from_cli_string(ports_str: str) -> List[int]:
    """Parse Docker CLI `{{.Ports}}` output (e.g. '0.0.0.0:3100->80/tcp, [::]:3100->80/tcp')."""
    if not ports_str:
        return []
    found: List[int] = []
    seen = set()
    for match in _CLI_HOST_PORT_RE.findall(ports_str):
        if match.isdigit():
            hp = int(match)
            if 1 <= hp <= 65535 and hp not in seen:
                seen.add(hp)
                found.append(hp)
    return found


def _extract_host_ports_from_port_entries(ports: Any) -> List[int]:
    """Extract unique host port integers from Docker UDS API dicts, SDK maps, or port strings."""
    if not ports:
        return []

    found: List[int] = []
    seen = set()

    def _add(val: Any) -> None:
        raw = str(val).strip() if val is not None else ""
        if raw.isdigit():
            hp = int(raw)
            if 1 <= hp <= 65535 and hp not in seen:
                seen.add(hp)
                found.append(hp)

    if isinstance(ports, dict):
        for bindings in ports.values():
            if not bindings:
                continue
            if isinstance(bindings, list):
                for b in bindings:
                    if isinstance(b, dict):
                        _add(b.get("HostPort") or b.get("PublicPort") or b.get("host_port"))
        return found

    if isinstance(ports, str):
        cli_ports = _extract_host_ports_from_cli_string(ports) if "->" in ports else []
        for hp in cli_ports:
            _add(hp)
        if not cli_ports:
            for item in ports.split(","):
                part = item.strip()
                if not part:
                    continue
                hp_str = part.split(":", 1)[0].strip() if ":" in part else part
                _add(hp_str)
        return found

    if isinstance(ports, (list, tuple)):
        for item in ports:
            if isinstance(item, dict):
                _add(item.get("PublicPort") or item.get("HostPort") or item.get("host_port"))
            elif isinstance(item, str):
                for hp in _extract_host_ports_from_port_entries(item):
                    _add(hp)
            elif isinstance(item, int):
                _add(item)
        return found

    return found


def _get_probe_hosts() -> List[str]:
    """Return host addresses to TCP-probe without leaking LAN probes into local Windows tests."""
    hosts: List[str] = ["127.0.0.1"]
    in_container = os.path.exists("/.dockerenv") or os.path.exists("/var/run/docker.sock")
    if in_container:
        try:
            socket.gethostbyname("host.docker.internal")
            hosts.append("host.docker.internal")
        except Exception:
            pass
        for env_key in ("HOST_IP", "SERVER_LAN_IP"):
            val = (os.getenv(env_key) or "").strip()
            if val and val not in ("0.0.0.0", "127.0.0.1") and val not in hosts:
                hosts.append(val)
    return hosts


def _is_host_port_open(port: int, probe_hosts: Optional[List[str]] = None) -> bool:
    """Return True if any host target accepts TCP connections on `port`."""
    targets = probe_hosts if probe_hosts is not None else _get_probe_hosts()
    for host in targets:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.05)
        try:
            if sock.connect_ex((host, port)) == 0:
                return True
        except Exception:
            continue
        finally:
            sock.close()
    return False


def _collect_docker_host_ports(exclude_container_name: Optional[str] = None) -> Dict[int, str]:
    """Best-effort inspection of host ports bound by running Docker containers via UDS, SDK, or CLI."""
    docker_ports: Dict[int, str] = {}
    sanitized_exclude = _normalize_container_slug(exclude_container_name)

    def _is_excluded(cname: str, aliases: Optional[List[str]] = None) -> bool:
        if not sanitized_exclude:
            return False
        for cand in [cname, *(aliases or [])]:
            if _normalize_container_slug(str(cand)) == sanitized_exclude:
                return True
        return False

    # Tier 1: Docker Engine UDS via DockerEngineService (works inside psm-api without docker SDK/CLI)
    try:
        from apps.api.app.services.docker_service import DockerEngineService

        for cont in DockerEngineService.list_containers() or []:
            state = str(cont.get("state") or "").strip().lower()
            if state not in ("running", "restarting", "paused", ""):
                continue
            cname = str(cont.get("name") or "").strip() or "unknown"
            if _is_excluded(cname, cont.get("names")):
                continue
            for hp in _extract_host_ports_from_port_entries(cont.get("ports")):
                docker_ports.setdefault(hp, f"Container Docker: {cname} (Port {hp})")
    except Exception:
        pass

    if docker_ports:
        return docker_ports

    # Tier 2: Python docker SDK fallback
    try:
        import docker

        client = docker.from_env(timeout=1.5)
        for cont in client.containers.list():
            cname = (cont.name or "").strip() or "unknown"
            if _is_excluded(cname):
                continue
            ports_map = (
                cont.attrs.get("NetworkSettings", {}).get("Ports")
                or getattr(cont, "ports", None)
                or {}
            )
            for hp in _extract_host_ports_from_port_entries(ports_map):
                docker_ports.setdefault(hp, f"Container Docker: {cname} (Port {hp})")
    except Exception:
        pass

    if docker_ports:
        return docker_ports

    # Tier 3: Docker CLI fallback
    try:
        res = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        if res.returncode == 0:
            for line in (res.stdout or "").splitlines():
                if not line.strip() or "\t" not in line:
                    continue
                cname, ports_str = line.split("\t", 1)
                cname = cname.strip().lstrip("/")
                if _is_excluded(cname):
                    continue
                for hp in _extract_host_ports_from_cli_string(ports_str):
                    docker_ports.setdefault(hp, f"Container Docker: {cname} (Port {hp})")
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
    Live runtime container bindings override stale Project rows, and CORE_RESERVED_PORTS
    always takes final precedence so core ports like 3000 (Grafana) are never masked.
    """
    reserved: Dict[int, str] = {}
    sanitized_exclude = _normalize_container_slug(exclude_container_name)

    # 1. Registered PSM projects (excluding self)
    projects = db.query(Project).all()
    for p in projects:
        if exclude_project_id and p.id == exclude_project_id:
            continue
        if p.port_mappings:
            for entry in p.port_mappings.split(","):
                entry = entry.strip()
                hp_str = entry.split(":", 1)[0].strip() if ":" in entry else entry
                if hp_str.isdigit():
                    hp = int(hp_str)
                    reserved[hp] = f"Project: {p.name} (Port {hp})"

    # 2. Container inventory cached in DB (running/restarting containers override stale project rows)
    try:
        containers = db.query(Container).all()
        for c in containers:
            c_state = str(c.state or "").strip().lower()
            if c_state not in ("running", "restarting", "paused"):
                continue
            cname = str(c.name or "").strip() or "unknown"
            if sanitized_exclude and _normalize_container_slug(cname) == sanitized_exclude:
                continue
            for hp in _extract_host_ports_from_port_entries(c.ports):
                reserved[hp] = f"Container Docker: {cname} (Port {hp})"
    except Exception:
        pass

    # 3. Live Docker Engine ground truth overrides DB inventory/project rows
    if include_docker:
        for hp, label in _collect_docker_host_ports(exclude_container_name).items():
            reserved[hp] = label

    # 4. Always enforce core homelab system ports on top (e.g. 3000 = Grafana)
    reserved.update(CORE_RESERVED_PORTS)
    return reserved


def find_next_available_port(
    db: Session,
    start_port: int = 3100,
    exclude_project_id: Optional[str] = None,
    exclude_container_name: Optional[str] = None,
    include_docker: bool = True,
    probe_host_sockets: Optional[bool] = None,
) -> Tuple[int, Dict[int, str]]:
    """Find the lowest free host port starting at `start_port` (>= 3100) avoiding reserved/bound ports."""
    reserved = get_reserved_ports_map(
        db=db,
        exclude_project_id=exclude_project_id,
        exclude_container_name=exclude_container_name,
        include_docker=include_docker,
    )
    should_probe = include_docker if probe_host_sockets is None else probe_host_sockets
    probe_hosts = _get_probe_hosts() if should_probe else []

    candidate = max(start_port, 3100)
    while candidate < 65535:
        if candidate not in reserved and (not should_probe or not _is_host_port_open(candidate, probe_hosts)):
            break
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
    - When a host port collides with `reserved` or a live host socket listener:
      * `auto_fix_reserved=False`: raises HTTPException(400) with safe port suggestion.
      * `auto_fix_reserved=True`: remaps host port automatically and returns notice text.
    """
    next_free, reserved = find_next_available_port(
        db=db,
        exclude_project_id=exclude_project_id,
        exclude_container_name=exclude_container_name,
        include_docker=include_docker,
    )
    probe_hosts = _get_probe_hosts() if include_docker else []
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
        if not conflict_reason and include_docker and _is_host_port_open(hp, probe_hosts):
            conflict_reason = f"Service aktif di host (Port {hp})"

        if conflict_reason:
            safe_hp = max(next_free, 3100)
            while safe_hp < 65535 and (
                safe_hp in reserved
                or safe_hp in used_in_batch
                or (include_docker and _is_host_port_open(safe_hp, probe_hosts))
            ):
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
