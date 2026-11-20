#!/usr/bin/env bash
# =============================================================================
# setup_eternal_iot.sh — Deploy & Sinkronisasi Pipeline IoT Eternal di Homelab
# =============================================================================
set -e

echo "=== [1/4] Memastikan Database eternal_iot & Schema Terbuat di PostgreSQL ==="
docker compose exec -T postgres psql -U fadb -d postgres -c "CREATE DATABASE eternal_iot OWNER fadb;" 2>/dev/null || true
docker compose exec -T postgres psql -U fadb -d postgres -c "GRANT ALL PRIVILEGES ON DATABASE eternal_iot TO fadb;" 2>/dev/null || true

echo "=== [2/4] Eksekusi Inisialisasi Tabel & Seed Data eternal_iot ==="
docker compose exec -T postgres psql -U fadb -d eternal_iot -f /docker-entrypoint-initdb.d/02-eternal-iot.sql

echo "=== [3/4] Build & Restart Container Layanan (Telegraf, Prometheus, PAP-API, MinIO, Grafana) ==="
docker compose pull telegraf
docker compose build pap-api
docker compose up -d mosquitto postgres mysql minio minio-init telegraf prometheus pap-api grafana
docker compose restart prometheus grafana

echo "=== [4/4] Verifikasi Status Kontainer ==="
docker compose ps telegraf prometheus pap-api postgres minio grafana

echo ""
echo "✅ Pipeline IoT Eternal & MinIO Siap!"
echo "• Grafana Dashboard: http://192.168.25.105:3000 (atau http://grafana.homelab)"
echo "• MinIO Console:     http://192.168.25.105:9002 (User: admin / Pass: admin12345)"
echo "• Prometheus Targets: http://192.168.25.105:9090/targets (Pastikan telegraf status UP)"
