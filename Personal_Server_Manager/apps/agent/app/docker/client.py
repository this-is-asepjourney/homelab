import subprocess
import json
import logging
import time
from datetime import datetime, timezone
from typing import List, Dict, Any
from apps.agent.app.core.config import agent_settings

logger = logging.getLogger(__name__)


def _format_bytes_human(num_bytes: Any) -> str:
    try:
        val = float(num_bytes or 0)
    except Exception:
        return "0 MB"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(val) < 1024.0 or unit == "TB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return "0 MB"


def _format_created_since(created_ts: Any) -> str:
    try:
        ts = int(created_ts)
        delta = max(0, int(datetime.now(timezone.utc).timestamp()) - ts)
        if delta < 60:
            return "just now"
        if delta < 3600:
            return f"{delta // 60}m ago"
        if delta < 86400:
            return f"{delta // 3600}h ago"
        return f"{delta // 86400}d ago"
    except Exception:
        return "recently"


class DockerManager:
    def __init__(self):
        self._sdk = None
        self._stats_cache: List[Dict[str, Any]] = []
        self._stats_cache_time: float = 0.0
        try:
            import docker
            self._sdk = docker.from_env(timeout=5)
        except Exception as e:
            logger.info(f"Docker SDK not available or daemon not running: {e}. Falling back to CLI.")

    def is_available(self) -> bool:
        if self._sdk:
            try:
                self._sdk.ping()
                return True
            except Exception:
                pass
        try:
            res = subprocess.run(["docker", "info"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
            return res.returncode == 0
        except Exception:
            return False

    def list_containers(self) -> List[Dict[str, Any]]:
        if not self.is_available():
            return []

        if self._sdk:
            try:
                # Single low-level API request (avoids N+1 per-container image inspect calls)
                raw_containers = self._sdk.api.containers(all=True)
                containers = []
                for item in raw_containers:
                    ports_list = []
                    for p in item.get("Ports") or []:
                        internal_num = p.get("PrivatePort")
                        external_num = p.get("PublicPort", internal_num)
                        proto = p.get("Type", "tcp")
                        if internal_num is not None:
                            ports_list.append({
                                "internal": int(internal_num) if str(internal_num).isdigit() else internal_num,
                                "external": int(external_num) if str(external_num).isdigit() else internal_num,
                                "protocol": proto,
                            })
                    names = item.get("Names") or []
                    clean_name = names[0].lstrip("/") if names else item.get("Id", "")[:12]
                    state = (item.get("State") or "exited").lower()
                    containers.append({
                        "docker_id": (item.get("Id") or "")[:12],
                        "name": clean_name,
                        "image": item.get("Image") or "unknown",
                        "status": "running" if state == "running" else "stopped",
                        "state": state,
                        "ports": ports_list,
                    })
                return containers
            except Exception as e:
                logger.warning(f"SDK list_containers failed, trying CLI: {e}")

        try:
            cmd = ["docker", "ps", "-a", "--format", "{{json .}}"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            containers = []
            for line in res.stdout.strip().split("\n"):
                if line.strip():
                    item = json.loads(line)
                    containers.append({
                        "docker_id": item.get("ID", ""),
                        "name": item.get("Names", "").replace("/", ""),
                        "image": item.get("Image", ""),
                        "status": "running" if "Up" in item.get("Status", "") else "stopped",
                        "state": item.get("State", "exited"),
                        "ports": [],
                    })
            return containers
        except Exception as e:
            logger.error(f"Error listing containers: {e}")
            return []

    def list_images(self) -> List[Dict[str, Any]]:
        if not self.is_available():
            return []
        if self._sdk:
            try:
                raw_images = self._sdk.api.images()
                images = []
                for img in raw_images:
                    short_id = (img.get("Id") or "").replace("sha256:", "")[:12]
                    size_str = _format_bytes_human(img.get("Size", 0))
                    created_str = _format_created_since(img.get("Created"))
                    tags = img.get("RepoTags") or ["<none>:<none>"]
                    for repo_tag in tags:
                        if ":" in repo_tag:
                            repo, tag = repo_tag.rsplit(":", 1)
                        else:
                            repo, tag = repo_tag, "latest"
                        images.append({
                            "id": short_id,
                            "repository": repo,
                            "tag": tag,
                            "size": size_str,
                            "created": created_str,
                        })
                return images
            except Exception:
                pass
        try:
            cmd = ["docker", "images", "--format", "{{json .}}"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            images = []
            for line in res.stdout.strip().split("\n"):
                if line.strip():
                    item = json.loads(line)
                    images.append({
                        "id": item.get("ID", ""),
                        "repository": item.get("Repository", "<none>"),
                        "tag": item.get("Tag", "latest"),
                        "size": item.get("Size", "0 MB"),
                        "created": item.get("CreatedSince", "recently"),
                    })
            return images
        except Exception:
            return []

    def list_volumes(self) -> List[Dict[str, Any]]:
        if not self.is_available():
            return []
        if self._sdk:
            try:
                raw_vols = (self._sdk.api.volumes() or {}).get("Volumes") or []
                return [
                    {
                        "name": v.get("Name", ""),
                        "driver": v.get("Driver", "local"),
                        "mountpoint": v.get("Mountpoint", f"/var/lib/docker/volumes/{v.get('Name', '')}/_data"),
                    }
                    for v in raw_vols
                ]
            except Exception:
                pass
        try:
            cmd = ["docker", "volume", "ls", "--format", "{{json .}}"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            volumes = []
            for line in res.stdout.strip().split("\n"):
                if line.strip():
                    item = json.loads(line)
                    volumes.append({
                        "name": item.get("Name", ""),
                        "driver": item.get("Driver", "local"),
                        "mountpoint": item.get("Mountpoint", f"/var/lib/docker/volumes/{item.get('Name', '')}/_data"),
                    })
            return volumes
        except Exception:
            return []

    def list_networks(self) -> List[Dict[str, Any]]:
        if not self.is_available():
            return []
        if self._sdk:
            try:
                raw_nets = self._sdk.api.networks() or []
                return [
                    {
                        "name": n.get("Name", ""),
                        "driver": n.get("Driver", "bridge"),
                        "scope": n.get("Scope", "local"),
                    }
                    for n in raw_nets
                ]
            except Exception:
                pass
        try:
            cmd = ["docker", "network", "ls", "--format", "{{json .}}"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            networks = []
            for line in res.stdout.strip().split("\n"):
                if line.strip():
                    item = json.loads(line)
                    networks.append({
                        "name": item.get("Name", ""),
                        "driver": item.get("Driver", "bridge"),
                        "scope": item.get("Scope", "local"),
                    })
            return networks
        except Exception:
            return []

    def start_container(self, container_id: str) -> bool:
        try:
            res = subprocess.run(["docker", "start", container_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            return res.returncode == 0
        except Exception:
            return False

    def stop_container(self, container_id: str) -> bool:
        try:
            res = subprocess.run(["docker", "stop", container_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            return res.returncode == 0
        except Exception:
            return False

    def restart_container(self, container_id: str) -> bool:
        try:
            res = subprocess.run(["docker", "restart", container_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            return res.returncode == 0
        except Exception:
            return False

    def remove_container(self, container_id: str, force: bool = False) -> bool:
        try:
            args = ["docker", "rm"]
            if force:
                args.append("-f")
            args.append(container_id)
            res = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            return res.returncode == 0
        except Exception:
            return False

    def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        try:
            res = subprocess.run(["docker", "logs", "--tail", str(tail), container_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            return res.stdout or res.stderr
        except Exception as e:
            return f"Error reading logs: {e}"

    def prune_system(self, all_images: bool = False) -> Dict[str, Any]:
        """Prune unused containers, networks, and images to free disk space."""
        try:
            cmd = ["docker", "system", "prune", "-f"]
            if all_images:
                cmd.append("-a")
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            return {
                "success": res.returncode == 0,
                "output": res.stdout.strip() if res.stdout else res.stderr.strip()
            }
        except Exception as e:
            return {"success": False, "output": f"Prune failed: {e}"}

    def inspect_container(self, container_id: str) -> Dict[str, Any]:
        """Inspect container configuration, networking, mounts, and health."""
        if not self.is_available():
            return {}
        try:
            cmd = ["docker", "inspect", container_id]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            if res.returncode == 0:
                data = json.loads(res.stdout)
                if data and len(data) > 0:
                    info = data[0]
                    # Filter sensitive environment variables
                    safe_envs = []
                    for env_line in info.get("Config", {}).get("Env", []):
                        parts = env_line.split("=", 1)
                        k = parts[0]
                        v = parts[1] if len(parts) > 1 else ""
                        if any(s in k.upper() for s in ["PASS", "SECRET", "KEY", "TOKEN", "AUTH"]):
                            safe_envs.append(f"{k}=********")
                        else:
                            safe_envs.append(f"{k}={v}")

                    net_settings = info.get("NetworkSettings", {})
                    networks = {}
                    for net_name, net_data in net_settings.get("Networks", {}).items():
                        networks[net_name] = {
                            "ip_address": net_data.get("IPAddress", ""),
                            "gateway": net_data.get("Gateway", ""),
                            "mac_address": net_data.get("MacAddress", "")
                        }

                    return {
                        "id": info.get("Id", "")[:12],
                        "name": info.get("Name", "").lstrip("/"),
                        "created": info.get("Created", ""),
                        "state": info.get("State", {}),
                        "image": info.get("Config", {}).get("Image", ""),
                        "cmd": info.get("Config", {}).get("Cmd", []),
                        "env": safe_envs,
                        "networks": networks,
                        "ip_address": net_settings.get("IPAddress", ""),
                        "ports": net_settings.get("Ports", {}),
                        "mounts": [
                            {
                                "type": m.get("Type", ""),
                                "source": m.get("Source", ""),
                                "destination": m.get("Destination", ""),
                                "mode": m.get("Mode", "")
                            }
                            for m in info.get("Mounts", [])
                        ],
                        "restart_policy": info.get("HostConfig", {}).get("RestartPolicy", {}),
                    }
        except Exception as e:
            logger.warning(f"Failed to inspect container {container_id}: {e}")
        return {}

    def get_all_container_stats(self, force: bool = False) -> List[Dict[str, Any]]:
        """Collect live CPU and Memory statistics for running containers with TTL caching and build-time guard."""
        now = time.monotonic()
        ttl = max(5, int(getattr(agent_settings, "CONTAINER_STATS_TTL", 30)))

        # Avoid hammering Docker daemon with `docker stats` while a build/deployment is running
        if not force:
            try:
                from apps.agent.app.jobs.executor import is_deployment_active
                if is_deployment_active():
                    return list(self._stats_cache)
            except Exception:
                pass
            if self._stats_cache and (now - self._stats_cache_time) < ttl:
                return list(self._stats_cache)

        if not self.is_available():
            return list(self._stats_cache)
        try:
            cmd = ["docker", "stats", "--no-stream", "--format", "{{json .}}"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8)
            stats = []
            for line in res.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    stats.append({
                        "container_id": item.get("ID", ""),
                        "name": item.get("Name", ""),
                        "cpu_percent": item.get("CPUPerc", "0.0%").replace("%", "").strip(),
                        "mem_usage": item.get("MemUsage", "0B / 0B"),
                        "mem_percent": item.get("MemPerc", "0.0%").replace("%", "").strip(),
                        "net_io": item.get("NetIO", "0B / 0B"),
                        "block_io": item.get("BlockIO", "0B / 0B")
                    })
                except Exception:
                    pass
            self._stats_cache = stats
            self._stats_cache_time = now
            return stats
        except Exception:
            return list(self._stats_cache)

docker_manager = DockerManager()
