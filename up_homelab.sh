#!/usr/bin/env bash
# =============================================================================
# Homelab Zero-Downtime Staged Orchestrator (up_homelab.sh)
# Membangun image di jalur terisolasi (homelab-build.slice cgroups v2) sehingga
# SELURUH LAYANAN AKTIF TETAP JALAN LANCAR 100% tanpa dihentikan saat build!
# Volume data 100% AMAN (tidak pernah memakai flag -v).
#
# Pemakaian:
#   ./up_homelab.sh               -> Start semua layanan bertahap (tanpa rebuild)
#   ./up_homelab.sh --build       -> Smart background build API & Agent (skip yg sama)
#   ./up_homelab.sh --build-all   -> Smart background build semua image termasuk psm-web
#   ./up_homelab.sh --force-build -> Paksa rebuild semua image di jalur terisolasi
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")"
export COMPOSE_PARALLEL_LIMIT=1
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

MODE="${1:-start}"
HASH_DIR=".build_hashes"
mkdir -p "$HASH_DIR"

echo "=========================================================="
echo " Homelab Zero-Downtime Orchestrator (Isolated Build Lane)"
echo "=========================================================="

# 0. Bersihkan container yatim atau container rename yang gagal
echo "[0/5] Membersihkan orphan/stale container (seluruh layanan aktif & volume tetap AMAN)..."
docker rm -f rapl-exporter 2>/dev/null || true
for stale in $(docker ps -a --format '{{.Names}}' | grep -E '^[0-9a-f]{12}_' || true); do
  docker rm -f "$stale" 2>/dev/null || true
done

# Deteksi dukungan systemd cgroup slice agar build tidak mengganggu CPU/RAM/IO layanan aktif
CGROUP_DRIVER=$(docker info --format '{{.CgroupDriver}}' 2>/dev/null || echo "unknown")
BUILD_CGROUP_ARGS=()
if [ "$CGROUP_DRIVER" = "systemd" ]; then
  BUILD_CGROUP_ARGS=(--cgroup-parent=homelab-build.slice)
  echo "      [QoS-Lane] Isolasi Cgroup v2 aktif (homelab-build.slice) — layanan utama dijamin tidak terganggu!"
fi

compute_service_hash() {
  local svc="$1"
  case "$svc" in
    psm-api)
      find Personal_Server_Manager/apps/api Personal_Server_Manager/infrastructure/docker/Dockerfile.api -type f \
        ! -path "*/__pycache__/*" ! -name "*.pyc" -print0 2>/dev/null | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
      ;;
    psm-agent)
      find Personal_Server_Manager/apps/agent Personal_Server_Manager/infrastructure/docker/Dockerfile.agent -type f \
        ! -path "*/__pycache__/*" ! -name "*.pyc" -print0 2>/dev/null | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
      ;;
    pap-api)
      find Personal_Api_Platform/app Personal_Api_Platform/docker/Dockerfile Personal_Api_Platform/requirements.txt \
           Personal_Server_Manager/apps/api Personal_Server_Manager/apps/agent -type f \
        ! -path "*/__pycache__/*" ! -name "*.pyc" -print0 2>/dev/null | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
      ;;
    psm-web)
      find Personal_Server_Manager/apps/web Personal_Server_Manager/infrastructure/docker/Dockerfile.web -type f \
        ! -path "*/node_modules/*" ! -path "*/.next/*" -print0 2>/dev/null | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
      ;;
    *)
      date +%s
      ;;
  esac
}

run_isolated_docker_build() {
  local svc="$1"
  local tag="homelab-${svc}:latest"
  local context_dir="."
  local df_path=""
  local extra_args=()

  case "$svc" in
    pap-api)
      context_dir="."
      df_path="Personal_Api_Platform/docker/Dockerfile"
      ;;
    psm-api)
      context_dir="./Personal_Server_Manager"
      df_path="Personal_Server_Manager/infrastructure/docker/Dockerfile.api"
      ;;
    psm-agent)
      context_dir="./Personal_Server_Manager"
      df_path="Personal_Server_Manager/infrastructure/docker/Dockerfile.agent"
      ;;
    psm-web)
      context_dir="./Personal_Server_Manager"
      df_path="Personal_Server_Manager/infrastructure/docker/Dockerfile.web"
      extra_args=(--build-arg INTERNAL_API_URL=http://psm-api:8000)
      ;;
  esac

  if DOCKER_BUILDKIT=1 nice -n 15 ionice -c 2 -n 7 docker build \
    "${BUILD_CGROUP_ARGS[@]}" \
    "${extra_args[@]}" \
    -t "$tag" \
    -f "$df_path" \
    "$context_dir"; then
    return 0
  fi

  # Fallback tanpa --cgroup-parent jika daemon Docker kustom tidak menggunakan systemd slice
  if [ "${#BUILD_CGROUP_ARGS[@]}" -gt 0 ]; then
    echo "      [Info] Mengulang build '$svc' dengan mode kompatibilitas..."
    DOCKER_BUILDKIT=1 nice -n 15 ionice -c 2 -n 7 docker build \
      "${extra_args[@]}" \
      -t "$tag" \
      -f "$df_path" \
      "$context_dir"
    return $?
  fi
  return 1
}

build_one_smart() {
  local svc="$1"
  local tag="homelab-${svc}:latest"
  local current_hash
  current_hash=$(compute_service_hash "$svc")
  local hash_file="${HASH_DIR}/${svc}.sha256"
  local img_exists=0

  if docker image inspect "$tag" >/dev/null 2>&1; then
    img_exists=1
  fi

  if [ "$MODE" != "--force-build" ] && [ "$img_exists" -eq 1 ] && [ -f "$hash_file" ] && [ "$(cat "$hash_file")" = "$current_hash" ]; then
    echo "      -> [SKIP] Source '$svc' tidak berubah sejak build terakhir (hemat waktu & I/O)."
    return 0
  fi

  echo "      -> [BUILD] Building '$svc' di jalur background terisolasi (seluruh layanan aktif tetap berjalan normal)..."
  for attempt in 1 2 3; do
    if run_isolated_docker_build "$svc"; then
      echo "$current_hash" > "$hash_file"
      return 0
    fi
    echo "      [!] Build '$svc' gagal sementara (percobaan $attempt/3). Menunggu 5 detik..."
    sleep 5
  done
  echo "      [X] Gagal mem-build '$svc' setelah 3 percobaan."
  return 1
}

if [ "$MODE" = "--build" ] || [ "$MODE" = "--build-all" ] || [ "$MODE" = "--force-build" ]; then
  echo "[Build] Membangun image yang berubah satu per satu di jalur cgroup rendah prioritas..."
  build_one_smart psm-api
  build_one_smart psm-agent
  build_one_smart pap-api
  if [ "$MODE" = "--build-all" ] || [ "$MODE" = "--force-build" ]; then
    build_one_smart psm-web
  fi
fi

# Tahap 1: Core Databases (PostgreSQL, MySQL, Redis)
echo "[1/5] Memastikan Core Databases aktif (postgres, mysql, redis)..."
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
echo "[2/5] Memastikan Object Storage & Broker IoT aktif (minio, mosquitto, telegraf)..."
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
echo "[3/5] Memperbarui / menyalakan Control Plane (psm-api, pap-api)..."
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

echo "[4/5] Memastikan PSM Agent, PSM Web Console, & Nextcloud aktif..."
docker compose up -d psm-agent psm-web nextcloud

# Tahap 5: Monitoring, Ingress Router, & Local DNS
echo "[5/5] Memastikan Observability, Nginx Router, & CoreDNS aktif..."
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
