import os
try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseModel as BaseSettings  # type: ignore

class AgentSettings(BaseSettings):
    SERVER_ID: str = os.getenv("SERVER_ID", "default-server")
    AGENT_TOKEN: str = os.getenv("AGENT_TOKEN", "psm_agent_token_dev")
    CONTROL_PLANE_URL: str = os.getenv("CONTROL_PLANE_URL", "http://localhost:8000")
    AGENT_VERSION: str = "0.1.0"

    # Optimized background loop intervals (seconds)
    # METRICS_INTERVAL=5s streams lightweight /proc CPU/RAM/Network in real-time while heavy Docker stats stay cached (30s)
    HEARTBEAT_INTERVAL: int = int(os.getenv("HEARTBEAT_INTERVAL", "15"))
    METRICS_INTERVAL: int = int(os.getenv("METRICS_INTERVAL", "5"))
    CONTAINER_STATS_TTL: int = int(os.getenv("CONTAINER_STATS_TTL", "30"))
    INVENTORY_INTERVAL: int = int(os.getenv("INVENTORY_INTERVAL", "60"))
    POLL_IDLE_INTERVAL: float = float(os.getenv("POLL_IDLE_INTERVAL", "30.0"))
    POLL_DISCONNECTED_INTERVAL: float = float(os.getenv("POLL_DISCONNECTED_INTERVAL", "5.0"))

    # Deployment & Build Resource Guard settings (blank/0 = no hard container cap)
    BUILD_NICE_LEVEL: int = int(os.getenv("PSM_BUILD_NICE", "15"))
    BUILD_IONICE_CLASS: int = int(os.getenv("PSM_BUILD_IONICE_CLASS", "2"))
    BUILD_IONICE_LEVEL: int = int(os.getenv("PSM_BUILD_IONICE_LEVEL", "7"))
    BUILD_CGROUP_PARENT: str = os.getenv("PSM_BUILD_CGROUP_PARENT", "homelab-build.slice")
    NODE_MAX_OLD_SPACE_MB: int = int(os.getenv("PSM_NODE_MAX_OLD_SPACE_MB", "896"))
    LOW_RAM_NEXT_BUILD: bool = os.getenv("PSM_LOW_RAM_NEXT_BUILD", "true").lower() in ("true", "1", "yes")
    APP_MEMORY_LIMIT: str = os.getenv("PSM_APP_MEMORY_LIMIT", "")
    APP_MEMORY_SWAP: str = os.getenv("PSM_APP_MEMORY_SWAP", "")
    APP_CPUS: str = os.getenv("PSM_APP_CPUS", "")
    MAX_KEPT_RELEASES: int = int(os.getenv("PSM_MAX_KEPT_RELEASES", "3"))

    # Workspace paths
    BASE_DIR: str = os.getenv("PSM_BASE_DIR", "./storage/psm")
    WORKSPACE_DIR: str = os.path.join(os.getenv("PSM_BASE_DIR", "./storage/psm"), "workspaces")
    RELEASE_DIR: str = os.path.join(os.getenv("PSM_BASE_DIR", "./storage/psm"), "releases")

    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }

agent_settings = AgentSettings()
os.makedirs(agent_settings.WORKSPACE_DIR, exist_ok=True)
os.makedirs(agent_settings.RELEASE_DIR, exist_ok=True)

