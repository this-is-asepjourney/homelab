"""
mqtt_influx_consumer.py — Realtime MQTT to PostgreSQL IoT Pipeline Worker
Dedicated IoT Project Architecture:
  Device IoT (ESP32) -> Mosquitto MQTT (:1883) -> Worker API PAP Core -> PostgreSQL eternal_iot (:5432) -> Dashboard Grafana

Runs as a background worker inside PAP Core (pap-worker / pap-api).
"""

from datetime import datetime, timezone
import json
import os
import threading
import time
from typing import Any, Dict, Optional

from app.core.config import get_settings
from app.core.logging import logger
from app.modules.iot.nfc_service import get_eternal_pg_conn, NfcService


class MqttPgConsumer:
    """Consumes incoming ESP32 IoT telemetry from Mosquitto and stores to PostgreSQL `eternal_iot`."""

    def __init__(self):
        settings = get_settings()
        self.host = os.getenv("MQTT_HOST") or getattr(settings, "MQTT_HOST", "mosquitto")
        self.port = int(os.getenv("MQTT_PORT") or getattr(settings, "MQTT_PORT", 1883))
        self.username = os.getenv("MQTT_USERNAME") or os.getenv("MQTT_USER") or getattr(settings, "MQTT_USERNAME", "useriot")
        self.password = os.getenv("MQTT_PASSWORD") or getattr(settings, "MQTT_PASSWORD", "25022002")
        self.client_id = f"pap-pg-consumer-{int(time.time())}"
        self.client = None
        self.is_running = False
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.connected = False
        self.messages_count = 0
        self.last_message_at = None
        self.last_topic = None
        self.devices_last_seen: Dict[str, float] = {}

        self.topics = [
            ("/SysCtrl", 0),
            ("SysCtrl", 0),
            ("Eternal/SysCtrl", 0),
            ("/Eternal/telemetry", 0),
            ("Eternal/telemetry", 0),
            ("/Eternal/attendance", 0),
            ("Eternal/attendance", 0),
            ("/Eternal/rfid", 0),
            ("/Eternal/lock", 0),
            ("/Eternal/#", 0),
            ("Eternal/#", 0),
            ("+/SysCtrl", 0),
            ("pap/iot/+/telemetry", 0),
            ("eternal/#", 0),
            ("eternal/telemetry", 0),
            ("/eternal/telemetry", 0),
            ("/eternal/attendance", 0),
            ("eternal/attendance", 0),
            ("eternal/rfid", 0),
            ("/eternal/rfid", 0),
            ("eternal/lock", 0),
            ("/eternal/lock", 0),
            ("sysctrl", 0),
            ("/sysctrl", 0),
            ("iot/#", 0),
            ("iot/eternal/#", 0),
        ]

    def start(self):
        """Start the background consumer thread."""
        if self.is_running:
            return
        self.is_running = True
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self._run_loop, daemon=True, name="MqttPgWorker")
        self.worker_thread.start()
        logger.info(f"[MQTT->PostgreSQL] IoT consumer worker started on {self.host}:{self.port} -> db: eternal_iot")

    def stop(self):
        self.is_running = False
        self.stop_event.set()
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
            try:
                self.client.loop_stop()
            except Exception:
                pass

    def _run_loop(self):
        """Thread worker loop with automatic reconnection."""
        while self.is_running and not self.stop_event.is_set():
            try:
                import paho.mqtt.client as mqtt

                try:
                    client = mqtt.Client(
                        mqtt.CallbackAPIVersion.VERSION2,
                        client_id=self.client_id
                    )
                except Exception:
                    client = mqtt.Client(client_id=self.client_id)

                self.client = client
                if self.username:
                    client.username_pw_set(self.username, self.password)

                client.on_connect = self._on_connect
                client.on_message = self._on_message
                client.on_disconnect = self._on_disconnect

                logger.info(f"[MQTT->PostgreSQL] Connecting to Mosquitto {self.host}:{self.port}...")
                client.connect(self.host, self.port, keepalive=60)
                client.loop_start()

                while self.is_running and not self.stop_event.is_set():
                    self.stop_event.wait(timeout=1.0)

                try:
                    client.loop_stop()
                    client.disconnect()
                except Exception:
                    pass
            except Exception as e:
                if not self.is_running or self.stop_event.is_set():
                    break
                logger.warning(f"[MQTT->PostgreSQL] Connection error: {e}. Retrying in 10s...")
                self.stop_event.wait(timeout=10)

    def _on_connect(self, client, userdata, flags, rc, *args):
        if rc == 0:
            self.connected = True
            logger.info(f"[MQTT->PostgreSQL] Connected to broker {self.host}:{self.port}!")
            for topic, qos in self.topics:
                client.subscribe(topic, qos)
                logger.info(f"[MQTT->PostgreSQL] Subscribed to topic: {topic}")
        else:
            self.connected = False
            logger.warning(f"[MQTT->PostgreSQL] Connect failed with code {rc}")

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        logger.warning("[MQTT->PostgreSQL] Disconnected from Mosquitto broker")

    def _on_message(self, client, userdata, msg):
        """Process incoming telemetry message and write to PostgreSQL `eternal_iot`."""
        try:
            self.messages_count += 1
            self.last_message_at = datetime.now(timezone.utc).isoformat()
            self.last_topic = msg.topic
            topic = msg.topic
            raw_payload = msg.payload.decode("utf-8", errors="replace").strip()
            if not raw_payload:
                return

            data = None
            try:
                data = json.loads(raw_payload)
            except Exception:
                return

            if not isinstance(data, dict):
                return

            self._process_payload(topic, data)
        except Exception as e:
            logger.debug(f"[MQTT->PostgreSQL] Error handling message on {msg.topic}: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Returns the consumer health status and telemetry counters."""
        return {
            "running": self.is_running,
            "connected": self.connected,
            "broker": f"{self.host}:{self.port}",
            "database": "postgresql://eternal_iot",
            "messages_count": self.messages_count,
            "last_message_at": self.last_message_at,
            "last_topic": self.last_topic,
            "devices_last_seen": self.devices_last_seen,
        }

    def _process_payload(self, topic: str, data: Dict[str, Any]):
        device_serial = (
            data.get("serial")
            or data.get("MCID")
            or data.get("device_serial")
            or data.get("mcid")
            or "ETL-ESP32-001"
        )
        self.devices_last_seen[str(device_serial)] = time.time()
        device_name = data.get("device") or "Eternal Absensi Kerja"
        model = data.get("model") or data.get("IOT_MODEL") or "Eternal-ESP32"

        topic_norm = topic.lower().rstrip("/")
        event_up = str(data.get("event") or "").strip().upper()
        is_attendance_topic = topic_norm.endswith("/attendance") or event_up in (
            "WORK_CHECK_IN",
            "WORK_CHECK_OUT",
            "AUTO_CHECKOUT",
            "MQTT_REMOTE_CHECKIN",
            "MQTT_REMOTE_CHECKOUT",
        )
        is_rfid_topic = (topic_norm.endswith("/rfid") or event_up == "NFC_TAP") and not is_attendance_topic

        # 1a. Handle Dedicated Work Attendance Events (/Eternal/attendance)
        if is_attendance_topic and data.get("uid"):
            uid = data.get("uid")
            user_name = data.get("nama") or data.get("user") or "Pekerja NFC"
            role = data.get("role") or "Operator"
            att_res = {}
            try:
                att_payload = dict(data)
                att_payload.setdefault("device_serial", str(device_serial))
                att_payload.setdefault("device_ip", str(data.get("ip") or data.get("MCIP") or "192.168.25.104"))
                att_res = NfcService.record_tap_event(att_payload)
                logger.info(
                    f"[MQTT->PostgreSQL] Attendance {att_res.get('action')} for {user_name} ({uid}) -> eternal_iot"
                )
            except Exception as e:
                logger.debug(f"[MQTT->PostgreSQL Attendance] Error: {e}")

            try:
                from app.integrations.prometheus import record_nfc_tap_prometheus_metric
                record_nfc_tap_prometheus_metric(device_serial=str(device_serial), role=str(role))
            except Exception:
                pass

            try:
                from app.modules.traffic.broadcaster import traffic_broadcaster
                is_working = att_res.get("work_status") == "WORKING"
                dur_hms = data.get("work_duration_hms") or att_res.get("action") or event_up
                traffic_broadcaster.record_and_broadcast(
                    method="ATTENDANCE",
                    path=topic,
                    status=200,
                    duration_ms=1.6,
                    client_ip=str(data.get("ip") or "192.168.25.104"),
                    source="esp32",
                    target="core",
                    sub_target="postgres",
                    tag="WORK_ATTENDANCE",
                    color="#34d399" if is_working else "#38bdf8",
                    detail=f"ESP32 -> Core -> PostgreSQL eternal_iot [{user_name} ({role}) | {dur_hms}]",
                )
            except Exception:
                pass

        # 1b. Handle NFC RFID Tap Events (/Eternal/rfid)
        if is_rfid_topic and data.get("status") != "registered" and data.get("action") != "REGISTER_USER":
            uid = data.get("uid")
            if uid:
                user_name = data.get("nama") or data.get("user") or "Tamu"
                role = data.get("role") or "Operator"
                tap_res = {}
                try:
                    rfid_payload = dict(data)
                    rfid_payload.setdefault("device_serial", str(device_serial))
                    rfid_payload.setdefault("device_ip", str(data.get("ip") or data.get("MCIP") or "192.168.25.104"))
                    tap_res = NfcService.record_tap_event(rfid_payload)
                    logger.info(f"[MQTT->PostgreSQL] Recorded NFC event for {user_name} ({uid}) -> PostgreSQL")
                except Exception as e:
                    logger.debug(f"[MQTT->PostgreSQL NFC Tap] Error: {e}")

                try:
                    from app.integrations.prometheus import record_nfc_tap_prometheus_metric
                    record_nfc_tap_prometheus_metric(device_serial=str(device_serial), role=str(role))
                except Exception:
                    pass

                # Broadcast live event to Architecture Map UI
                try:
                    from app.modules.traffic.broadcaster import traffic_broadcaster
                    denied = tap_res.get("work_status") == "DENIED" or data.get("status") == "denied"
                    traffic_broadcaster.record_and_broadcast(
                        method="NFC_TAP",
                        path=topic,
                        status=403 if denied else 200,
                        duration_ms=1.8,
                        client_ip=str(data.get("ip") or "192.168.25.104"),
                        source="esp32",
                        target="core",
                        sub_target="postgres",
                        tag="NFC_AUTH",
                        color="#ef4444" if denied else "#34d399",
                        detail=f"ESP32 -> Core -> PostgreSQL eternal_iot [UID: {uid} | {user_name} ({role})]",
                    )
                except Exception:
                    pass

        # 2. Handle Telemetry & Work Attendance State
        f = self._parse_telemetry(data)
        has_data = any(v is not None for v in f.values())
        if has_data:
            self._sync_pg_telemetry(
                device_serial=str(device_serial),
                device_name=str(device_name),
                model=str(model),
                temp=f["temperature"],
                hum=f["humidity"],
                heat_idx=f["heat_index"],
                rssi=f["rssi"],
                is_locked=f["is_locked"],
                rem_sec=f["remaining_sec"],
                uptime=f["uptime_sec"],
                heap=f["free_heap"],
                ip=data.get("ip") or data.get("MCIP"),
                mac=data.get("mac") or data.get("MAC"),
                ssid=data.get("ap") or data.get("ssid"),
                firmware=data.get("VER"),
                air_quality=data.get("air_quality"),
                work_status=f["work_status"],
                last_work_duration_sec=f["last_work_duration_sec"],
                last_work_duration_str=f["last_work_duration_str"],
                total_work_sec=f["total_work_sec"],
                total_work_sessions=f["total_work_sessions"],
                active_uid=f["active_uid"],
                active_user=f["active_user"],
                active_username=f["active_username"],
                active_role=f["active_role"],
                check_in_time=f["check_in_time"],
            )

            # Update live Prometheus metrics for Prometheus dashboards
            try:
                from app.integrations.prometheus import record_esp32_prometheus_telemetry
                record_esp32_prometheus_telemetry(
                    device_serial=str(device_serial),
                    model=str(model),
                    temperature=f["temperature"],
                    humidity=f["humidity"],
                    heat_index=f["heat_index"],
                    rssi=f["rssi"],
                    is_locked=f["is_locked"],
                    remaining_sec=f["remaining_sec"],
                    uptime_sec=f["uptime_sec"],
                    free_heap=f["free_heap"],
                )
            except Exception:
                pass

            # Broadcast live telemetry stream to Architecture Map UI
            try:
                from app.modules.traffic.broadcaster import traffic_broadcaster
                traffic_broadcaster.record_and_broadcast(
                    method="MQTT",
                    path=topic,
                    status=200,
                    duration_ms=1.2,
                    client_ip=str(data.get("ip") or "192.168.25.104"),
                    source="esp32",
                    target="mqtt",
                    sub_target="postgres",
                    tag="TELEMETRY",
                    color="#06b6d4",
                    detail=f"ESP32 -> Mosquitto {topic} -> PostgreSQL eternal_iot"
                )
            except Exception:
                pass

    @staticmethod
    def _parse_telemetry(payload: Dict[str, Any]) -> Dict[str, Any]:
        def _num(*keys, cast=float):
            for k in keys:
                v = payload.get(k)
                if v is not None and v != "":
                    try:
                        return cast(v)
                    except (TypeError, ValueError):
                        return None
            return None

        def _text(*keys) -> Optional[str]:
            for k in keys:
                v = payload.get(k)
                if v is not None:
                    s = str(v).strip()
                    if s and s not in ("-", "null", "None"):
                        return s
            return None

        work_active_val = payload.get("work_active")
        is_locked_val = payload.get("is_locked")
        work_status_raw = _text("work_status", "lock_status")

        if work_active_val is not None:
            is_locked = 0 if work_active_val in (1, True, "1", "true", "TRUE") else 1
        elif is_locked_val is not None:
            is_locked = 1 if is_locked_val in (1, True, "1", "true", "TRUE") else 0
        elif work_status_raw is not None:
            lock_str = work_status_raw.upper()
            is_locked = 0 if ("WORKING" in lock_str or "UNLOCK" in lock_str or "OPEN" in lock_str) else 1
        else:
            is_locked = None

        if work_status_raw:
            ws_up = work_status_raw.upper()
            if "WORKING" in ws_up or "UNLOCK" in ws_up:
                work_status = "WORKING"
            elif "COMPLETED" in ws_up or "STANDBY" in ws_up or "LOCK" in ws_up:
                work_status = "STANDBY"
            else:
                work_status = ws_up
        elif is_locked is not None:
            work_status = "WORKING" if is_locked == 0 else "STANDBY"
        else:
            work_status = None

        temp = _num("suhu", "temperature", "temp", "temperature_c", "t")
        hum = _num("humidity", "hum", "kelembapan", "humidity_pct", "h")
        heat_idx = _num("heat_index", "heatIndex", "heat_index_c", "hi")
        if heat_idx is None and temp is not None and temp > 0:
            heat_idx = temp

        work_elapsed_sec = _num("work_elapsed_sec", "remaining_sec", "lock_duration_sec", "remaining", cast=int)

        return {
            "temperature": temp,
            "humidity": hum,
            "heat_index": heat_idx,
            "rssi": _num("rssi", "wifi_rssi", "wifiRssi", cast=int),
            "is_locked": is_locked,
            "work_status": work_status,
            "work_elapsed_sec": work_elapsed_sec,
            "remaining_sec": work_elapsed_sec,
            "last_work_duration_sec": _num("last_work_duration_sec", "work_duration_sec", cast=int),
            "last_work_duration_str": _text("last_work_duration_str", "work_duration_hms"),
            "total_work_sec": _num("total_work_sec", cast=int),
            "total_work_sessions": _num("total_work_sessions", cast=int),
            "active_uid": _text("active_uid", "uid"),
            "active_user": _text("active_user", "user", "nama"),
            "active_username": _text("active_username", "username"),
            "active_role": _text("active_role", "role"),
            "check_in_time": _text("check_in_time"),
            "uptime_sec": _num("uptime_sec", "uptime", "upTime", cast=int),
            "free_heap": _num("free_heap", "heap", "freeHeap", cast=int),
        }

    def _sync_pg_telemetry(
        self,
        device_serial: str,
        device_name: str,
        model: str = "Eternal-ESP32",
        temp=None,
        hum=None,
        heat_idx=None,
        rssi=None,
        is_locked=None,
        rem_sec=None,
        uptime=None,
        heap=None,
        ip=None,
        mac=None,
        ssid=None,
        firmware=None,
        air_quality=None,
        work_status=None,
        last_work_duration_sec=None,
        last_work_duration_str=None,
        total_work_sec=None,
        total_work_sessions=None,
        active_uid=None,
        active_user=None,
        active_username=None,
        active_role=None,
        check_in_time=None,
    ):
        """Keep PostgreSQL `eternal_iot` snapshot & logs in sync with incoming MQTT telemetry."""
        conn = None
        try:
            conn = get_eternal_pg_conn()
            with conn.cursor() as cur:
                # 1. Update latest snapshot in iot_devices
                cols = {"device_name": device_name}
                if temp is not None:
                    cols["temperature_c"] = temp
                if hum is not None:
                    cols["humidity_pct"] = hum
                if heat_idx is not None:
                    cols["heat_index_c"] = heat_idx
                if rssi is not None:
                    cols["wifi_rssi"] = rssi
                    cols["rssi"] = rssi
                if uptime is not None:
                    cols["uptime_sec"] = uptime
                if heap is not None:
                    cols["free_heap"] = heap
                if ip:
                    cols["ip_address"] = str(ip)
                if mac:
                    cols["mac_address"] = str(mac)
                if ssid:
                    cols["ssid"] = str(ssid)
                if firmware:
                    cols["firmware_version"] = str(firmware)
                if air_quality:
                    cols["air_quality"] = str(air_quality)

                names = ["device_serial", "model", "status", "last_seen_at", "updated_at"] + list(cols.keys())
                placeholders = ["%s", "%s", "'online'", "NOW()", "NOW()"] + ["%s"] * len(cols)
                updates = ["status = 'online'", "last_seen_at = NOW()", "updated_at = NOW()"] + [f"{k} = EXCLUDED.{k}" for k in cols]

                sql_device = f"""
                    INSERT INTO iot_devices ({', '.join(names)})
                    VALUES ({', '.join(placeholders)})
                    ON CONFLICT (device_serial) DO UPDATE
                    SET {', '.join(updates)};
                """
                cur.execute(sql_device, [device_serial, model] + list(cols.values()))

                # 2. Append time-series point to iot_telemetry_logs
                if temp is not None and hum is not None:
                    work_active_int = 1 if is_locked == 0 else 0
                    elapsed_int = (rem_sec or 0) if is_locked == 0 else 0
                    cur.execute("""
                        INSERT INTO iot_telemetry_logs
                            (device_serial, temperature_c, humidity_pct, heat_index_c, wifi_rssi,
                             is_locked, work_active, work_elapsed_sec, remaining_sec, uptime_sec, free_heap, recorded_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW());
                    """, (
                        device_serial,
                        temp or 0.0,
                        hum or 0.0,
                        heat_idx or 0.0,
                        rssi or 0,
                        is_locked if is_locked is not None else 1,
                        work_active_int,
                        elapsed_int,
                        elapsed_int,
                        uptime or 0,
                        heap or 0,
                    ))

                # 3. Update device_locks (attendance live status snapshot)
                if is_locked is not None:
                    eff_status = work_status or ("WORKING" if is_locked == 0 else "STANDBY")
                    elapsed_val = (rem_sec or 0) if is_locked == 0 else 0
                    clean_user = None if active_user in (None, "", "-", "Standby") else active_user
                    cur.execute("""
                        INSERT INTO device_locks
                            (device_serial, device_name, is_locked, lock_status, work_status, work_elapsed_sec,
                             last_work_duration_sec, last_work_duration_str, total_work_sec, total_work_sessions,
                             check_in_time, unlocked_by_uid, unlocked_by_name, unlocked_by_username, unlocked_by_role,
                             unlock_duration_sec, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (device_serial) DO UPDATE
                        SET is_locked = EXCLUDED.is_locked,
                            lock_status = EXCLUDED.lock_status,
                            work_status = EXCLUDED.work_status,
                            work_elapsed_sec = EXCLUDED.work_elapsed_sec,
                            last_work_duration_sec = COALESCE(NULLIF(EXCLUDED.last_work_duration_sec, 0), device_locks.last_work_duration_sec),
                            last_work_duration_str = COALESCE(NULLIF(EXCLUDED.last_work_duration_str, ''), device_locks.last_work_duration_str),
                            total_work_sec = GREATEST(COALESCE(device_locks.total_work_sec, 0), COALESCE(EXCLUDED.total_work_sec, 0)),
                            total_work_sessions = GREATEST(COALESCE(device_locks.total_work_sessions, 0), COALESCE(EXCLUDED.total_work_sessions, 0)),
                            check_in_time = COALESCE(EXCLUDED.check_in_time, device_locks.check_in_time),
                            unlocked_by_uid = COALESCE(EXCLUDED.unlocked_by_uid, device_locks.unlocked_by_uid),
                            unlocked_by_name = COALESCE(EXCLUDED.unlocked_by_name, device_locks.unlocked_by_name),
                            unlocked_by_username = COALESCE(EXCLUDED.unlocked_by_username, device_locks.unlocked_by_username),
                            unlocked_by_role = COALESCE(EXCLUDED.unlocked_by_role, device_locks.unlocked_by_role),
                            unlock_duration_sec = EXCLUDED.unlock_duration_sec,
                            updated_at = NOW();
                    """, (
                        device_serial,
                        device_name,
                        is_locked,
                        eff_status,
                        eff_status,
                        elapsed_val,
                        last_work_duration_sec or 0,
                        last_work_duration_str or "00:00:00",
                        total_work_sec or 0,
                        total_work_sessions or 0,
                        check_in_time,
                        active_uid,
                        clean_user,
                        active_username,
                        active_role,
                        elapsed_val,
                    ))
        except Exception as e:
            logger.debug(f"[MQTT->PostgreSQL Sync] Error: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass


# Global singleton instance
mqtt_influx_consumer = MqttPgConsumer()
