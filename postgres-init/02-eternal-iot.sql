-- =============================================================================
-- IoT Eternal Database Schema & Tables Initialization
-- Executed automatically on startup when connected to PostgreSQL
-- =============================================================================

\connect eternal_iot;

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

-- Idempotent schema upgrades for existing databases
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

-- =============================================================================
-- Seed Initial Snapshot & History for ETL-ESP32-001 (Prevents Grafana "No data")
-- =============================================================================
INSERT INTO iot_devices (device_serial, device_name, model, ip_address, temperature_c, humidity_pct, heat_index_c, wifi_rssi, rssi, status, uptime_sec, free_heap, firmware_version, last_seen_at, updated_at)
VALUES ('ETL-ESP32-001', 'Eternal Absensi Kerja', 'Eternal-ESP32', '192.168.25.104', 28.5, 65.0, 29.8, -55, -55, 'online', 3600, 245000, '3.2.0', NOW(), NOW())
ON CONFLICT (device_serial) DO UPDATE SET
    last_seen_at = NOW(),
    updated_at = NOW();

INSERT INTO device_locks (device_serial, device_name, is_locked, lock_status, work_status, work_elapsed_sec, last_work_duration_sec, last_work_duration_str, total_work_sec, total_work_sessions, unlocked_by_uid, unlocked_by_name, unlocked_by_username, unlocked_by_role, unlock_duration_sec, last_toggle_action, updated_at)
VALUES ('ETL-ESP32-001', 'Eternal Absensi Kerja', 1, 'STANDBY', 'STANDBY', 0, 0, '00:00:00', 0, 0, NULL, NULL, NULL, NULL, 0, 'WORK_CHECK_OUT', NOW())
ON CONFLICT (device_serial) DO UPDATE SET
    updated_at = NOW();

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
SELECT 'ETL-ESP32-001', 28.1, 66.5, 29.2, -58, 1, 0, 0, 0, 3300, 246000, NOW() - INTERVAL '30 minutes'
WHERE NOT EXISTS (SELECT 1 FROM iot_telemetry_logs LIMIT 1);

INSERT INTO iot_telemetry_logs (device_serial, temperature_c, humidity_pct, heat_index_c, wifi_rssi, is_locked, work_active, work_elapsed_sec, remaining_sec, uptime_sec, free_heap, recorded_at)
SELECT 'ETL-ESP32-001', 28.5, 65.0, 29.8, -55, 1, 0, 0, 0, 3600, 245000, NOW()
WHERE NOT EXISTS (SELECT 1 FROM iot_telemetry_logs WHERE recorded_at >= NOW() - INTERVAL '1 minute');

-- Grant fadb user full control
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO fadb;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO fadb;
