"""Homelab Local DNS & Dynamic Virtual Host Router Service.

Manages:
1. CoreDNS static host mappings (homelab.hosts)
2. Ingress Nginx virtual host configs (conf.d/projects/*.conf)
3. Client hosts file export for zero-config LAN devices
"""

import logging
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("homelab.dns")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


def _get_base_candidates() -> List[Path]:
    candidates: List[Path] = []
    env_dir = os.getenv("DNS_BASE_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend([
        Path("/app"),
        Path("/opt/projects/homelab-server"),
        _REPO_ROOT,
        Path("."),
    ])
    return candidates


def _get_lan_ip() -> str:
    lan_ip = os.getenv("SERVER_LAN_IP", "").strip()
    if lan_ip and lan_ip not in ("0.0.0.0", "127.0.0.1"):
        return lan_ip
    host_ip = os.getenv("HOST_IP", "").strip()
    if host_ip and host_ip not in ("0.0.0.0", "127.0.0.1"):
        return host_ip
    return "192.168.25.105"


def _get_tailscale_ip() -> str:
    ts_ip = os.getenv("SERVER_TS_IP", "").strip() or os.getenv("TAILSCALE_HOST", "").strip()
    if ts_ip:
        return ts_ip
    return "100.78.113.53"


def _resolve_existing_dir(subpath: str) -> Path:
    candidates = _get_base_candidates()
    env_dir = os.getenv("DNS_BASE_DIR", "").strip()
    if env_dir:
        target = (Path(env_dir) / subpath).resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target

    for base in candidates:
        cand = (base / subpath).resolve()
        if cand.exists():
            return cand

    fallback = (candidates[0] / subpath).resolve()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _resolve_existing_file(subpath: str, default_content: str = "") -> Path:
    candidates = _get_base_candidates()
    env_dir = os.getenv("DNS_BASE_DIR", "").strip()
    if env_dir:
        target = (Path(env_dir) / subpath).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(default_content, encoding="utf-8")
        return target

    for base in candidates:
        cand = (base / subpath).resolve()
        if cand.exists():
            return cand

    fallback = (candidates[0] / subpath).resolve()
    fallback.parent.mkdir(parents=True, exist_ok=True)
    if not fallback.exists():
        fallback.write_text(default_content, encoding="utf-8")
    return fallback


DEFAULT_HOSTS_TEMPLATE = """# Homelab Local DNS Static Hosts Mapping
# Host Server: fa (Ubuntu Server)

192.168.25.105 homelab
192.168.25.105 api.homelab
192.168.25.105 psm.homelab
192.168.25.105 psm-api.homelab
192.168.25.105 nextcloud.homelab
192.168.25.105 grafana.homelab
192.168.25.105 prometheus.homelab
192.168.25.105 kuma.homelab
192.168.25.105 uptime.homelab
192.168.25.105 cadvisor.homelab

100.78.113.53 api.tailscale.homelab
100.78.113.53 psm.tailscale.homelab
100.78.113.53 psm-api.tailscale.homelab
100.78.113.53 nextcloud.tailscale.homelab
100.78.113.53 grafana.tailscale.homelab
100.78.113.53 prometheus.tailscale.homelab
100.78.113.53 kuma.tailscale.homelab
100.78.113.53 cadvisor.tailscale.homelab
"""

BUILTIN_SERVICES = [
    {"domain": "homelab", "target": "pap-api:8000", "type": "Dashboard", "desc": "Homelab Mission Control UI"},
    {"domain": "api.homelab", "target": "pap-api:8000", "type": "Core Gateway", "desc": "PAP Central API Gateway"},
    {"domain": "psm.homelab", "target": "psm-web:3005", "type": "Console", "desc": "Personal Server Manager"},
    {"domain": "psm-api.homelab", "target": "psm-api:8000", "type": "Control Plane API", "desc": "PSM Operations API"},
    {"domain": "nextcloud.homelab", "target": "nextcloud:80", "type": "Cloud Storage", "desc": "Nextcloud Personal Drive"},
    {"domain": "grafana.homelab", "target": "grafana:3000", "type": "Monitoring", "desc": "Grafana Visualizations"},
    {"domain": "prometheus.homelab", "target": "prometheus:9090", "type": "Metrics", "desc": "Prometheus TSDB"},
    {"domain": "kuma.homelab", "target": "uptime-kuma:3001", "type": "Health Check", "desc": "Uptime Kuma Hub"},
    {"domain": "cadvisor.homelab", "target": "cadvisor:8080", "type": "Container Metrics", "desc": "cAdvisor Container Inspector"},
]


