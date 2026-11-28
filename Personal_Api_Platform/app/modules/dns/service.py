
"""Homelab Local DNS & Dynamic Virtual Host Router Service.

Manages:
1. CoreDNS static host mappings (homelab.hosts)
2. Ingress Nginx virtual host configs (conf.d/projects/*.conf)
3. Client hosts file export for zero-config LAN devices
"""

import html
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
192.168.25.105 minio.homelab
192.168.25.105 s3.homelab

100.78.113.53 api.tailscale.homelab
100.78.113.53 psm.tailscale.homelab
100.78.113.53 psm-api.tailscale.homelab
100.78.113.53 nextcloud.tailscale.homelab
100.78.113.53 grafana.tailscale.homelab
100.78.113.53 prometheus.tailscale.homelab
100.78.113.53 kuma.tailscale.homelab
100.78.113.53 cadvisor.tailscale.homelab
100.78.113.53 minio.tailscale.homelab
100.78.113.53 s3.tailscale.homelab
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
    {"domain": "minio.homelab", "target": "minio:9001", "type": "Object Storage", "desc": "MinIO Web Console (Image & File Buckets)"},
    {"domain": "s3.homelab", "target": "minio:9000", "type": "S3 API", "desc": "MinIO S3-Compatible Object Storage API"},
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
    def get_maintenance_dir(cls) -> Path:
        return _resolve_existing_dir("router/conf.d/maintenance")

    @classmethod
    def render_maintenance_html(
        cls,
        project_name: str,
        domain: str,
        title: Optional[str] = None,
        message: Optional[str] = None,
        eta: Optional[str] = None,
    ) -> str:
        """Render a standalone XSS-safe HTTP 503 Maintenance HTML page with inline CSS/SVG."""
        safe_project = html.escape((project_name or domain or "Aplikasi").strip())
        safe_domain = html.escape((domain or "").strip())
        safe_title = html.escape((title or "").strip() or "Sedang Dalam Pemeliharaan")
        safe_message = html.escape(
            (message or "").strip()
            or "Kami sedang melakukan pembaruan dan peningkatan sistem untuk memberikan layanan terbaik. Silakan coba kembali beberapa saat lagi."
        )
        safe_eta = html.escape((eta or "").strip()) if (eta or "").strip() else ""
        eta_block = (
            f'<div class="eta-box"><span>Estimasi Selesai:</span> <strong>{safe_eta}</strong></div>'
            if safe_eta
            else ""
        )

        return f"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate" />
  <title>{safe_title} - {safe_project}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      min-height: 100vh;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: radial-gradient(circle at top, #1e293b 0%, #090d16 65%);
      color: #e2e8f0;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .card {{
      width: 100%;
      max-width: 560px;
      background: rgba(15, 23, 42, 0.92);
      border: 1px solid rgba(245, 158, 11, 0.35);
      border-radius: 20px;
      padding: 36px 32px;
      box-shadow: 0 24px 64px rgba(0, 0, 0, 0.55);
      text-align: center;
    }}
    .icon-wrap {{
      width: 68px;
      height: 68px;
      margin: 0 auto 20px;
      border-radius: 18px;
      background: rgba(245, 158, 11, 0.14);
      border: 1px solid rgba(245, 158, 11, 0.35);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fbbf24;
    }}
    .badge {{
      display: inline-block;
      padding: 5px 12px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      background: rgba(245, 158, 11, 0.15);
      color: #fcd34d;
      border: 1px solid rgba(245, 158, 11, 0.3);
      margin-bottom: 14px;
    }}
    .project-name {{
      font-size: 13px;
      color: #94a3b8;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      margin-bottom: 8px;
    }}
    h1 {{
      font-size: 24px;
      font-weight: 800;
      color: #f8fafc;
      margin-bottom: 14px;
      line-height: 1.3;
    }}
    .message {{
      font-size: 14px;
      line-height: 1.7;
      color: #cbd5e1;
      margin-bottom: 22px;
      white-space: pre-line;
    }}
    .eta-box {{
      display: inline-block;
      padding: 10px 16px;
      border-radius: 12px;
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.3);
      color: #93c5fd;
      font-size: 13px;
      margin-bottom: 22px;
    }}
    .actions {{
      display: flex;
      justify-content: center;
      gap: 12px;
    }}
    .btn {{
      appearance: none;
      border: 1px solid rgba(245, 158, 11, 0.4);
      background: #f59e0b;
      color: #090d16;
      font-weight: 700;
      font-size: 13px;
      padding: 10px 20px;
      border-radius: 12px;
      cursor: pointer;
    }}
    .btn:hover {{ background: #fbbf24; }}
    .footer {{
      margin-top: 24px;
      padding-top: 16px;
      border-top: 1px solid rgba(148, 163, 184, 0.15);
      font-size: 11px;
      color: #64748b;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
  </style>
</head>
<body>
  <main class="card">
    <div class="icon-wrap">
      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
      </svg>
    </div>
    <div class="badge">UNDER MAINTENANCE</div>
    <div class="project-name">{safe_project}{f" &bull; {safe_domain}" if safe_domain and safe_domain != safe_project else ""}</div>
    <h1>{safe_title}</h1>
    <p class="message">{safe_message}</p>
    {eta_block}
    <div class="actions">
      <button type="button" class="btn" onclick="window.location.reload()">Muat Ulang Halaman</button>
    </div>
    <div class="footer">HTTP 503 Service Unavailable &bull; Homelab Ingress Maintenance</div>
  </main>
</body>
</html>
"""

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
    def _to_tailscale_domain(cls, domain: str) -> str:
        if domain == "homelab":
            return "api.tailscale.homelab"
        if domain.endswith(".homelab"):
            return domain.replace(".homelab", ".tailscale.homelab")
        return domain

    @classmethod
    def list_domains(cls) -> List[Dict[str, Any]]:
        """List all active domains (built-in + deployed projects)."""
        results = []
        host_ip = _get_lan_ip()
        tailscale_ip = _get_tailscale_ip()

        # 1. Built-in system domains
        for svc in BUILTIN_SERVICES:
            ts_domain = cls._to_tailscale_domain(svc["domain"])
            results.append({
                "domain": svc["domain"],
                "url": f"http://{svc['domain']}",
                "tailscale_domain": ts_domain,
                "tailscale_url": f"http://{ts_domain}",
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
                    upstream_match = re.search(r"#\s*upstream:\s*([^\s]+)", text)
                    if srv_match and (proxy_match or upstream_match):
                        domain_names = srv_match.group(1).strip().split()
                        primary_domain = domain_names[0] if domain_names else conf_file.stem
                        raw_target = proxy_match.group(1) if proxy_match else upstream_match.group(1)
                        proxy_target = raw_target.replace("http://", "").strip()
                        is_maint = "MAINTENANCE MODE" in text
                        ts_domain = cls._to_tailscale_domain(primary_domain)
                        results.append({
                            "domain": primary_domain,
                            "url": f"http://{primary_domain}",
                            "tailscale_domain": ts_domain,
                            "tailscale_url": f"http://{ts_domain}",
                            "target": proxy_target,
                            "category": "Project App",
                            "description": f"Deployed Project ({conf_file.stem})",
                            "is_system": False,
                            "maintenance_mode": is_maint,
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
        maintenance_mode: bool = False,
        maintenance_title: Optional[str] = None,
        maintenance_message: Optional[str] = None,
        maintenance_eta: Optional[str] = None,
        retry_after: int = 60,
    ) -> Dict[str, Any]:
        """Creates an Nginx virtual host for domain (or 503 maintenance page) and adds DNS host mapping."""
        clean_domain = cls.sanitize_domain(domain)
        slug = project_name or clean_domain.replace(".homelab", "").replace(".local", "")
        vhost_dir = cls.get_projects_vhost_dir()
        vhost_file = vhost_dir / f"{clean_domain}.conf"
        maint_dir = cls.get_maintenance_dir()
        maint_file = maint_dir / f"{clean_domain}.html"
        lan_ip = _get_lan_ip()
        safe_retry = max(5, int(retry_after or 60))

        # 1. Write static Nginx virtual host config (either Maintenance 503 or upstream proxy)
        if maintenance_mode:
            html_body = cls.render_maintenance_html(
                project_name=slug,
                domain=clean_domain,
                title=maintenance_title,
                message=maintenance_message,
                eta=maintenance_eta,
            )
            maint_file.write_text(html_body, encoding="utf-8")
            conf_content = f"""# Virtual Host for Project: {slug} (MAINTENANCE MODE)
# upstream: http://{target_host}:{target_port}
server {{
    listen 80;
    server_name {clean_domain};

    error_page 503 /__maintenance__.html;

    location / {{
        return 503;
    }}

    location = /__maintenance__.html {{
        internal;
        alias /etc/nginx/conf.d/maintenance/{clean_domain}.html;
        default_type text/html;
        add_header Retry-After {safe_retry} always;
        add_header Cache-Control "no-store, no-cache, must-revalidate" always;
    }}
}}
"""
        else:
            if maint_file.exists():
                try:
                    maint_file.unlink()
                except Exception:
                    pass
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
            "maintenance_mode": bool(maintenance_mode),
            "config_file": str(vhost_file),
        }

    @classmethod
    def remove_domain_route(cls, domain: str) -> bool:
        """Removes a virtual host config, maintenance HTML page, and DNS mapping."""
        clean_domain = cls.sanitize_domain(domain)
        vhost_dir = cls.get_projects_vhost_dir()
        vhost_file = vhost_dir / f"{clean_domain}.conf"
        maint_file = cls.get_maintenance_dir() / f"{clean_domain}.html"
        removed = False

        for fpath in (vhost_file, maint_file):
            if fpath.exists():
                try:
                    fpath.unlink()
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
