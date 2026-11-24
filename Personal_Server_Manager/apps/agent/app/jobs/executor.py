import threading
import time
import httpx
from typing import Dict, Any, List
from apps.agent.app.jobs.handlers import HANDLERS, ALLOWED_JOB_TYPES
from apps.agent.app.core.config import agent_settings


class _RealtimeLogStreamer:
    """Thread-safe keep-alive log streamer with smart micro-batching and immediate flush on milestones/errors."""

    def __init__(
        self,
        job_id: str,
        flush_interval: float = 0.12,
        max_batch_size: int = 12,
    ):
        self.job_id = job_id
        self.flush_interval = flush_interval
        self.max_batch_size = max_batch_size
        self.url = f"{agent_settings.CONTROL_PLANE_URL}/api/v1/agents/jobs/{job_id}/logs"
        self.headers = {
            "X-Agent-Token": agent_settings.AGENT_TOKEN,
            "X-Server-Id": agent_settings.SERVER_ID,
        }
        self._lock = threading.Lock()
        self._buffer: List[Dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._flush_event = threading.Event()
        self._client = httpx.Client(timeout=6)
        self._worker = threading.Thread(target=self._run_loop, daemon=True)
        self._worker.start()

    @staticmethod
    def _is_priority_entry(entry: Dict[str, Any]) -> bool:
        level = str(entry.get("level") or "").lower()
        msg = str(entry.get("message") or "")
        return (
            level in ("error", "warning")
            or "[STAGE:" in msg
            or "[Auto-Heal]" in msg
        )

    def enqueue(self, entry: Dict[str, Any]) -> None:
        should_wake = False
        with self._lock:
            self._buffer.append(entry)
            if len(self._buffer) >= self.max_batch_size or self._is_priority_entry(entry):
                should_wake = True
        if should_wake:
            self._flush_event.set()

    def _post_batch(self, batch: List[Dict[str, Any]]) -> None:
        if not batch:
            return
        for attempt in range(2):
            try:
                res = self._client.post(self.url, json={"logs": batch}, headers=self.headers)
                if res.status_code < 500:
                    return
            except Exception:
                pass
            if attempt == 0:
                time.sleep(0.25)

    def _drain_and_send(self) -> None:
        while True:
            with self._lock:
                if not self._buffer:
                    return
                batch = self._buffer[: self.max_batch_size * 4]
                del self._buffer[: len(batch)]
            self._post_batch(batch)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._flush_event.wait(timeout=self.flush_interval)
            self._flush_event.clear()
            self._drain_and_send()
        self._drain_and_send()

    def close(self) -> None:
        self._stop_event.set()
        self._flush_event.set()
        self._worker.join(timeout=5)
        self._drain_and_send()
        try:
            self._client.close()
        except Exception:
            pass


class JobExecutor:
    @staticmethod
    def execute_job(job_id: str, job_type: str, server_id: str, payload: Dict[str, Any]):
        if job_type not in ALLOWED_JOB_TYPES or job_type not in HANDLERS:
            raise ValueError(f"Job type '{job_type}' is not allowed or not recognized")

        start_time = time.time()
        log_sequence = 0
        seq_lock = threading.Lock()
        streamer = _RealtimeLogStreamer(job_id=job_id)

        def log_callback(level: str, message: str):
            nonlocal log_sequence
            with seq_lock:
                log_sequence += 1
                seq = log_sequence
            entry = {"sequence": seq, "level": level, "message": message}
            streamer.enqueue(entry)

        handler = HANDLERS[job_type]
        status = "success"
        err_msg = None
        output = {}

        try:
            output = handler(payload, log_callback)
        except Exception as e:
            status = "failed"
            err_msg = str(e)
            log_callback("error", f"Job failed: {err_msg}")
        finally:
            streamer.close()

        duration = int(time.time() - start_time)
        JobExecutor.report_complete(job_id, status, output, err_msg, duration)

    @staticmethod
    def send_logs(job_id: str, logs: List[Dict[str, Any]]):
        url = f"{agent_settings.CONTROL_PLANE_URL}/api/v1/agents/jobs/{job_id}/logs"
        headers = {
            "X-Agent-Token": agent_settings.AGENT_TOKEN,
            "X-Server-Id": agent_settings.SERVER_ID
        }
        try:
            with httpx.Client(timeout=5) as client:
                client.post(url, json={"logs": logs}, headers=headers)
        except Exception:
            pass

    @staticmethod
    def report_complete(job_id: str, status: str, output: Dict[str, Any], error_message: str, duration: int):
        url = f"{agent_settings.CONTROL_PLANE_URL}/api/v1/agents/jobs/{job_id}/complete"
        headers = {
            "X-Agent-Token": agent_settings.AGENT_TOKEN,
            "X-Server-Id": agent_settings.SERVER_ID
        }
        try:
            with httpx.Client(timeout=5) as client:
                client.post(
                    url,
                    json={
                        "status": status,
                        "output": output,
                        "error_message": error_message,
                        "duration_seconds": duration
                    },
                    headers=headers
                )
        except Exception:
            pass

