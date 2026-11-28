"""
nfc_service.py — NFC Identity, Roles, Whitelist & Access Logs Management
Dedicated IoT Project Database: PostgreSQL `eternal_iot` (:5432).

Pipeline Arsitektur:
  Device IoT (ESP32) -> Mosquitto MQTT (:1883) -> Worker API PAP Core -> PostgreSQL (:5432) -> Dashboard Grafana
"""

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional
import urllib.request


def _auto_create_eternal_db(host: str, port: int, user: str, password: str, db_name: str) -> bool:
    """Auto-create eternal_iot database if missing using maintenance database connection."""
    for mdb in ["postgres", "fadb", "pap_core"]:
        try:
            try:
                import psycopg
                with psycopg.connect(
                    host=host, port=port, user=user, password=password, dbname=mdb, autocommit=True
                ) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                        if not cur.fetchone():
                            cur.execute(f'CREATE DATABASE "{db_name}" OWNER "{user}";')
                            cur.execute(f'GRANT ALL PRIVILEGES ON DATABASE "{db_name}" TO "{user}";')
                            return True
            except ImportError:
                import psycopg2
                conn = psycopg2.connect(
                    host=host, port=port, user=user, password=password, dbname=mdb
                )
                conn.autocommit = True
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
                        if not cur.fetchone():
                            cur.execute(f'CREATE DATABASE "{db_name}" OWNER "{user}";')
                            cur.execute(f'GRANT ALL PRIVILEGES ON DATABASE "{db_name}" TO "{user}";')
                            return True
                finally:
                    conn.close()
        except Exception:
            continue
    return False


def get_eternal_pg_conn():
    """Connect to PostgreSQL `eternal_iot` dedicated IoT database with auto-provisioning."""
    host = os.getenv("POSTGRES_HOST") or os.getenv("PSM_DATABASE_HOST") or "postgres"
    port = int(os.getenv("POSTGRES_PORT") or os.getenv("PSM_DATABASE_PORT") or 5432)
    user = os.getenv("POSTGRES_USER") or os.getenv("PSM_DATABASE_USER") or "fadb"
    password = os.getenv("POSTGRES_PASSWORD") or os.getenv("PSM_DATABASE_PASSWORD") or "wokeh123"
    db_name = os.getenv("IOT_POSTGRES_DB", "eternal_iot")

    conn = None
    for attempt in range(2):
        try:
            try:
                import psycopg
                from psycopg.rows import dict_row
                conn = psycopg.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    dbname=db_name,
                    autocommit=True,
                    row_factory=dict_row
                )
                break
            except ImportError:
                import psycopg2
                import psycopg2.extras
                conn = psycopg2.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    dbname=db_name
                )
                conn.autocommit = True
                break
        except Exception as e:
            if attempt == 0 and ("does not exist" in str(e).lower() or "database" in str(e).lower()):
                _auto_create_eternal_db(host, port, user, password, db_name)
            else:
                if attempt == 1:
                    raise e

    if conn:
        try:
            _ensure_tables(conn)
        except Exception:
            pass
    return conn


# Backward-compatibility alias
def get_eternal_mysql_conn():
    return get_eternal_pg_conn()


def _clean_uid(uid: Any) -> str:
    return str(uid or "").strip().upper().replace(" ", "").replace(":", "")


