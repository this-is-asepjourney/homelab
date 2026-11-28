#!/usr/bin/env python3
"""Generate the single PostgreSQL-based Eternal IoT Work Attendance dashboard.
Usage: python grafana/gen_eternal_dashboard.py
"""
import json
from pathlib import Path

DS = {"type": "postgres", "uid": "postgres-iot-db"}
S = "'$serial'"
panels = []
_id = [0]


def nid():
    _id[0] += 1
    return _id[0]


def tgt(sql, fmt="table"):
    return [{
        "datasource": DS,
        "editorMode": "code",
        "format": fmt,
        "rawQuery": True,
        "rawSql": sql,
        "refId": "A",
    }]


def row(title, y):
    panels.append({
        "id": nid(),
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
    })


def steps(*pairs):
    return {
        "mode": "absolute",
        "steps": [{"color": c, "value": v} for c, v in pairs],
    }


def vmap(d):
    return [{
        "type": "value",
        "options": {
            str(k): {"text": t, "color": c, "index": i}
            for i, (k, (t, c)) in enumerate(d.items())
        },
    }]


def hms_sql(expr):
    """Format integer seconds as HH:MM:SS cleanly even past 24 hours."""
    return (
        f"LPAD((({expr}) / 3600)::text, 2, '0') || ':' || "
        f"LPAD(((({expr}) % 3600) / 60)::text, 2, '0') || ':' || "
        f"LPAD((({expr}) % 60)::text, 2, '0')"
    )


def stat(
    title, sql, x, y, w, h=4, unit="none", th=None, maps=None, dec=None,
    color_mode="background", text_mode="auto", desc="",
):
    d = {
        "unit": unit,
        "color": {"mode": "thresholds"},
        "thresholds": th or steps(("blue", None)),
        "mappings": maps or [],
        "noValue": "–",
    }
    if dec is not None:
        d["decimals"] = dec
    panels.append({
        "id": nid(),
        "type": "stat",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": tgt(sql),
        "fieldConfig": {"defaults": d, "overrides": []},
        "options": {
            "colorMode": color_mode,
            "graphMode": "none",
            "justifyMode": "center",
            "textMode": text_mode,
            "orientation": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    })


def gauge(title, sql, x, y, w, h, unit, mn, mx, th, dec=1):
    panels.append({
        "id": nid(),
        "type": "gauge",
        "title": title,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": tgt(sql),
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "min": mn,
                "max": mx,
                "decimals": dec,
                "color": {"mode": "thresholds"},
                "thresholds": th,
                "noValue": "–",
            },
            "overrides": [],
        },
        "options": {
            "showThresholdMarkers": True,
            "showThresholdLabels": False,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    })


def ts(
    title, sql, x, y, w, h, unit="none", colors=None, bars=False, stack=False,
    mn=None, mx=None, desc="",
):
    d = {
        "unit": unit,
        "color": {"mode": "palette-classic"},
        "noValue": "Belum ada data",
        "custom": {
            "drawStyle": "bars" if bars else "line",
            "lineInterpolation": "smooth",
            "lineWidth": 2,
            "fillOpacity": 75 if bars else 18,
            "gradientMode": "opacity",
            "showPoints": "never",
            "spanNulls": True,
            "stacking": {"mode": "normal" if stack else "none", "group": "A"},
        },
    }
    if mn is not None:
        d["min"] = mn
    if mx is not None:
        d["max"] = mx
    ov = [
        {
            "matcher": {"id": "byName", "options": n},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}],
        }
        for n, c in (colors or {}).items()
    ]
    panels.append({
        "id": nid(),
        "type": "timeseries",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": tgt(sql, "time_series"),
        "fieldConfig": {"defaults": d, "overrides": ov},
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "none"},
        },
    })


def table(title, sql, x, y, w, h, overrides=None, desc=""):
    panels.append({
        "id": nid(),
        "type": "table",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": tgt(sql),
        "fieldConfig": {
            "defaults": {
                "custom": {"align": "left", "cellOptions": {"type": "auto"}},
                "noValue": "Belum ada data",
            },
            "overrides": overrides or [],
        },
        "options": {"showHeader": True, "cellHeight": "sm"},
    })


