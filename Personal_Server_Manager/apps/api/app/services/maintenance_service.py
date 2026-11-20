"""Project maintenance HTML renderer and direct container maintenance integration."""

import base64
import html
import json
from typing import Any, Dict, Optional

from apps.api.app.models.entities import Project
from apps.api.app.services.docker_service import DockerEngineService

DEFAULT_MAINTENANCE_TITLE = "Sedang Dalam Pemeliharaan"
DEFAULT_MAINTENANCE_MESSAGE = (
    "Kami sedang melakukan pembaruan dan peningkatan sistem untuk memberikan layanan terbaik. "
    "Silakan coba kembali beberapa saat lagi."
)


def render_maintenance_html(
    project_name: str,
    domain: Optional[str],
    title: Optional[str] = None,
    message: Optional[str] = None,
    eta: Optional[str] = None,
) -> str:
    """Render a standalone XSS-safe HTTP 503 Maintenance HTML page with inline CSS/SVG."""
    safe_project = html.escape((project_name or domain or "Aplikasi").strip())
    safe_domain = html.escape((domain or "").strip())
    safe_title = html.escape((title or "").strip() or DEFAULT_MAINTENANCE_TITLE)
    safe_message = html.escape((message or "").strip() or DEFAULT_MAINTENANCE_MESSAGE)
    safe_eta = html.escape((eta or "").strip()) if (eta or "").strip() else ""
    eta_block = (
        f'<div class="eta-box"><span>Estimasi Selesai:</span> <strong>{safe_eta}</strong></div>'
        if safe_eta
        else ""
    )
    domain_suffix = f" &bull; {safe_domain}" if safe_domain and safe_domain != safe_project else ""

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
    <div class="project-name">{safe_project}{domain_suffix}</div>
    <h1>{safe_title}</h1>
    <p class="message">{safe_message}</p>
    {eta_block}
    <div class="actions">
      <button type="button" class="btn" onclick="window.location.reload()">Muat Ulang Halaman</button>
    </div>
    <div class="footer">HTTP 503 Service Unavailable &bull; Homelab Maintenance Mode</div>
  </main>
</body>
</html>
"""


def apply_container_maintenance(
    project: Project,
    enabled: bool,
    html_content: str,
    retry_after: int = 60,
) -> Dict[str, Any]:
    """Toggle direct-port container maintenance for running Laravel apps via storage/framework/down."""
    try:
        matched = DockerEngineService.find_container_by_project(project.name, project.id)
    except Exception:
        matched = None

    if not matched:
        return {"applied": False, "driver": "none", "reason": "Container belum berjalan."}

    cid = matched.get("full_id") or matched.get("docker_id") or matched.get("name")
    container_name = matched.get("name") or project.name
    inspected = DockerEngineService.inspect_container(cid) if cid else {}
    is_running = bool(inspected.get("running", "Up" in str(matched.get("status", ""))))
    if not cid or not is_running:
        return {
            "applied": False,
            "driver": "none",
            "container": container_name,
            "reason": "Container belum berjalan.",
        }

    art_code, _ = DockerEngineService.exec_in_container(
        cid, ["sh", "-c", "test -f artisan"], timeout=5
    )
    if art_code != 0:
        return {
            "applied": False,
            "driver": "nginx_ingress_only",
            "container": container_name,
            "reason": "Halaman maintenance aktif di Nginx Domain Ingress (:80).",
        }

    if enabled:
        payload = json.dumps(
            {
                "except": [],
                "redirect": None,
                "retry": max(5, int(retry_after or 60)),
                "refresh": None,
                "secret": None,
                "status": 503,
                "template": html_content,
            }
        )
        b64_payload = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        cmd = f"mkdir -p storage/framework && printf '%s' '{b64_payload}' | base64 -d > storage/framework/down"
        code, out = DockerEngineService.exec_in_container(cid, ["sh", "-c", cmd], timeout=8)
        return {
            "applied": code == 0,
            "driver": "laravel_down",
            "container": container_name,
            **({"error": out.strip()} if code != 0 and out.strip() else {}),
        }

    code, out = DockerEngineService.exec_in_container(
        cid, ["sh", "-c", "rm -f storage/framework/down"], timeout=6
    )
    return {
        "applied": code == 0,
        "driver": "laravel_up",
        "container": container_name,
        **({"error": out.strip()} if code != 0 and out.strip() else {}),
    }