def _opt_int(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _opt_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _clean_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return None if s in ("", "-", "null", "None") else s


def _format_hms(seconds: Optional[int]) -> str:
    sec = max(0, int(seconds or 0))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _classify_attendance_action(
    action: Any = "",
    status: Any = "",
    work_status: Any = "",
    work_active: Any = None,
) -> tuple:
    """Classify ESP32 attendance/NFC event into (canonical_action, kind).

    kind is one of: 'DENIED', 'CHECK_IN', 'CHECK_OUT', 'AUTO_CHECKOUT'.
    """
    act = str(action or "").strip().upper()
    st = str(status or "").strip().upper()
    ws = str(work_status or "").strip().upper()

    if "DENIED" in act or st == "DENIED":
        return ("NFC_DENIED", "DENIED")

    if "AUTO_CHECKOUT" in act or "AUTO_LOCK" in act:
        return ("AUTO_CHECKOUT", "AUTO_CHECKOUT")

    is_active = None
    if work_active is not None:
        if work_active in (1, True, "1", "true", "TRUE"):
            is_active = True
        elif work_active in (0, False, "0", "false", "FALSE"):
            is_active = False

    if (
        "CHECK_IN" in act
        or "CHECKIN" in act
        or "UNLOCK" in act
        or act.endswith("_ON")
        or act == "TAP_ON"
        or st == "WORKING"
        or ws == "WORKING"
        or is_active is True
    ):
        canonical = "WORK_CHECK_IN" if act in ("", "NFC_TAP", "TAP_ON") else act
        return (canonical, "CHECK_IN")

    if (
        "CHECK_OUT" in act
        or "CHECKOUT" in act
        or "LOCK" in act
        or act.endswith("_OFF")
        or st == "COMPLETED"
        or ws in ("COMPLETED", "STANDBY")
        or is_active is False
    ):
        canonical = "WORK_CHECK_OUT" if act in ("", "NFC_TAP") else act
        return (canonical, "CHECK_OUT")

    return (act or "WORK_CHECK_IN", "CHECK_IN")


def _ensure_tables(conn):
    """Ensure PostgreSQL schema for eternal_iot exists (idempotent migration)."""
    ddl = """
    CREATE TABLE IF NOT EXISTS nfc_users (
        id SERIAL PRIMARY KEY,
        uid VARCHAR(32) NOT NULL UNIQUE,
        nama VARCHAR(100) NOT NULL,
        username VARCHAR(64) NOT NULL,
        role VARCHAR(32) DEFAULT 'Operator',
        pin VARCHAR(16) DEFAULT '',
        is_active SMALLINT DEFAULT 1,
        device_serial VARCHAR(32) DEFAULT 'ETL-ESP32-001',
        total_taps INT DEFAULT 0,
        total_work_sec BIGINT DEFAULT 0,
        total_sessions INT DEFAULT 0,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        last_login_at TIMESTAMP WITH TIME ZONE DEFAULT NULL
    );

    CREATE TABLE IF NOT EXISTS nfc_access_logs (
        id SERIAL PRIMARY KEY,
        uid VARCHAR(32) NOT NULL,
        nama VARCHAR(100) DEFAULT '-',
        username VARCHAR(64) DEFAULT '-',
        role VARCHAR(32) DEFAULT 'Operator',
        action_type VARCHAR(64) NOT NULL,
        lock_duration_sec INT DEFAULT 0,
        work_duration_sec INT DEFAULT 0,
        work_duration_hms VARCHAR(16) DEFAULT '00:00:00',
        check_in_time VARCHAR(64) DEFAULT NULL,
        check_out_time VARCHAR(64) DEFAULT NULL,
        device_serial VARCHAR(32) DEFAULT 'ETL-ESP32-001',
        device_ip VARCHAR(64) DEFAULT '0.0.0.0',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS device_locks (
        device_serial VARCHAR(32) PRIMARY KEY,
        device_name VARCHAR(64) DEFAULT 'Eternal Absensi Kerja',
        is_locked SMALLINT DEFAULT 1,
        lock_status VARCHAR(24) DEFAULT 'STANDBY',
        work_status VARCHAR(24) DEFAULT 'STANDBY',
        work_elapsed_sec INT DEFAULT 0,
        last_work_duration_sec INT DEFAULT 0,
        last_work_duration_str VARCHAR(16) DEFAULT '00:00:00',
        total_work_sec BIGINT DEFAULT 0,
        total_work_sessions INT DEFAULT 0,
        check_in_time VARCHAR(64) DEFAULT NULL,
        unlocked_by_uid VARCHAR(32) DEFAULT NULL,
        unlocked_by_name VARCHAR(100) DEFAULT NULL,
        unlocked_by_username VARCHAR(64) DEFAULT NULL,
        unlocked_by_role VARCHAR(32) DEFAULT NULL,
        unlock_duration_sec INT DEFAULT 0,
        unlocked_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,
        unlock_expires_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,
        last_toggle_action VARCHAR(64) DEFAULT 'WORK_CHECK_OUT',
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS iot_devices (
        id SERIAL PRIMARY KEY,
        device_serial VARCHAR(32) NOT NULL UNIQUE,
        device_name VARCHAR(64) NOT NULL,
        mac_address VARCHAR(32) DEFAULT NULL,
        model VARCHAR(32) DEFAULT 'Eternal-ESP32',
        ip_address VARCHAR(64) DEFAULT '0.0.0.0',
        ssid VARCHAR(64) DEFAULT NULL,
        rssi INT DEFAULT 0,
        wifi_rssi INT DEFAULT 0,
        temperature_c REAL DEFAULT 0,
        humidity_pct REAL DEFAULT 0,
        heat_index_c REAL DEFAULT 0,
        firmware_version VARCHAR(32) DEFAULT '3.2.0',
        mqtt_broker VARCHAR(64) DEFAULT '192.168.25.105',
        status VARCHAR(16) DEFAULT 'online',
        uptime_sec BIGINT DEFAULT 0,
        free_heap BIGINT DEFAULT 0,
        air_quality VARCHAR(32) DEFAULT NULL,
        last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS iot_telemetry_logs (
        id BIGSERIAL PRIMARY KEY,
        device_serial VARCHAR(32) NOT NULL,
        temperature_c REAL DEFAULT 0,
        humidity_pct REAL DEFAULT 0,
        heat_index_c REAL DEFAULT 0,
        wifi_rssi INT DEFAULT 0,
        is_locked SMALLINT DEFAULT 1,
        work_active SMALLINT DEFAULT 0,
        work_elapsed_sec INT DEFAULT 0,
        remaining_sec INT DEFAULT 0,
        uptime_sec BIGINT DEFAULT 0,
        free_heap BIGINT DEFAULT 0,
        recorded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS work_attendance_sessions (
        id SERIAL PRIMARY KEY,
        device_serial VARCHAR(32) DEFAULT 'ETL-ESP32-001',
        session_id INT DEFAULT 0,
        uid VARCHAR(32) NOT NULL,
        nama VARCHAR(100) DEFAULT '-',
        username VARCHAR(64) DEFAULT '-',
        role VARCHAR(32) DEFAULT 'Operator',
        status VARCHAR(24) DEFAULT 'WORKING',
        check_in_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        check_out_at TIMESTAMP WITH TIME ZONE DEFAULT NULL,
        check_in_wib VARCHAR(64) DEFAULT NULL,
        check_out_wib VARCHAR(64) DEFAULT NULL,
        duration_sec INT DEFAULT 0,
        duration_hms VARCHAR(16) DEFAULT '00:00:00',
        duration_detail VARCHAR(64) DEFAULT NULL,
        suhu_in REAL DEFAULT NULL,
        humidity_in REAL DEFAULT NULL,
        suhu_out REAL DEFAULT NULL,
        humidity_out REAL DEFAULT NULL,
        trigger_event VARCHAR(64) DEFAULT 'WORK_CHECK_IN',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    ALTER TABLE nfc_users ADD COLUMN IF NOT EXISTS total_work_sec BIGINT DEFAULT 0;
    ALTER TABLE nfc_users ADD COLUMN IF NOT EXISTS total_sessions INT DEFAULT 0;

    ALTER TABLE nfc_access_logs ADD COLUMN IF NOT EXISTS work_duration_sec INT DEFAULT 0;
    ALTER TABLE nfc_access_logs ADD COLUMN IF NOT EXISTS work_duration_hms VARCHAR(16) DEFAULT '00:00:00';
    ALTER TABLE nfc_access_logs ADD COLUMN IF NOT EXISTS check_in_time VARCHAR(64) DEFAULT NULL;
    ALTER TABLE nfc_access_logs ADD COLUMN IF NOT EXISTS check_out_time VARCHAR(64) DEFAULT NULL;

    ALTER TABLE device_locks ALTER COLUMN lock_status TYPE VARCHAR(24);
    ALTER TABLE device_locks ADD COLUMN IF NOT EXISTS work_status VARCHAR(24) DEFAULT 'STANDBY';
    ALTER TABLE device_locks ADD COLUMN IF NOT EXISTS work_elapsed_sec INT DEFAULT 0;
    ALTER TABLE device_locks ADD COLUMN IF NOT EXISTS last_work_duration_sec INT DEFAULT 0;
    ALTER TABLE device_locks ADD COLUMN IF NOT EXISTS last_work_duration_str VARCHAR(16) DEFAULT '00:00:00';
    ALTER TABLE device_locks ADD COLUMN IF NOT EXISTS total_work_sec BIGINT DEFAULT 0;
    ALTER TABLE device_locks ADD COLUMN IF NOT EXISTS total_work_sessions INT DEFAULT 0;
    ALTER TABLE device_locks ADD COLUMN IF NOT EXISTS check_in_time VARCHAR(64) DEFAULT NULL;

    ALTER TABLE iot_telemetry_logs ADD COLUMN IF NOT EXISTS work_active SMALLINT DEFAULT 0;
    ALTER TABLE iot_telemetry_logs ADD COLUMN IF NOT EXISTS work_elapsed_sec INT DEFAULT 0;

    CREATE INDEX IF NOT EXISTS idx_nfc_users_uid ON nfc_users(uid);
    CREATE INDEX IF NOT EXISTS idx_nfc_access_logs_time ON nfc_access_logs(created_at);
    CREATE INDEX IF NOT EXISTS idx_iot_telemetry_logs_time ON iot_telemetry_logs(recorded_at);
    CREATE INDEX IF NOT EXISTS idx_iot_telemetry_logs_serial ON iot_telemetry_logs(device_serial, recorded_at);
    CREATE INDEX IF NOT EXISTS idx_work_attendance_serial_time ON work_attendance_sessions(device_serial, check_in_at DESC);
    CREATE INDEX IF NOT EXISTS idx_work_attendance_uid_status ON work_attendance_sessions(uid, status);

    INSERT INTO iot_devices (device_serial, device_name, model, ip_address, temperature_c, humidity_pct, heat_index_c, wifi_rssi, rssi, status, uptime_sec, free_heap, firmware_version, last_seen_at, updated_at)
    VALUES ('ETL-ESP32-001', 'Eternal Absensi Kerja', 'Eternal-ESP32', '192.168.25.104', 28.5, 65.0, 29.8, -55, -55, 'online', 3600, 245000, '3.2.0', NOW(), NOW())
    ON CONFLICT (device_serial) DO NOTHING;

    INSERT INTO device_locks (device_serial, device_name, is_locked, lock_status, work_status, work_elapsed_sec, last_work_duration_sec, last_work_duration_str, total_work_sec, total_work_sessions, unlocked_by_uid, unlocked_by_name, unlocked_by_username, unlocked_by_role, unlock_duration_sec, last_toggle_action, updated_at)
    VALUES ('ETL-ESP32-001', 'Eternal Absensi Kerja', 1, 'STANDBY', 'STANDBY', 0, 0, '00:00:00', 0, 0, NULL, NULL, NULL, NULL, 0, 'WORK_CHECK_OUT', NOW())
    ON CONFLICT (device_serial) DO NOTHING;

    INSERT INTO nfc_users (uid, nama, username, role, is_active, total_taps, created_at)
    VALUES 
        ('E2801191', 'Administrator', 'admin', 'Superadmin', 1, 5, NOW()),
        ('058E208F87A200', 'Ferdiansyah', 'ferdi', 'Operator', 1, 12, NOW())
    ON CONFLICT (uid) DO NOTHING;

    INSERT INTO nfc_access_logs (uid, nama, username, role, action_type, lock_duration_sec, work_duration_sec, work_duration_hms, device_serial, device_ip, created_at)
    SELECT '058E208F87A200', 'Ferdiansyah', 'ferdi', 'Operator', 'WORK_CHECK_IN', 0, 0, '00:00:00', 'ETL-ESP32-001', '192.168.25.104', NOW() - INTERVAL '15 minutes'
    WHERE NOT EXISTS (SELECT 1 FROM nfc_access_logs LIMIT 1);

    INSERT INTO work_attendance_sessions (device_serial, session_id, uid, nama, username, role, status, check_in_at, check_out_at, duration_sec, duration_hms, duration_detail, suhu_in, humidity_in, suhu_out, humidity_out, trigger_event)
    SELECT 'ETL-ESP32-001', 1, '058E208F87A200', 'Ferdiansyah', 'ferdi', 'Operator', 'COMPLETED', NOW() - INTERVAL '2 hours', NOW() - INTERVAL '15 minutes', 6300, '01:45:00', '1 jam 45 menit 0 detik', 28.1, 66.5, 28.5, 65.0, 'WORK_CHECK_OUT'
    WHERE NOT EXISTS (SELECT 1 FROM work_attendance_sessions LIMIT 1);

    INSERT INTO iot_telemetry_logs (device_serial, temperature_c, humidity_pct, heat_index_c, wifi_rssi, is_locked, work_active, work_elapsed_sec, remaining_sec, uptime_sec, free_heap, recorded_at)
    SELECT 'ETL-ESP32-001', 28.5, 65.0, 29.8, -55, 1, 0, 0, 0, 3600, 245000, NOW()
    WHERE NOT EXISTS (SELECT 1 FROM iot_telemetry_logs LIMIT 1);
    """
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
    except Exception:
        pass


def _get_cursor(conn):
    try:
        import psycopg2.extras
        if hasattr(conn, "cursor") and not hasattr(conn, "row_factory"):
            return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception:
        pass
    return conn.cursor()


class NfcService:
    @staticmethod
    def list_users() -> List[Dict[str, Any]]:
        conn = get_eternal_pg_conn()
        try:
            _ensure_tables(conn)
            with _get_cursor(conn) as cur:
                cur.execute("""
                    SELECT id, uid, nama, username, role, pin, is_active, device_serial,
                           total_taps, COALESCE(total_work_sec, 0) AS total_work_sec,
                           COALESCE(total_sessions, 0) AS total_sessions,
                           TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                           TO_CHAR(last_login_at, 'YYYY-MM-DD HH24:MI:SS') AS last_login_at
                    FROM nfc_users
                    ORDER BY id ASC;
                """)
                return cur.fetchall() or []
        finally:
            conn.close()

    @staticmethod
    def get_user_by_uid(uid: str) -> Optional[Dict[str, Any]]:
        clean_uid = _clean_uid(uid)
        conn = get_eternal_pg_conn()
        try:
            _ensure_tables(conn)
            with _get_cursor(conn) as cur:
                cur.execute("""
                    SELECT id, uid, nama, username, role, pin, is_active, device_serial,
                           total_taps, COALESCE(total_work_sec, 0) AS total_work_sec,
                           COALESCE(total_sessions, 0) AS total_sessions,
                           TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                           TO_CHAR(last_login_at, 'YYYY-MM-DD HH24:MI:SS') AS last_login_at
                    FROM nfc_users
                    WHERE UPPER(uid) = %s
                    LIMIT 1;
                """, (clean_uid,))
                return cur.fetchone()
        finally:
            conn.close()

    @staticmethod
    def save_user(data: Dict[str, Any], push_to_hardware: bool = True) -> Dict[str, Any]:
        raw_uid = _clean_uid(data.get("uid", ""))
        if not raw_uid:
            raise ValueError("UID NFC wajib diisi")

        nama = str(data.get("nama", "")).strip() or "Pengguna NFC"
        username = str(data.get("username", "")).strip() or f"user_{raw_uid[-4:].lower()}"
        role = str(data.get("role", "Operator")).strip()
        pin = str(data.get("pin", "")).strip()
        is_active = 1 if data.get("is_active", True) in (1, True, "1", "true") else 0
        device_serial = data.get("device_serial") or data.get("serial") or "ETL-ESP32-001"
        total_work_sec = _opt_int(data.get("total_work_sec")) or 0
        total_sessions = _opt_int(data.get("total_sessions")) or 0

        conn = get_eternal_pg_conn()
        try:
            _ensure_tables(conn)
            with _get_cursor(conn) as cur:
                cur.execute("""
                    INSERT INTO nfc_users (uid, nama, username, role, pin, is_active, device_serial, total_work_sec, total_sessions, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (uid) DO UPDATE
                    SET nama = EXCLUDED.nama,
                        username = EXCLUDED.username,
                        role = EXCLUDED.role,
                        pin = EXCLUDED.pin,
                        is_active = EXCLUDED.is_active,
                        device_serial = EXCLUDED.device_serial,
                        total_work_sec = GREATEST(COALESCE(nfc_users.total_work_sec, 0), EXCLUDED.total_work_sec),
                        total_sessions = GREATEST(COALESCE(nfc_users.total_sessions, 0), EXCLUDED.total_sessions);
                """, (raw_uid, nama, username, role, pin, is_active, device_serial, total_work_sec, total_sessions))

            saved_user = NfcService.get_user_by_uid(raw_uid) or {"uid": raw_uid, "status": "saved"}

            # Auto-push ke ESP32 hardware hanya jika bukan event yang berasal dari alat itu sendiri
            if push_to_hardware:
                NfcService.push_to_device(saved_user)
            return saved_user
        finally:
            conn.close()

    @staticmethod
    def push_to_device(user: Dict[str, Any]):
        """Kirim profil user NFC langsung ke memori RAM/NVS ESP32 agar langsung aktif tanpa restart alat."""
        target_ips = ["192.168.25.104"]
        for ip in target_ips:
            try:
                payload = json.dumps({
                    "uid": user.get("uid"),
                    "nama": user.get("nama"),
                    "username": user.get("username"),
                    "role": user.get("role", "Operator"),
                    "pin": user.get("pin", ""),
                    "source": "server"
                }).encode("utf-8")
                req = urllib.request.Request(
                    f"http://{ip}/api/nfc/register_user",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Source": "server-sync"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=1.5):
                    pass
            except Exception:
                pass

    @staticmethod
    def delete_user(uid: str, push_to_hardware: bool = True) -> bool:
        clean_uid = _clean_uid(uid)
        conn = get_eternal_pg_conn()
        try:
            _ensure_tables(conn)
            with _get_cursor(conn) as cur:
                cur.execute("DELETE FROM nfc_users WHERE UPPER(uid) = %s;", (clean_uid,))
                affected = cur.rowcount

            # Hapus juga dari hardware ESP32 bila dipicu dari dashboard/API server
            if push_to_hardware:
                try:
                    payload = json.dumps({"uid": clean_uid}).encode("utf-8")
                    req = urllib.request.Request(
                        "http://192.168.25.104/api/rfid/delete",
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=1.5):
                        pass
                except Exception:
                    pass

            return affected > 0
        finally:
            conn.close()

    @staticmethod
    def list_access_logs(limit: int = 50) -> List[Dict[str, Any]]:
        conn = get_eternal_pg_conn()
        try:
            _ensure_tables(conn)
            with _get_cursor(conn) as cur:
                cur.execute("""
                    SELECT id, uid, nama, username, role, action_type, lock_duration_sec,
                           COALESCE(work_duration_sec, 0) AS work_duration_sec,
                           COALESCE(work_duration_hms, '00:00:00') AS work_duration_hms,
                           check_in_time, check_out_time,
                           device_serial, device_ip,
                           TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at
                    FROM nfc_access_logs
                    ORDER BY id DESC
                    LIMIT %s;
                """, (limit,))
                return cur.fetchall() or []
        finally:
            conn.close()

    @staticmethod
    def list_attendance_sessions(limit: int = 50, device_serial: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = get_eternal_pg_conn()
        try:
            _ensure_tables(conn)
            with _get_cursor(conn) as cur:
                if device_serial:
                    cur.execute("""
                        SELECT id, device_serial, session_id, uid, nama, username, role, status,
                               TO_CHAR(check_in_at, 'YYYY-MM-DD HH24:MI:SS') AS check_in_at,
                               TO_CHAR(check_out_at, 'YYYY-MM-DD HH24:MI:SS') AS check_out_at,
                               check_in_wib, check_out_wib,
                               COALESCE(duration_sec, 0) AS duration_sec,
                               COALESCE(duration_hms, '00:00:00') AS duration_hms,
                               duration_detail, suhu_in, humidity_in, suhu_out, humidity_out, trigger_event
                        FROM work_attendance_sessions
                        WHERE device_serial = %s
                        ORDER BY check_in_at DESC, id DESC
                        LIMIT %s;
                    """, (device_serial, limit))
                else:
                    cur.execute("""
                        SELECT id, device_serial, session_id, uid, nama, username, role, status,
                               TO_CHAR(check_in_at, 'YYYY-MM-DD HH24:MI:SS') AS check_in_at,
                               TO_CHAR(check_out_at, 'YYYY-MM-DD HH24:MI:SS') AS check_out_at,
                               check_in_wib, check_out_wib,
                               COALESCE(duration_sec, 0) AS duration_sec,
                               COALESCE(duration_hms, '00:00:00') AS duration_hms,
                               duration_detail, suhu_in, humidity_in, suhu_out, humidity_out, trigger_event
                        FROM work_attendance_sessions
                        ORDER BY check_in_at DESC, id DESC
                        LIMIT %s;
                    """, (limit,))
                return cur.fetchall() or []
        finally:
            conn.close()

    @staticmethod
    def get_latest_tap() -> Optional[Dict[str, Any]]:
        conn = get_eternal_pg_conn()
        try:
            _ensure_tables(conn)
            with _get_cursor(conn) as cur:
                cur.execute("""
                    SELECT id, uid, nama, username, role, action_type, lock_duration_sec,
                           COALESCE(work_duration_sec, 0) AS work_duration_sec,
                           COALESCE(work_duration_hms, '00:00:00') AS work_duration_hms,
                           check_in_time, check_out_time,
                           device_serial, device_ip,
                           TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at
                    FROM nfc_access_logs
                    ORDER BY id DESC
                    LIMIT 1;
                """)
                return cur.fetchone()
        finally:
            conn.close()

    @staticmethod
    def record_attendance_session(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert work attendance sessions (Check-In / Check-Out / Auto-Checkout) cleanly without duplicates."""
        raw_uid = _clean_uid(payload.get("uid") or payload.get("active_uid") or "")
        if not raw_uid:
            return {"status": "skipped", "reason": "missing_uid"}

        user = NfcService.get_user_by_uid(raw_uid)
        device_serial = str(
            payload.get("device_serial") or payload.get("serial") or payload.get("MCID") or "ETL-ESP32-001"
        )
        device_name = str(payload.get("device") or "Eternal Absensi Kerja")
        session_id = _opt_int(payload.get("session_id")) or 0
        nama = _clean_str(payload.get("nama") or payload.get("user")) or (user["nama"] if user else "Pengguna NFC")
        username = _clean_str(payload.get("username")) or (user["username"] if user else f"nfc_{raw_uid[-4:].lower()}")
        role = _clean_str(payload.get("role")) or (user["role"] if user else "Operator")

        canonical_action, kind = _classify_attendance_action(
            action=payload.get("event") or payload.get("action_type") or payload.get("action") or "",
            status=payload.get("status") or "",
            work_status=payload.get("work_status") or payload.get("lock_status") or "",
            work_active=payload.get("work_active"),
        )
        if kind == "DENIED":
            return {"status": "ignored", "reason": "denied", "uid": raw_uid, "action": canonical_action}

        check_in_wib = _clean_str(payload.get("check_in_time"))
        check_out_wib = _clean_str(payload.get("check_out_time"))
        duration_sec = (
            _opt_int(payload.get("work_duration_sec"))
            if payload.get("work_duration_sec") is not None
            else (_opt_int(payload.get("duration_sec")) if payload.get("duration_sec") is not None else _opt_int(payload.get("last_work_duration_sec")))
        ) or 0
        duration_hms = _clean_str(payload.get("work_duration_hms") or payload.get("last_work_duration_str")) or _format_hms(duration_sec)
        duration_detail = _clean_str(payload.get("work_duration_detail"))

        suhu_in = _opt_float(payload.get("suhu_in"))
        humidity_in = _opt_float(payload.get("humidity_in"))
        suhu_out = _opt_float(payload.get("suhu_out"))
        humidity_out = _opt_float(payload.get("humidity_out"))
        curr_suhu = _opt_float(payload.get("suhu") or payload.get("temperature"))
        curr_hum = _opt_float(payload.get("humidity"))

        if kind == "CHECK_IN":
            if suhu_in is None:
                suhu_in = curr_suhu
            if humidity_in is None:
                humidity_in = curr_hum
        else:
            if suhu_out is None:
                suhu_out = curr_suhu
            if humidity_out is None:
                humidity_out = curr_hum

        user_total_work_sec = _opt_int(payload.get("user_total_work_sec"))
        user_total_sessions = _opt_int(payload.get("user_total_sessions"))
        total_work_sec = _opt_int(payload.get("total_work_sec")) or 0
        total_work_sessions = _opt_int(payload.get("total_work_sessions")) or 0

        conn = get_eternal_pg_conn()
        try:
            _ensure_tables(conn)
            with _get_cursor(conn) as cur:
                if kind == "CHECK_IN":
                    cur.execute("""
                        SELECT id FROM work_attendance_sessions
                        WHERE device_serial = %s AND UPPER(uid) = %s AND status = 'WORKING'
                          AND ((%s IS NOT NULL AND check_in_wib = %s) OR check_in_at >= NOW() - INTERVAL '30 seconds')
                        ORDER BY id DESC LIMIT 1;
                    """, (device_serial, raw_uid, check_in_wib, check_in_wib))
                    existing = cur.fetchone()

                    if existing:
                        cur.execute("""
                            UPDATE work_attendance_sessions
                            SET session_id = CASE WHEN %s > 0 THEN %s ELSE session_id END,
                                nama = %s,
                                username = %s,
                                role = %s,
                                check_in_wib = COALESCE(%s, check_in_wib),
                                suhu_in = COALESCE(%s, suhu_in),
                                humidity_in = COALESCE(%s, humidity_in),
                                trigger_event = %s,
                                updated_at = NOW()
                            WHERE id = %s;
                        """, (session_id, session_id, nama, username, role, check_in_wib, suhu_in, humidity_in, canonical_action, existing["id"]))
                    else:
                        # Auto-close stale WORKING sessions before opening a new one
                        cur.execute("""
                            UPDATE work_attendance_sessions
                            SET status = 'COMPLETED',
                                check_out_at = COALESCE(check_out_at, NOW()),
                                duration_sec = CASE
                                    WHEN COALESCE(duration_sec, 0) > 0 THEN duration_sec
                                    ELSE GREATEST(0, EXTRACT(EPOCH FROM (NOW() - check_in_at))::int)
                                END,
                                updated_at = NOW()
                            WHERE device_serial = %s AND status = 'WORKING';
                        """, (device_serial,))

                        cur.execute("""
                            INSERT INTO work_attendance_sessions
                                (device_serial, session_id, uid, nama, username, role, status,
                                 check_in_at, check_in_wib, duration_sec, duration_hms,
                                 suhu_in, humidity_in, trigger_event, created_at, updated_at)
                            VALUES
                                (%s, %s, %s, %s, %s, %s, 'WORKING', NOW(), %s, 0, '00:00:00', %s, %s, %s, NOW(), NOW());
                        """, (device_serial, session_id, raw_uid, nama, username, role, check_in_wib, suhu_in, humidity_in, canonical_action))

                    cur.execute("""
                        INSERT INTO device_locks
                            (device_serial, device_name, is_locked, lock_status, work_status, work_elapsed_sec,
                             check_in_time, unlocked_by_uid, unlocked_by_name, unlocked_by_username, unlocked_by_role,
                             unlock_duration_sec, unlocked_at, total_work_sec, total_work_sessions, last_toggle_action, updated_at)
                        VALUES
                            (%s, %s, 0, 'WORKING', 'WORKING', 0, %s, %s, %s, %s, %s, 0, NOW(), %s, %s, %s, NOW())
                        ON CONFLICT (device_serial) DO UPDATE
                        SET is_locked = 0,
                            lock_status = 'WORKING',
                            work_status = 'WORKING',
                            work_elapsed_sec = 0,
                            check_in_time = COALESCE(EXCLUDED.check_in_time, device_locks.check_in_time),
                            unlocked_by_uid = EXCLUDED.unlocked_by_uid,
                            unlocked_by_name = EXCLUDED.unlocked_by_name,
                            unlocked_by_username = EXCLUDED.unlocked_by_username,
                            unlocked_by_role = EXCLUDED.unlocked_by_role,
                            unlocked_at = NOW(),
                            total_work_sec = GREATEST(COALESCE(device_locks.total_work_sec, 0), EXCLUDED.total_work_sec),
                            total_work_sessions = GREATEST(COALESCE(device_locks.total_work_sessions, 0), EXCLUDED.total_work_sessions),
                            last_toggle_action = EXCLUDED.last_toggle_action,
                            updated_at = NOW();
                    """, (device_serial, device_name, check_in_wib, raw_uid, nama, username, role, total_work_sec, total_work_sessions, canonical_action))
                else:
                    cur.execute("""
                        SELECT id, status, COALESCE(duration_sec, 0) AS duration_sec
                        FROM work_attendance_sessions
                        WHERE device_serial = %s AND UPPER(uid) = %s
                          AND (status = 'WORKING'
                               OR (%s IS NOT NULL AND check_in_wib = %s)
                               OR check_out_at >= NOW() - INTERVAL '30 seconds')
                        ORDER BY CASE WHEN status = 'WORKING' THEN 0 ELSE 1 END, id DESC
                        LIMIT 1;
                    """, (device_serial, raw_uid, check_in_wib, check_in_wib))
                    existing = cur.fetchone()
                    was_working = bool(existing and existing.get("status") == "WORKING")

                    if existing:
                        cur.execute("""
                            UPDATE work_attendance_sessions
                            SET session_id = CASE WHEN %s > 0 THEN %s ELSE session_id END,
                                status = 'COMPLETED',
                                check_in_wib = COALESCE(check_in_wib, %s),
                                check_out_at = COALESCE(check_out_at, NOW()),
                                check_out_wib = COALESCE(%s, check_out_wib),
                                duration_sec = GREATEST(COALESCE(duration_sec, 0), %s),
                                duration_hms = CASE WHEN %s <> '00:00:00' THEN %s ELSE duration_hms END,
                                duration_detail = COALESCE(%s, duration_detail),
                                suhu_in = COALESCE(suhu_in, %s),
                                humidity_in = COALESCE(humidity_in, %s),
                                suhu_out = COALESCE(%s, suhu_out),
                                humidity_out = COALESCE(%s, humidity_out),
                                trigger_event = %s,
                                updated_at = NOW()
                            WHERE id = %s;
                        """, (
                            session_id, session_id, check_in_wib, check_out_wib,
                            duration_sec, duration_hms, duration_hms, duration_detail,
                            suhu_in, humidity_in, suhu_out, humidity_out, canonical_action, existing["id"],
                        ))
                    else:
                        was_working = True
                        cur.execute("""
                            INSERT INTO work_attendance_sessions
                                (device_serial, session_id, uid, nama, username, role, status,
                                 check_in_at, check_out_at, check_in_wib, check_out_wib,
                                 duration_sec, duration_hms, duration_detail,
                                 suhu_in, humidity_in, suhu_out, humidity_out, trigger_event, created_at, updated_at)
                            VALUES
                                (%s, %s, %s, %s, %s, %s, 'COMPLETED',
                                 NOW() - (INTERVAL '1 second' * %s), NOW(), %s, %s,
                                 %s, %s, %s,
                                 %s, %s, %s, %s, %s, NOW(), NOW());
                        """, (
                            device_serial, session_id, raw_uid, nama, username, role,
                            duration_sec, check_in_wib, check_out_wib,
                            duration_sec, duration_hms, duration_detail,
                            suhu_in, humidity_in, suhu_out, humidity_out, canonical_action,
                        ))

                    if user:
                        if user_total_work_sec is not None or user_total_sessions is not None:
                            cur.execute("""
                                UPDATE nfc_users
                                SET total_work_sec = GREATEST(COALESCE(total_work_sec, 0), %s),
                                    total_sessions = GREATEST(COALESCE(total_sessions, 0), %s),
                                    last_login_at = NOW()
                                WHERE UPPER(uid) = %s;
                            """, (user_total_work_sec or 0, user_total_sessions or 0, raw_uid))
                        elif was_working:
                            cur.execute("""
                                UPDATE nfc_users
                                SET total_work_sec = COALESCE(total_work_sec, 0) + %s,
                                    total_sessions = COALESCE(total_sessions, 0) + 1,
                                    last_login_at = NOW()
                                WHERE UPPER(uid) = %s;
                            """, (duration_sec, raw_uid))

                    cur.execute("""
                        INSERT INTO device_locks
                            (device_serial, device_name, is_locked, lock_status, work_status, work_elapsed_sec,
                             last_work_duration_sec, last_work_duration_str, total_work_sec, total_work_sessions,
                             check_in_time, unlocked_by_uid, unlocked_by_name, unlocked_by_username, unlocked_by_role,
                             last_toggle_action, updated_at)
                        VALUES
                            (%s, %s, 1, 'STANDBY', 'STANDBY', 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (device_serial) DO UPDATE
                        SET is_locked = 1,
                            lock_status = 'STANDBY',
                            work_status = 'STANDBY',
                            work_elapsed_sec = 0,
                            last_work_duration_sec = CASE WHEN EXCLUDED.last_work_duration_sec > 0 THEN EXCLUDED.last_work_duration_sec ELSE device_locks.last_work_duration_sec END,
                            last_work_duration_str = CASE WHEN EXCLUDED.last_work_duration_str <> '00:00:00' THEN EXCLUDED.last_work_duration_str ELSE device_locks.last_work_duration_str END,
                            total_work_sec = GREATEST(COALESCE(device_locks.total_work_sec, 0), EXCLUDED.total_work_sec),
                            total_work_sessions = GREATEST(COALESCE(device_locks.total_work_sessions, 0), EXCLUDED.total_work_sessions),
                            last_toggle_action = EXCLUDED.last_toggle_action,
                            updated_at = NOW();
                    """, (
                        device_serial, device_name, duration_sec, duration_hms,
                        total_work_sec, total_work_sessions, check_in_wib,
                        raw_uid, nama, username, role, canonical_action,
                    ))

            return {
                "status": "success",
                "uid": raw_uid,
                "nama": nama,
                "role": role,
                "work_status": "WORKING" if kind == "CHECK_IN" else "COMPLETED",
                "action": canonical_action,
                "duration_sec": duration_sec,
                "duration_hms": duration_hms,
                "is_locked": kind != "CHECK_IN",
            }
        finally:
            conn.close()

    @staticmethod
    def record_tap_event(payload: Dict[str, Any]) -> Dict[str, Any]:
        raw_uid = _clean_uid(payload.get("uid", ""))
        if not raw_uid:
            raise ValueError("UID kartu wajib ada.")

        user = NfcService.get_user_by_uid(raw_uid)
        nama = _clean_str(payload.get("nama") or payload.get("user")) or (user["nama"] if user else "Tamu / Belum Terdaftar")
        username = _clean_str(payload.get("username")) or (user["username"] if user else f"nfc_{raw_uid[-4:].lower()}")
        role = _clean_str(payload.get("role")) or (user["role"] if user else "GUEST")

        canonical_action, kind = _classify_attendance_action(
            action=payload.get("action_type") or payload.get("action") or payload.get("event") or "",
            status=payload.get("status") or "",
            work_status=payload.get("work_status") or payload.get("lock_status") or "",
            work_active=payload.get("work_active"),
        )
        device_serial = str(
            payload.get("device_serial") or payload.get("serial") or payload.get("MCID") or "ETL-ESP32-001"
        )
        device_ip = str(payload.get("device_ip") or payload.get("ip") or "0.0.0.0")

        work_sec = (
            _opt_int(payload.get("work_duration_sec"))
            if payload.get("work_duration_sec") is not None
            else (_opt_int(payload.get("last_work_duration_sec")) if payload.get("last_work_duration_sec") is not None else _opt_int(payload.get("work_elapsed_sec")))
        ) or 0
        work_hms = _clean_str(payload.get("work_duration_hms") or payload.get("last_work_duration_str")) or _format_hms(work_sec)
        check_in_wib = _clean_str(payload.get("check_in_time"))
        check_out_wib = _clean_str(payload.get("check_out_time"))
        lock_sec = _opt_int(payload.get("lock_duration_sec")) or _opt_int(payload.get("remaining_sec")) or work_sec

        is_denied = kind == "DENIED"
        is_working = kind == "CHECK_IN"
        group_key = "DENIED" if is_denied else ("CHECK_IN" if is_working else "CHECK_OUT")

        conn = get_eternal_pg_conn()
        try:
            _ensure_tables(conn)
            with _get_cursor(conn) as cur:
                # 1. Deduplicate simultaneous MQTT (/Eternal/rfid, /Eternal/attendance) + HTTP (/api/nfc/access)
                cur.execute("""
                    SELECT id, action_type FROM nfc_access_logs
                    WHERE device_serial = %s AND UPPER(uid) = %s
                      AND created_at >= NOW() - INTERVAL '10 seconds'
                    ORDER BY id DESC LIMIT 1;
                """, (device_serial, raw_uid))
                recent = cur.fetchone()
                recent_group = None
                if recent:
                    _, rk = _classify_attendance_action(action=recent.get("action_type"))
                    recent_group = "DENIED" if rk == "DENIED" else ("CHECK_IN" if rk == "CHECK_IN" else "CHECK_OUT")

                if recent and recent_group == group_key:
                    prev_act = str(recent.get("action_type") or "")
                    better_action = canonical_action if (
                        "AUTO_CHECKOUT" in canonical_action
                        or "REMOTE" in canonical_action
                        or prev_act in ("", "NFC_TAP", "TAP_ON")
                    ) else prev_act
                    cur.execute("""
                        UPDATE nfc_access_logs
                        SET action_type = %s,
                            work_duration_sec = GREATEST(COALESCE(work_duration_sec, 0), %s),
                            work_duration_hms = CASE WHEN %s <> '00:00:00' THEN %s ELSE work_duration_hms END,
                            check_in_time = COALESCE(%s, check_in_time),
                            check_out_time = COALESCE(%s, check_out_time)
                        WHERE id = %s;
                    """, (better_action, work_sec, work_hms, work_hms, check_in_wib, check_out_wib, recent["id"]))
                else:
                    cur.execute("""
                        INSERT INTO nfc_access_logs
                            (uid, nama, username, role, action_type, lock_duration_sec,
                             work_duration_sec, work_duration_hms, check_in_time, check_out_time,
                             device_serial, device_ip, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW());
                    """, (
                        raw_uid, nama, username, role, canonical_action, lock_sec,
                        work_sec, work_hms, check_in_wib, check_out_wib, device_serial, device_ip,
                    ))

                    if user:
                        cur.execute("""
                            UPDATE nfc_users
                            SET total_taps = COALESCE(total_taps, 0) + 1,
                                last_login_at = NOW()
                            WHERE UPPER(uid) = %s;
                        """, (raw_uid,))
        finally:
            conn.close()

        # 2. Sync work_attendance_sessions, device_locks & user work-hour totals
        if not is_denied:
            NfcService.record_attendance_session(payload)

        return {
            "status": "success",
            "uid": raw_uid,
            "nama": nama,
            "role": role,
            "action": canonical_action,
            "work_status": "DENIED" if is_denied else ("WORKING" if is_working else "COMPLETED"),
            "is_locked": not is_working,
            "database": "postgresql"
        }