LAST = f"FROM iot_devices WHERE device_serial = {S} LIMIT 1"
LOCK_ROW = f"FROM device_locks WHERE device_serial = {S} LIMIT 1"
SESS_WHERE = f"FROM work_attendance_sessions WHERE device_serial = {S} AND $__timeFilter(check_in_at)"
LOG_WHERE = f"FROM nfc_access_logs WHERE device_serial = {S} AND $__timeFilter(created_at)"
CHECKIN_SQL = (
    "(action_type ILIKE '%CHECK_IN%' OR action_type ILIKE '%CHECKIN%' "
    "OR action_type ILIKE '%UNLOCK%' OR action_type ILIKE '%TAP_ON%')"
)

# ---------------------------------------------------------------- Row 1: status perangkat & pekerja aktif
row("Status Perangkat & Absensi Aktif", 0)
stat(
    "Perangkat",
    f"SELECT CASE WHEN last_seen_at >= NOW() - INTERVAL '90 seconds' THEN 1 ELSE 0 END AS v {LAST}",
    0, 1, 3,
    maps=vmap({1: ("ONLINE", "green"), 0: ("OFFLINE", "red")}),
    th=steps(("red", None), ("green", 1)),
    desc="Online bila ada paket MQTT dari ESP32 dalam 90 detik terakhir.",
)
stat(
    "Status Kerja",
    f"SELECT is_locked AS v {LOCK_ROW}",
    3, 1, 4,
    maps=vmap({0: ("SEDANG BEKERJA", "green"), 1: ("STANDBY", "blue")}),
    th=steps(("green", None), ("blue", 1)),
    desc="Status mesin absensi saat ini (WORKING saat tap masuk, STANDBY saat tap pulang).",
)
stat(
    "Pekerja Aktif",
    f"SELECT CASE WHEN is_locked = 0 THEN COALESCE(NULLIF(unlocked_by_name,''), 'Pekerja') ELSE 'Standby' END AS v {LOCK_ROW}",
    7, 1, 5,
    text_mode="value",
    color_mode="value",
    th=steps(("green", None)),
    desc="Nama pekerja yang sedang check-in dan aktif bekerja.",
)
stat(
    "Durasi Kerja Berjalan",
    f"SELECT CASE WHEN is_locked = 0 THEN GREATEST(COALESCE(work_elapsed_sec, 0), COALESCE(EXTRACT(EPOCH FROM (NOW() - unlocked_at))::int, 0)) ELSE 0 END AS v {LOCK_ROW}",
    12, 1, 4,
    unit="dtdurations",
    th=steps(("green", None)),
    color_mode="value",
    maps=[{"type": "value", "options": {"0": {"text": "–", "index": 0}}}],
    desc="Stopwatch durasi sesi kerja yang sedang berlangsung.",
)
stat(
    "Sinyal WiFi",
    f"SELECT wifi_rssi AS v {LAST}",
    16, 1, 3,
    unit="dBm",
    dec=0,
    th=steps(("red", None), ("orange", -80), ("yellow", -70), ("green", -60)),
)
stat(
    "Uptime",
    f"SELECT uptime_sec AS v {LAST}",
    19, 1, 3,
    unit="dtdurations",
    dec=0,
    th=steps(("purple", None)),
    color_mode="value",
)
stat(
    "Free Heap",
    f"SELECT free_heap AS v {LAST}",
    22, 1, 2,
    unit="bytes",
    dec=0,
    th=steps(("red", None), ("orange", 80000), ("green", 150000)),
)

