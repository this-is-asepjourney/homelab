import asyncio
import json
import logging
import httpx
import websockets
from apps.agent.app.core.config import agent_settings
from apps.agent.app.collectors.metrics import get_metrics_snapshot
from apps.agent.app.collectors.system import get_system_info
from apps.agent.app.docker.client import docker_manager
from apps.agent.app.jobs.executor import JobExecutor, is_deployment_active

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("psm-agent")

_ws_connected_event = asyncio.Event()


def _collect_inventory_snapshot() -> dict | None:
    if not docker_manager.is_available():
        return None
    return {
        "server_id": agent_settings.SERVER_ID,
        "containers": docker_manager.list_containers(),
        "images": docker_manager.list_images(),
        "volumes": docker_manager.list_volumes(),
        "networks": docker_manager.list_networks(),
    }


async def register_agent(http_client: httpx.AsyncClient):
    """Initial handshake and registration with Control Plane with retry."""
    url = f"{agent_settings.CONTROL_PLANE_URL}/api/v1/agents/register"
    sys_info = await asyncio.to_thread(get_system_info)
    payload = {
        "server_id": agent_settings.SERVER_ID,
        "agent_token": agent_settings.AGENT_TOKEN,
        "agent_version": agent_settings.AGENT_VERSION,
        "os_name": sys_info["os_name"],
        "os_version": sys_info["os_version"],
        "architecture": sys_info["architecture"],
        "hostname": sys_info["hostname"]
    }
    for attempt in range(1, 6):
        logger.info(f"Registering with Control Plane at {url} (attempt {attempt})...")
        try:
            res = await http_client.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get("server_id"):
                    agent_settings.SERVER_ID = data["server_id"]
                logger.info(f"Successfully registered with Control Plane (server_id={agent_settings.SERVER_ID}).")
                return
            else:
                logger.warning(f"Registration response: {res.status_code} - {res.text}")
        except Exception as e:
            logger.warning(f"Registration request error: {e}")
        await asyncio.sleep(3)


async def heartbeat_loop(http_client: httpx.AsyncClient):
    """Sends lightweight heartbeat without opening new TCP sockets every tick."""
    url = f"{agent_settings.CONTROL_PLANE_URL}/api/v1/agents/heartbeat"
    while True:
        try:
            payload = {
                "agent_id": f"agent-{agent_settings.SERVER_ID}",
                "server_id": agent_settings.SERVER_ID,
                "agent_version": agent_settings.AGENT_VERSION,
                "status": "online"
            }
            await http_client.post(url, json=payload, timeout=5)
        except Exception as e:
            logger.debug(f"Heartbeat failed: {e}")
        await asyncio.sleep(agent_settings.HEARTBEAT_INTERVAL)


async def metrics_loop(http_client: httpx.AsyncClient):
    """Collects & reports CPU, RAM, Disk, Network off the event loop thread."""
    url = f"{agent_settings.CONTROL_PLANE_URL}/api/v1/agents/metrics"
    while True:
        try:
            metrics = await asyncio.to_thread(get_metrics_snapshot)
            metrics["server_id"] = agent_settings.SERVER_ID
            await http_client.post(url, json=metrics, timeout=6)
        except Exception as e:
            logger.debug(f"Metrics report failed: {e}")
        # Back off slightly while heavy Docker builds are running
        interval = agent_settings.METRICS_INTERVAL * (2 if is_deployment_active() else 1)
        await asyncio.sleep(interval)


async def inventory_loop(http_client: httpx.AsyncClient):
    """Syncs Docker containers, images, volumes, and networks off-thread, pausing during active builds."""
    url = f"{agent_settings.CONTROL_PLANE_URL}/api/v1/agents/inventory"
    while True:
        try:
            if not is_deployment_active():
                payload = await asyncio.to_thread(_collect_inventory_snapshot)
                if payload:
                    headers = {
                        "X-Agent-Token": agent_settings.AGENT_TOKEN,
                        "X-Server-Id": agent_settings.SERVER_ID,
                    }
                    await http_client.post(url, json=payload, headers=headers, timeout=10)
        except Exception as e:
            logger.debug(f"Inventory sync failed: {e}")
        await asyncio.sleep(agent_settings.INVENTORY_INTERVAL)


async def websocket_loop():
    """Maintains persistent WebSocket connection to Control Plane for instant job delivery."""
    ws_base = agent_settings.CONTROL_PLANE_URL.replace("http://", "ws://").replace("https://", "wss://")

    retry_delay = 2
    while True:
        ws_url = f"{ws_base}/api/v1/agents/connect?server_id={agent_settings.SERVER_ID}&token={agent_settings.AGENT_TOKEN}"
        try:
            logger.info(f"Connecting to Control Plane WSS: {ws_url}")
            async with websockets.connect(ws_url, ping_interval=25, ping_timeout=20) as ws:
                logger.info("Persistent WSS connection established.")
                _ws_connected_event.set()
                retry_delay = 2
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if data.get("event") == "job.dispatch":
                        logger.info(f"Received job dispatch: {data.get('job_type')} (id: {data.get('job_id')})")
                        asyncio.get_running_loop().run_in_executor(
                            None,
                            JobExecutor.execute_job,
                            data.get("job_id"),
                            data.get("job_type"),
                            data.get("server_id"),
                            data.get("payload")
                        )
        except Exception as e:
            _ws_connected_event.clear()
            logger.warning(f"WebSocket disconnected or failed ({e}). Reconnecting in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)


async def job_polling_loop(http_client: httpx.AsyncClient):
    """Adaptive fallback poller: sleeps longer while WebSocket is active, polls faster only on disconnect."""
    url = f"{agent_settings.CONTROL_PLANE_URL}/api/v1/agents/jobs/claim"
    headers = {
        "X-Agent-Token": agent_settings.AGENT_TOKEN,
        "X-Server-Id": agent_settings.SERVER_ID
    }
    while True:
        try:
            res = await http_client.post(url, headers=headers, timeout=6)
            if res.status_code == 200:
                data = res.json()
                job_data = data.get("job")
                if job_data:
                    logger.info(f"Claimed pending job via poll: {job_data.get('job_type')} (id: {job_data.get('id')})")
                    asyncio.get_running_loop().run_in_executor(
                        None,
                        JobExecutor.execute_job,
                        job_data.get("id"),
                        job_data.get("job_type"),
                        job_data.get("server_id"),
                        job_data.get("payload")
                    )
        except Exception as e:
            logger.debug(f"Job polling check failed: {e}")

        poll_sleep = (
            agent_settings.POLL_IDLE_INTERVAL
            if _ws_connected_event.is_set()
            else agent_settings.POLL_DISCONNECTED_INTERVAL
        )
        await asyncio.sleep(poll_sleep)


async def main():
    logger.info(f"Starting PSM Agent v{agent_settings.AGENT_VERSION} for server '{agent_settings.SERVER_ID}'")
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    async with httpx.AsyncClient(limits=limits, timeout=10) as http_client:
        await register_agent(http_client)
        await asyncio.gather(
            heartbeat_loop(http_client),
            metrics_loop(http_client),
            inventory_loop(http_client),
            websocket_loop(),
            job_polling_loop(http_client)
        )


if __name__ == "__main__":
    asyncio.run(main())
