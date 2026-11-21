#!/usr/bin/env python3
"""Generate the single PostgreSQL-based Eternal IoT dashboard.
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
    return [{"datasource": DS, "editorMode": "code", "format": fmt,
             "rawQuery": True, "rawSql": sql, "refId": "A"}]


def row(title, y):
    panels.append({"id": nid(), "type": "row", "title": title, "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}})


def steps(*pairs):
    return {"mode": "absolute",
            "steps": [{"color": c, "value": v} for c, v in pairs]}


def vmap(d):
    return [{"type": "value", "options": {
        str(k): {"text": t, "color": c, "index": i} for i, (k, (t, c)) in enumerate(d.items())}}]


def stat(title, sql, x, y, w, h=4, unit="none", th=None, maps=None, dec=None,
         color_mode="background", text_mode="auto", desc=""):
    d = {"unit": unit, "color": {"mode": "thresholds"},
         "thresholds": th or steps(("blue", None)), "mappings": maps or [],
         "noValue": "–"}
    if dec is not None:
        d["decimals"] = dec
    panels.append({
        "id": nid(), "type": "stat", "title": title, "description": desc,
        "datasource": DS, "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": tgt(sql),
        "fieldConfig": {"defaults": d, "overrides": []},
        "options": {"colorMode": color_mode, "graphMode": "none", "justifyMode": "center",
                    "textMode": text_mode, "orientation": "auto",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}})


def gauge(title, sql, x, y, w, h, unit, mn, mx, th, dec=1):
    panels.append({
        "id": nid(), "type": "gauge", "title": title, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y}, "targets": tgt(sql),
        "fieldConfig": {"defaults": {"unit": unit, "min": mn, "max": mx, "decimals": dec,
                                     "color": {"mode": "thresholds"}, "thresholds": th,
                                     "noValue": "–"}, "overrides": []},
        "options": {"showThresholdMarkers": True, "showThresholdLabels": False,
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}})


def ts(title, sql, x, y, w, h, unit="none", colors=None, bars=False, stack=False,
       mn=None, mx=None, desc=""):
    d = {"unit": unit, "color": {"mode": "palette-classic"}, "noValue": "Belum ada data",
         "custom": {"drawStyle": "bars" if bars else "line", "lineInterpolation": "smooth",
                    "lineWidth": 2, "fillOpacity": 70 if bars else 18,
                    "gradientMode": "opacity", "showPoints": "never", "spanNulls": True,
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"}}}
    if mn is not None:
        d["min"] = mn
    if mx is not None:
        d["max"] = mx
    ov = [{"matcher": {"id": "byName", "options": n},
           "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
          for n, c in (colors or {}).items()]
    panels.append({
        "id": nid(), "type": "timeseries", "title": title, "description": desc,
        "datasource": DS, "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": tgt(sql, "time_series"),
        "fieldConfig": {"defaults": d, "overrides": ov},
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "none"}}})


def table(title, sql, x, y, w, h, overrides=None):
    panels.append({
        "id": nid(), "type": "table", "title": title, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y}, "targets": tgt(sql),
        "fieldConfig": {"defaults": {"custom": {"align": "left", "cellOptions": {"type": "auto"}},
                                     "noValue": "Belum ada data"},
                        "overrides": overrides or []},
        "options": {"showHeader": True, "cellHeight": "sm"}})


LAST = f"FROM iot_devices WHERE device_serial = {S} LIMIT 1"
LASTLOG = f"FROM iot_telemetry_logs WHERE device_serial = {S} AND temperature_c > 0 ORDER BY recorded_at DESC LIMIT 1"

# ---------------------------------------------------------------- Row 1: status
row("Status Saat Ini", 0)
stat("Perangkat",
     f"SELECT CASE WHEN last_seen_at >= NOW() - INTERVAL '90 seconds' THEN 1 ELSE 0 END AS v {LAST}",
     0, 1, 4, maps=vmap({1: ("ONLINE", "green"), 0: ("OFFLINE", "red")}),
     th=steps(("red", None), ("green", 1)),
     desc="Online bila ada paket MQTT dalam 90 detik terakhir.")
stat("Kunci Pintu",
     f"SELECT is_locked AS v FROM device_locks WHERE device_serial = {S} LIMIT 1",
     4, 1, 4, maps=vmap({1: ("TERKUNCI", "blue"), 0: ("TERBUKA", "orange")}),
     th=steps(("orange", None), ("blue", 1)))
stat("Sisa Auto-Lock",
     f"SELECT remaining_sec AS v FROM iot_telemetry_logs WHERE device_serial = {S} ORDER BY recorded_at DESC LIMIT 1",
     8, 1, 4, unit="s", th=steps(("text", None)), color_mode="value",
     maps=[{"type": "value", "options": {"0": {"text": "–", "index": 0}}}],
     desc="Hitung mundur sebelum pintu terkunci otomatis (maks 2 jam).")
stat("Sinyal WiFi", f"SELECT wifi_rssi AS v {LAST}", 12, 1, 4, unit="dBm", dec=0,
     th=steps(("red", None), ("orange", -80), ("yellow", -70), ("green", -60)))
stat("Uptime", f"SELECT uptime_sec AS v {LAST}", 16, 1, 4, unit="s", dec=0,
     th=steps(("purple", None)), color_mode="value")
stat("Free Heap", f"SELECT free_heap AS v {LAST}", 20, 1, 4, unit="bytes", dec=0,
     th=steps(("red", None), ("orange", 80000), ("green", 150000)))

# ---------------------------------------------------------------- Row 2: env
row("Suhu & Kelembapan (DHT11)", 5)
gauge("Suhu Udara", f"SELECT temperature_c AS v {LAST}", 0, 6, 6, 6, "celsius", 15, 45,
      steps(("blue", None), ("green", 22), ("orange", 31), ("red", 36)))
gauge("Kelembapan", f"SELECT humidity_pct AS v {LAST}", 6, 6, 6, 6, "humidity", 0, 100,
      steps(("orange", None), ("green", 40), ("blue", 70), ("purple", 85)), dec=0)
gauge("Heat Index", f"SELECT heat_index_c AS v {LAST}", 12, 6, 6, 6, "celsius", 15, 55,
      steps(("green", None), ("yellow", 32), ("orange", 38), ("red", 45)))
stat("Kualitas Udara", f"SELECT air_quality AS v {LAST}", 18, 6, 6, 6,
     th=steps(("green", None)), text_mode="value", color_mode="value",
     desc="Dikirim langsung oleh firmware ESP32 (Sejuk / Nyaman / Hangat / ...).")
ts("Tren Suhu & Heat Index",
   "SELECT $__timeGroupAlias(recorded_at,'1m'), "
   'AVG(temperature_c) AS "Suhu", AVG(heat_index_c) AS "Heat Index" '
   f"FROM iot_telemetry_logs WHERE $__timeFilter(recorded_at) AND device_serial = {S} "
   "AND temperature_c > 0 GROUP BY 1 ORDER BY 1",
   0, 12, 12, 8, unit="celsius", colors={"Suhu": "orange", "Heat Index": "red"})
ts("Tren Kelembapan",
   "SELECT $__timeGroupAlias(recorded_at,'1m'), "
   'AVG(humidity_pct) AS "Kelembapan" '
   f"FROM iot_telemetry_logs WHERE $__timeFilter(recorded_at) AND device_serial = {S} "
   "AND humidity_pct > 0 GROUP BY 1 ORDER BY 1",
   12, 12, 12, 8, unit="humidity", colors={"Kelembapan": "blue"}, mn=0, mx=100)

# ---------------------------------------------------------------- Row 3: NFC
row("Akses Kartu NFC", 20)
CNT = "FROM nfc_access_logs WHERE $__timeFilter(created_at)"
stat("Total Tap", f"SELECT COUNT(*) AS v {CNT}", 0, 21, 6, th=steps(("purple", None)),
     dec=0, desc="Jumlah tap pada rentang waktu yang dipilih.")
stat("Pintu Dibuka", f"SELECT COUNT(*) AS v {CNT} AND action_type NOT ILIKE '%DENIED%' AND (action_type ILIKE '%UNLOCK%' OR action_type ILIKE '%TAP_ON%')",
     6, 21, 6, th=steps(("green", None)), dec=0)
stat("Ditolak", f"SELECT COUNT(*) AS v {CNT} AND action_type ILIKE '%DENIED%'",
     12, 21, 6, th=steps(("green", None), ("red", 1)), dec=0)
stat("Kartu Terdaftar", "SELECT COUNT(*) AS v FROM nfc_users WHERE is_active = 1",
     18, 21, 6, th=steps(("blue", None)), dec=0)
ts("Aktivitas Tap per Jam",
   "SELECT $__timeGroupAlias(created_at,'1h'), "
   "SUM(CASE WHEN action_type ILIKE '%DENIED%' THEN 0 ELSE 1 END) AS \"Diizinkan\", "
   "SUM(CASE WHEN action_type ILIKE '%DENIED%' THEN 1 ELSE 0 END) AS \"Ditolak\" "
   "FROM nfc_access_logs WHERE $__timeFilter(created_at) GROUP BY 1 ORDER BY 1",
   0, 25, 10, 9, bars=True, stack=True, colors={"Diizinkan": "green", "Ditolak": "red"})
act = [{"matcher": {"id": "byName", "options": "Aksi"}, "properties": [
    {"id": "mappings", "value": vmap({"BUKA": ("BUKA", "green"), "KUNCI": ("KUNCI", "blue"),
                                       "DITOLAK": ("DITOLAK", "red")})},
    {"id": "custom.cellOptions", "value": {"type": "color-text"}},
    {"id": "custom.width", "value": 110}]}]
table("Riwayat Akses Terbaru",
      "SELECT created_at AS \"Waktu\", COALESCE(NULLIF(nama,'-'),'Tidak dikenal') AS \"Nama\", "
      "uid AS \"UID Kartu\", role AS \"Role\", "
      "CASE WHEN action_type ILIKE '%DENIED%' THEN 'DITOLAK' "
      "WHEN action_type ILIKE '%UNLOCK%' OR action_type ILIKE '%TAP_ON%' THEN 'BUKA' "
      "ELSE 'KUNCI' END AS \"Aksi\" "
      "FROM nfc_access_logs WHERE $__timeFilter(created_at) ORDER BY created_at DESC LIMIT 50",
      10, 25, 14, 9, overrides=act)

# ---------------------------------------------------------------- Row 4: device
row("Informasi Perangkat", 34)
table("Perangkat Terdaftar",
      "SELECT device_name AS \"Nama\", device_serial AS \"Serial\", model AS \"Model\", "
      "ip_address AS \"IP\", mac_address AS \"MAC\", ssid AS \"WiFi\", "
      "firmware_version AS \"Firmware\", last_seen_at AS \"Terakhir Terlihat\" "
      "FROM iot_devices ORDER BY device_serial",
      0, 35, 24, 5)

dash = {
    "uid": "eternal-iot-main",
    "title": "Eternal IoT — Smart Lock & Sensor",
    "description": "ESP32 → MQTT → PAP Worker → PostgreSQL eternal_iot → Grafana",
    "tags": ["eternal", "iot", "esp32", "postgresql"],
    "timezone": "Asia/Jakarta", "schemaVersion": 39, "version": 1, "editable": True,
    "graphTooltip": 1, "refresh": "10s",
    "time": {"from": "now-6h", "to": "now"},
    "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
    "templating": {"list": [{
        "name": "serial", "label": "Perangkat", "type": "query", "datasource": DS,
        "query": "SELECT device_serial FROM iot_devices ORDER BY 1", "refresh": 1,
        "includeAll": False, "multi": False, "sort": 1,
        "current": {"text": "ETL-ESP32-001", "value": "ETL-ESP32-001"}}]},
    "annotations": {"list": []},
    "panels": panels,
}

out = Path(__file__).parent / "provisioning" / "dashboards" / "json" / "eternal-iot.json"
out.write_text(json.dumps(dash, indent=2, ensure_ascii=False), encoding="utf-8")
print("written", out, len(panels), "panels")