# ---------------------------------------------------------------- Row 2: absensi kerja & jam kerja
row("Absensi Kerja & Rekap Jam Kerja", 5)
stat(
    "Check-In (Masuk)",
    f"SELECT COUNT(*) AS v {SESS_WHERE}",
    0, 6, 5,
    th=steps(("green", None)),
    dec=0,
    desc="Jumlah sesi check-in pekerja pada rentang waktu yang dipilih.",
)
stat(
    "Sesi Selesai",
    f"SELECT COUNT(*) AS v {SESS_WHERE} AND status = 'COMPLETED'",
    5, 6, 5,
    th=steps(("blue", None)),
    dec=0,
    desc="Jumlah sesi kerja yang sudah check-out / selesai pada periode terpilih.",
)
stat(
    "Total Jam Kerja",
    "SELECT COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN COALESCE(duration_sec, 0) "
    "ELSE GREATEST(COALESCE(duration_sec, 0), GREATEST(0, EXTRACT(EPOCH FROM (NOW() - check_in_at))::int)) END), 0) AS v "
    f"{SESS_WHERE}",
    10, 6, 5,
    unit="dtdurations",
    th=steps(("purple", None)),
    desc="Akumulasi total durasi kerja seluruh pekerja pada rentang waktu terpilih.",
)
stat(
    "Tap Ditolak",
    f"SELECT COUNT(*) AS v {LOG_WHERE} AND action_type ILIKE '%DENIED%'",
    15, 6, 5,
    th=steps(("green", None), ("red", 1)),
    dec=0,
    desc="Kartu NFC tidak terdaftar yang mencoba tap pada mesin absensi.",
)
stat(
    "Pekerja Terdaftar",
    "SELECT COUNT(*) AS v FROM nfc_users WHERE is_active = 1",
    20, 6, 4,
    th=steps(("blue", None)),
    dec=0,
    desc="Jumlah pekerja aktif pada whitelist NFC.",
)

ts(
    "Aktivitas Absensi per Jam",
    "SELECT $__timeGroupAlias(created_at,'1h'), "
    f"SUM(CASE WHEN action_type NOT ILIKE '%DENIED%' AND {CHECKIN_SQL} THEN 1 ELSE 0 END) AS \"Check-In\", "
    f"SUM(CASE WHEN action_type NOT ILIKE '%DENIED%' AND NOT {CHECKIN_SQL} THEN 1 ELSE 0 END) AS \"Check-Out\", "
    "SUM(CASE WHEN action_type ILIKE '%DENIED%' THEN 1 ELSE 0 END) AS \"Ditolak\" "
    f"{LOG_WHERE} GROUP BY 1 ORDER BY 1",
    0, 10, 12, 8,
    bars=True,
    stack=True,
    colors={"Check-In": "green", "Check-Out": "blue", "Ditolak": "red"},
    desc="Distribusi tap Check-In, Check-Out, dan Ditolak per jam.",
)

LIVE_DUR_EXPR = (
    "CASE WHEN status = 'COMPLETED' THEN COALESCE(duration_sec, 0) "
    "ELSE GREATEST(COALESCE(duration_sec, 0), GREATEST(0, EXTRACT(EPOCH FROM (NOW() - check_in_at))::int)) END"
)
table(
    "Rekap Jam Kerja per Pekerja",
    "WITH periode AS ("
    f"  SELECT UPPER(uid) AS uid_key, COUNT(*) AS p_sess, COALESCE(SUM({LIVE_DUR_EXPR}), 0)::bigint AS p_sec "
    f"  FROM work_attendance_sessions WHERE device_serial = {S} AND $__timeFilter(check_in_at) "
    "  GROUP BY UPPER(uid)"
    "), totals AS ("
    f"  SELECT UPPER(uid) AS uid_key, COUNT(*) AS a_sess, COALESCE(SUM({LIVE_DUR_EXPR}), 0)::bigint AS a_sec "
    f"  FROM work_attendance_sessions WHERE device_serial = {S} "
    "  GROUP BY UPPER(uid)"
    ") "
    "SELECT u.nama AS \"Nama\", u.role AS \"Role\", "
    "COALESCE(p.p_sess, 0) AS \"Sesi (Periode)\", "
    f"{hms_sql('COALESCE(p.p_sec, 0)')} AS \"Durasi (Periode)\", "
    "GREATEST(COALESCE(u.total_sessions, 0), COALESCE(t.a_sess, 0)) AS \"Total Sesi\", "
    f"{hms_sql('GREATEST(COALESCE(u.total_work_sec, 0), COALESCE(t.a_sec, 0))')} AS \"Total Jam Kerja\", "
    "COALESCE(TO_CHAR(u.last_login_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI'), '–') AS \"Terakhir Absen\" "
    "FROM nfc_users u "
    "LEFT JOIN periode p ON UPPER(u.uid) = p.uid_key "
    "LEFT JOIN totals t ON UPPER(u.uid) = t.uid_key "
    "WHERE u.is_active = 1 "
    "ORDER BY COALESCE(p.p_sec, 0) DESC, GREATEST(COALESCE(u.total_work_sec, 0), COALESCE(t.a_sec, 0)) DESC, u.id ASC",
    12, 10, 12, 8,
    desc="Ringkasan total sesi & akumulasi jam kerja per pekerja.",
)

