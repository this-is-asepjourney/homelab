#!/usr/bin/env bash
# =============================================================================
# Homelab Safe Staged Orchestrator (up_homelab.sh)
# Menyalakan seluruh 19 container secara bertahap agar tidak memicu Disk I/O
# stampede atau timeout healthcheck pada cold boot.
# Volume data 100% AMAN (tidak pernah memakai flag -v).
#
# Pemakaian:
#   ./up_homelab.sh              -> Start semua layanan bertahap (tanpa rebuild)
#   ./up_homelab.sh --build      -> Build bergiliran pap-api, psm-api, psm-agent lalu start
#   ./up_homelab.sh --build-all  -> Build bergiliran termasuk psm-web lalu start
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"
export COMPOSE_PARALLEL_LIMIT=1

MODE="${1:-start}"

echo "=========================================================="
echo " Homelab Staged Startup Orchestrator (Safe Disk I/O Mode)"
echo "=========================================================="

# 0. Bersihkan container yatim (seperti rapl-exporter) atau container rename yang gagal
echo "[0/5] Membersihkan orphan/stale container (volume tetap AMAN)..."
docker rm -f rapl-exporter 2>/dev/null || true
for stale in $(docker ps -a --format '{{.Names}}' | grep -E '^[0-9a-f]{12}_' || true); do
  docker rm -f "$stale" 2>/dev/null || true
done

build_one_with_retry() {
  local svc="$1"
  echo "      -> Building image '$svc' (sequential, low CPU/IO priority)..."
  for attempt in 1 2 3; do
    if nice -n 15 ionice -c 2 -n 7 docker compose build "$svc"; then
      return 0
    fi
    echo "      [!] Build '$svc' gagal sementara (percobaan $attempt/3, kemungkinan timeout jaringan Docker Hub). Menunggu 5 detik..."
    sleep 5
  done
  echo "      [X] Gagal mem-build '$svc' setelah 3 percobaan."
  return 1
}

if [ "$MODE" = "--build" ] || [ "$MODE" = "--build-all" ]; then
  echo "[Build] Membangun image satu per satu agar disk & RAM tidak penuh..."
  build_one_with_retry psm-api
  build_one_with_retry psm-agent
  build_one_with_retry pap-api
  if [ "$MODE" = "--build-all" ]; then
    build_one_with_retry psm-web
  fi
fi

# Tahap 1: Core Databases (PostgreSQL, MySQL, Redis)
echo "[1/5] Menyalakan Core Databases (postgres, mysql, redis)..."
docker compose up -d postgres mysql redis

echo "      -> Menunggu PostgreSQL & MySQL berstatus healthy..."
for i in $(seq 1 45); do
  PG_HEALTH=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' postgres 2>/dev/null || echo "starting")
  MY_HEALTH=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' mysql 2>/dev/null || echo "starting")
  if [ "$PG_HEALTH" = "healthy" ] && [ "$MY_HEALTH" = "healthy" ]; then
    echo "      -> PostgreSQL & MySQL sudah HEALTHY!"
    break
  fi
  sleep 3
done

# Tahap 2: Object Storage & Messaging IoT
echo "[2/5] Menyalakan Object Storage & Broker IoT (minio, mosquitto, telegraf)..."
docker compose up -d minio mosquitto telegraf
for i in $(seq 1 30); do
  MN_HEALTH=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' minio 2>/dev/null || echo "starting")
  if [ "$MN_HEALTH" = "healthy" ]; then
    break
  fi
  sleep 2
done
docker compose up -d minio-init

# Tahap 3: Control Plane (PSM & PAP)
echo "[3/5] Menyalakan Control Plane (psm-api, pap-api)..."
docker compose up -d psm-api pap-api

echo "      -> Menunggu psm-api siap merespons /health..."
for i in $(seq 1 45); do
  PSM_HEALTH=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' psm-api 2>/dev/null || echo "starting")
  if [ "$PSM_HEALTH" = "healthy" ]; then
    echo "      -> psm-api sudah HEALTHY!"
    break
  fi
  sleep 3
done

echo "[4/5] Menyalakan PSM Agent, PSM Web Console, & Nextcloud..."
docker compose up -d psm-agent psm-web nextcloud

# Tahap 4: Monitoring, Ingress Router, & Local DNS
echo "[5/5] Menyalakan Observability, Nginx Router, & CoreDNS..."
docker compose up -d --remove-orphans prometheus node-exporter cadvisor grafana uptime-kuma homelab-router homelab-dns

echo ""
echo "=========================================================="
echo " Status Seluruh Layanan Homelab:"
echo "=========================================================="
docker compose ps

echo ""
echo "Cek Kesehatan Endpoint Utama:"
for item in \
  "PAP API (:8000)|http://127.0.0.1:8000/health" \
  "PSM API (:8001)|http://127.0.0.1:8001/health" \
  "PSM Web (:3005)|http://127.0.0.1:3005" \
  "MinIO S3 (:9000)|http://127.0.0.1:9000/minio/health/live" \
  "Prometheus (:9090)|http://127.0.0.1:9090/-/healthy" \
  "Grafana (:3000)|http://127.0.0.1:3000/api/health"; do
  name="${item%%|*}"
  url="${item##*|}"
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" || echo "000")
  printf "  %-22s -> HTTP %s\n" "$name" "$code"
done
echo "=========================================================="