class DnsService:
    """Manages local DNS and virtual host routing."""

    @classmethod
    def get_hosts_file_path(cls) -> Path:
        return _resolve_existing_file("dns/homelab.hosts", DEFAULT_HOSTS_TEMPLATE)

    @classmethod
    def get_projects_vhost_dir(cls) -> Path:
        return _resolve_existing_dir("router/conf.d/projects")

    @classmethod
    def sanitize_domain(cls, raw: str) -> str:
        """Normalize a domain or bare project name into a safe hostname.

        Preserves domains that already contain a dot (e.g. pos-main.homelab,
        pos.main.homelab, pos.toko.my.id) and appends `.homelab` only to bare names.
        """
        domain = (raw or "").strip().lower()
        domain = re.sub(r"^https?://", "", domain)
        domain = domain.split("/")[0].split(":")[0]
        domain = re.sub(r"[^a-z0-9.-]", "-", domain)
        domain = re.sub(r"\.{2,}", ".", domain).strip(".-")
        if not domain:
            domain = "app.homelab"
        elif "." not in domain:
            domain = f"{domain}.homelab"
        return domain

    @classmethod
    def list_domains(cls) -> List[Dict[str, Any]]:
        """List all active domains (built-in + deployed projects)."""
        results = []
        host_ip = _get_lan_ip()
        tailscale_ip = _get_tailscale_ip()

        # 1. Built-in system domains
        for svc in BUILTIN_SERVICES:
            results.append({
                "domain": svc["domain"],
                "url": f"http://{svc['domain']}",
                "target": svc["target"],
                "category": svc["type"],
                "description": svc["desc"],
                "is_system": True,
                "ip_lan": host_ip,
                "ip_tailscale": tailscale_ip,
            })

        # 2. Deployed project domains from vhost directory
        vhost_dir = cls.get_projects_vhost_dir()
        if vhost_dir.exists():
            for conf_file in sorted(vhost_dir.glob("*.conf")):
                try:
                    text = conf_file.read_text(encoding="utf-8")
                    srv_match = re.search(r"server_name\s+([^;]+);", text)
                    proxy_match = re.search(r"proxy_pass\s+([^;]+);", text)
                    if srv_match and proxy_match:
                        domain_names = srv_match.group(1).strip().split()
                        primary_domain = domain_names[0] if domain_names else conf_file.stem
                        proxy_target = proxy_match.group(1).replace("http://", "").strip()
                        results.append({
                            "domain": primary_domain,
                            "url": f"http://{primary_domain}",
                            "target": proxy_target,
                            "category": "Project App",
                            "description": f"Deployed Project ({conf_file.stem})",
                            "is_system": False,
                            "ip_lan": host_ip,
                            "ip_tailscale": tailscale_ip,
                        })
                except Exception as e:
                    logger.warning("Error reading vhost %s: %s", conf_file, e)

        return results

    @classmethod
    def register_domain_route(
        cls,
        domain: str,
        target_port: int,
        target_host: str = "host.docker.internal",
        project_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates an Nginx virtual host for domain and adds DNS host mapping."""
        clean_domain = cls.sanitize_domain(domain)
        slug = project_name or clean_domain.replace(".homelab", "").replace(".local", "")
        vhost_dir = cls.get_projects_vhost_dir()
        vhost_file = vhost_dir / f"{clean_domain}.conf"
        lan_ip = _get_lan_ip()

        # 1. Write static Nginx virtual host config (keep literal target_host so /etc/hosts extra_hosts applies)
        conf_content = f"""# Virtual Host for Project: {slug}
server {{
    listen 80;
    server_name {clean_domain};

    client_max_body_size 100M;

    location / {{
        proxy_pass http://{target_host}:{target_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }}
}}
"""
        vhost_file.write_text(conf_content, encoding="utf-8")

        # 2. Idempotently upsert domain in homelab.hosts
        hosts_file = cls.get_hosts_file_path()
        entry_line = f"{lan_ip} {clean_domain}"
        existing_lines = hosts_file.read_text(encoding="utf-8").splitlines() if hosts_file.exists() else []
        updated_lines: List[str] = []
        replaced = False
        for line in existing_lines:
            stripped = line.strip()
            parts = stripped.split() if stripped and not stripped.startswith("#") else []
            if len(parts) >= 2 and clean_domain in parts[1:]:
                if not replaced:
                    updated_lines.append(entry_line)
                    replaced = True
                # Skip duplicate entries for the same domain
                continue
            updated_lines.append(line)

        if not replaced:
            updated_lines.append(entry_line)

        hosts_file.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")

        # 3. Reload Nginx Router if possible
        cls.reload_router()

        return {
            "success": True,
            "domain": clean_domain,
            "url": f"http://{clean_domain}",
            "target": f"{target_host}:{target_port}",
            "lan_ip": lan_ip,
            "config_file": str(vhost_file),
        }

    @classmethod
    def remove_domain_route(cls, domain: str) -> bool:
        """Removes a virtual host config and DNS mapping."""
        clean_domain = cls.sanitize_domain(domain)
        vhost_dir = cls.get_projects_vhost_dir()
        vhost_file = vhost_dir / f"{clean_domain}.conf"
        removed = False

        if vhost_file.exists():
            try:
                vhost_file.unlink()
                removed = True
            except Exception:
                pass

        # Clean exact host entry from homelab.hosts
        hosts_file = cls.get_hosts_file_path()
        if hosts_file.exists():
            try:
                lines = hosts_file.read_text(encoding="utf-8").splitlines()
                filtered: List[str] = []
                for ln in lines:
                    stripped = ln.strip()
                    parts = stripped.split() if stripped and not stripped.startswith("#") else []
                    if len(parts) >= 2 and clean_domain in parts[1:]:
                        removed = True
                        continue
                    filtered.append(ln)
                hosts_file.write_text("\n".join(filtered) + "\n", encoding="utf-8")
            except Exception:
                pass

        cls.reload_router()
        return removed

    @classmethod
    def reload_router(cls) -> None:
        """Attempts to signal the router container to reload its configuration.
        Tries unix socket at /var/run/docker.sock directly first (works inside containers
        without docker CLI), then falls back to subprocess docker CLI.
        """
        # 1. Direct Docker daemon Unix Socket (Fast & zero-dependency)
        socket_path = "/var/run/docker.sock"
        if os.path.exists(socket_path):
            for cname in ["homelab_router", "homelab-router"]:
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(2.0)
                    s.connect(socket_path)
                    req = f"POST /containers/{cname}/kill?signal=HUP HTTP/1.1\r\nHost: localhost\r\n\r\n"
                    s.sendall(req.encode("utf-8"))
                    res = s.recv(1024)
                    s.close()
                    if b"204" in res or b"200" in res:
                        logger.info("Router %s reloaded successfully via docker.sock", cname)
                        return
                except Exception as ex:
                    logger.debug("Socket reload attempt for %s failed: %s", cname, ex)

        # 2. Subprocess fallback
        for cname in ["homelab_router", "homelab-router"]:
            try:
                res = subprocess.run(
                    ["docker", "exec", cname, "nginx", "-s", "reload"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
                if res.returncode == 0:
                    logger.info("Router %s reloaded successfully via CLI", cname)
                    break
            except Exception:
                pass

    @classmethod
    def generate_hosts_file_content(cls) -> str:
        """Generates ready-to-copy /etc/hosts or Windows hosts file format."""
        domains = cls.list_domains()
        host_ip = _get_lan_ip()
        tailscale_ip = _get_tailscale_ip()

        lines = [
            "# ==================================================================",
            "# HOMELAB SERVER LOCAL DOMAIN MAPPINGS (Ubuntu Homelab Server)",
            f"# Generated automatically by PAP Core • {len(domains)} Domains Active",
            "# ==================================================================",
            f"# A. LAN Connection (Rumah / Kantor - {host_ip})",
        ]
        for d in domains:
            lines.append(f"{host_ip:<16} {d['domain']}")

        lines.extend([
            "",
            f"# B. Tailscale Connection (Remote / Di Luar Rumah - {tailscale_ip})",
        ])
        for d in domains:
            dom = d["domain"]
            ts_dom = dom.replace(".homelab", ".tailscale.homelab") if dom.endswith(".homelab") else dom
            lines.append(f"{tailscale_ip:<16} {ts_dom}")

        return "\n".join(lines) + "\n"

    @classmethod
    def check_dns_status(cls) -> Dict[str, Any]:
        """Verifies if Local DNS (port 53) and Ingress Router (port 80) are active."""
        host_ip = _get_lan_ip()
        tailscale_ip = _get_tailscale_ip()

        # 1. Check TCP Port 53 (DNS)
        dns_tcp = False
        for target in ("homelab_dns", "homelab-dns", host_ip, "127.0.0.1"):
            try:
                with socket.create_connection((target, 53), timeout=0.6):
                    dns_tcp = True
                    break
            except Exception:
                continue

        # 2. Check Port 80 (Ingress Router)
        router_http = False
        for target in ("homelab_router", "homelab-router", "host.docker.internal", host_ip, "127.0.0.1"):
            try:
                with socket.create_connection((target, 80), timeout=0.6):
                    router_http = True
                    break
            except Exception:
                continue

        return {
            "dns_server_port_53": "ONLINE" if dns_tcp else "INITIALIZING",
            "ingress_router_port_80": "ONLINE" if router_http else "INITIALIZING",
            "host_lan_ip": host_ip,
            "host_tailscale_ip": tailscale_ip,
            "domain_zone": ".homelab (and custom domains)",
            "total_domains_active": len(cls.list_domains()),
        }