sess_overrides = [
    {
        "matcher": {"id": "byName", "options": "Status"},
        "properties": [
            {"id": "mappings", "value": vmap({
                "WORKING": ("SEDANG BEKERJA", "green"),
                "COMPLETED": ("SELESAI", "blue"),
            })},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "custom.width", "value": 140},
        ],
    },
    {"matcher": {"id": "byName", "options": "Role"}, "properties": [{"id": "custom.width", "value": 100}]},
    {"matcher": {"id": "byName", "options": "Durasi"}, "properties": [{"id": "custom.width", "value": 95}]},
]
table(
    "Rekap Sesi Absensi Kerja",
    "SELECT COALESCE(NULLIF(nama,'-'), 'Pengguna NFC') AS \"Nama\", "
    "role AS \"Role\", "
    "status AS \"Status\", "
    "COALESCE(check_in_wib, TO_CHAR(check_in_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS')) AS \"Masuk\", "
    "COALESCE(check_out_wib, TO_CHAR(check_out_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS'), '–') AS \"Pulang\", "
    f"CASE WHEN status = 'WORKING' THEN {hms_sql('GREATEST(0, EXTRACT(EPOCH FROM (NOW() - check_in_at))::int)')} "
    f"ELSE COALESCE(NULLIF(duration_hms, '00:00:00'), {hms_sql('COALESCE(duration_sec, 0)')}) END AS \"Durasi\", "
    "ROUND(suhu_in::numeric, 1) AS \"Suhu Masuk (°C)\", "
    "ROUND(suhu_out::numeric, 1) AS \"Suhu Pulang (°C)\" "
    f"{SESS_WHERE} ORDER BY check_in_at DESC, id DESC LIMIT 50",
    0, 18, 15, 9,
    overrides=sess_overrides,
    desc="Detail sesi check-in dan check-out pekerja dari ESP32.",
)

act_overrides = [
    {
        "matcher": {"id": "byName", "options": "Aksi"},
        "properties": [
            {"id": "mappings", "value": vmap({
                "MASUK": ("MASUK", "green"),
                "PULANG": ("PULANG", "blue"),
                "AUTO-OUT": ("AUTO-OUT", "orange"),
                "DITOLAK": ("DITOLAK", "red"),
            })},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "custom.width", "value": 105},
        ],
    },
    {"matcher": {"id": "byName", "options": "Durasi"}, "properties": [{"id": "custom.width", "value": 90}]},
]
table(
    "Log Tap NFC Terbaru",
    "SELECT TO_CHAR(created_at AT TIME ZONE 'Asia/Jakarta', 'MM-DD HH24:MI:SS') AS \"Waktu\", "
    "COALESCE(NULLIF(nama,'-'), 'Tidak dikenal') AS \"Nama\", "
    "role AS \"Role\", "
    "CASE WHEN action_type ILIKE '%DENIED%' THEN 'DITOLAK' "
    "WHEN action_type ILIKE '%AUTO_CHECKOUT%' OR action_type ILIKE '%AUTO_LOCK%' THEN 'AUTO-OUT' "
    f"WHEN {CHECKIN_SQL} THEN 'MASUK' "
    "ELSE 'PULANG' END AS \"Aksi\", "
    f"CASE WHEN COALESCE(work_duration_sec, 0) > 0 THEN COALESCE(NULLIF(work_duration_hms,'00:00:00'), {hms_sql('work_duration_sec')}) ELSE '–' END AS \"Durasi\" "
    f"{LOG_WHERE} ORDER BY created_at DESC LIMIT 50",
    15, 18, 9, 9,
    overrides=act_overrides,
)

# ---------------------------------------------------------------- Row 3: suhu & kelembapan ruangan kerja
row("Suhu & Kelembapan Ruangan Kerja (DHT11)", 27)
gauge(
    "Suhu Udara", f"SELECT temperature_c AS v {LAST}", 0, 28, 6, 6, "celsius", 15, 45,
    steps(("blue", None), ("green", 22), ("orange", 31), ("red", 36)),
)
gauge(
    "Kelembapan", f"SELECT humidity_pct AS v {LAST}", 6, 28, 6, 6, "humidity", 0, 100,
    steps(("orange", None), ("green", 40), ("blue", 70), ("purple", 85)), dec=0,
)
gauge(
    "Heat Index", f"SELECT heat_index_c AS v {LAST}", 12, 28, 6, 6, "celsius", 15, 55,
    steps(("green", None), ("yellow", 32), ("orange", 38), ("red", 45)),
)
stat(
    "Kualitas Udara", f"SELECT air_quality AS v {LAST}", 18, 28, 6, 6,
    th=steps(("green", None)), text_mode="value", color_mode="value",
    desc="Dikirim langsung oleh firmware ESP32 (Sejuk / Nyaman / Hangat / ...).",
)
ts(
    "Tren Suhu & Heat Index",
    "SELECT $__timeGroupAlias(recorded_at,'1m'), "
    'AVG(temperature_c) AS "Suhu", AVG(heat_index_c) AS "Heat Index" '
    f"FROM iot_telemetry_logs WHERE $__timeFilter(recorded_at) AND device_serial = {S} "
    "AND temperature_c > 0 GROUP BY 1 ORDER BY 1",
    0, 34, 12, 8, unit="celsius", colors={"Suhu": "orange", "Heat Index": "red"},
)
ts(
    "Tren Kelembapan",
    "SELECT $__timeGroupAlias(recorded_at,'1m'), "
    'AVG(humidity_pct) AS "Kelembapan" '
    f"FROM iot_telemetry_logs WHERE $__timeFilter(recorded_at) AND device_serial = {S} "
    "AND humidity_pct > 0 GROUP BY 1 ORDER BY 1",
    12, 34, 12, 8, unit="humidity", colors={"Kelembapan": "blue"}, mn=0, mx=100,
)

# ---------------------------------------------------------------- Row 4: informasi perangkat
row("Informasi Perangkat", 42)
table(
    "Perangkat Terdaftar",
    "SELECT device_name AS \"Nama\", device_serial AS \"Serial\", model AS \"Model\", "
    "ip_address AS \"IP\", mac_address AS \"MAC\", ssid AS \"WiFi\", "
    "firmware_version AS \"Firmware\", "
    "TO_CHAR(last_seen_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') AS \"Terakhir Terlihat\" "
    "FROM iot_devices ORDER BY device_serial",
    0, 43, 24, 5,
)

dash = {
    "uid": "eternal-iot-main",
    "title": "Eternal IoT — Absensi Kerja & Sensor",
    "description": "ESP32 Absensi Kerja (v3.2.0) -> MQTT -> PAP Worker -> PostgreSQL eternal_iot -> Grafana",
    "tags": ["eternal", "iot", "absensi", "esp32", "postgresql"],
    "timezone": "Asia/Jakarta",
    "schemaVersion": 39,
    "version": 1,
    "editable": True,
    "graphTooltip": 1,
    "refresh": "10s",
    "time": {"from": "now-24h", "to": "now"},
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
    "templating": {
        "list": [{
            "name": "serial",
            "label": "Perangkat",
            "type": "query",
            "datasource": DS,
            "query": "SELECT device_serial FROM iot_devices ORDER BY 1",
            "refresh": 1,
            "includeAll": False,
            "multi": False,
            "sort": 1,
            "current": {"text": "ETL-ESP32-001", "value": "ETL-ESP32-001"},
        }],
    },
    "annotations": {"list": []},
    "panels": panels,
}

out = Path(__file__).parent / "provisioning" / "dashboards" / "json" / "eternal-iot.json"
out.write_text(json.dumps(dash, indent=2, ensure_ascii=False), encoding="utf-8")
print("written", out, len(panels), "panels")
