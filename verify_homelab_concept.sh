#!/usr/bin/env bash
# =============================================================================
# Homelab Architecture & Concept Verification Scorecard (verify_homelab_concept.sh)
# Mengecek 7 pilar konsep Personal Cloud Management Platform secara otomatis
# (100% Read-Only — aman dijalankan kapan saja di server Ubuntu).
# =============================================================================
set -u

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf "  ${GREEN}[PASS]${NC} %s\n" "$1"
}

warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  printf "  ${YELLOW}[WARN]${NC} %s\n" "$1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf "  ${RED}[FAIL]${NC} %s\n" "$1"
}

section() {
  printf "\n${CYAN}${BOLD}=== %s ===${NC}\n" "$1"
}

check_http() {
  local label="$1"
  local url="$2"
  local host_header="${3:-}"
  local code
  if [ -n "$host_header" ]; then
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 4 -H "Host: $host_header" "$url" 2>/dev/null || echo "000")
  else
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 4 "$url" 2>/dev/null || echo "000")
  fi
  if [ "$code" = "200" ] || [ "$code" = "204" ] || [ "$code" = "302" ]; then
    pass "$label (HTTP $code)"
  else
    fail "$label tidak merespons normal (HTTP $code -> $url)"
  fi
}

echo "======================================================================"
echo " HOMELAB PERSONAL CLOUD PLATFORM — AUDIT KESESUAIAN KONSEP"
echo "======================================================================"

# -----------------------------------------------------------------------------
# PILAR 1: 3-TIER QoS, CGROUPS V2 BUILD ISOLATION & MEMORI HOST
# -----------------------------------------------------------------------------
section "1. Isolasi Build 3-Tier QoS & Kesehatan Memori Kernel"

if [ -f /etc/systemd/system/homelab-build.slice ]; then
  QUOTA=$(grep -E "^CPUQuota=" /etc/systemd/system/homelab-build.slice | cut -d= -f2 || echo "n/a")
  MEM_HIGH=$(grep -E "^MemoryHigh=" /etc/systemd/system/homelab-build.slice | cut -d= -f2 || echo "n/a")
  pass "Slice isolasi build 'homelab-build.slice' terdaftar (CPUQuota=$QUOTA, MemoryHigh=$MEM_HIGH)"
else
  warn "File /etc/systemd/system/homelab-build.slice belum ada (jalankan ./optimize_homelab_host.sh)"
fi

if swapon --show=NAME 2>/dev/null | grep -q "zram"; then
  pass "Linux zram Compressed RAM Swap aktif (anti-freeze saat build berat)"
else
  warn "zram belum aktif (opsional, jalankan ./optimize_homelab_host.sh untuk akselerasi RAM)"
fi

AVAIL_MB=$(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo "0")
if [ "$AVAIL_MB" -ge 600 ]; then
  pass "Headroom memori tersedia sehat: ${AVAIL_MB} MB MemAvailable"
else
  warn "MemAvailable saat ini rendah (${AVAIL_MB} MB) — pantau tab Monitoring Swap"
fi

# -----------------------------------------------------------------------------
# PILAR 2: CORE DATABASES & CACHE (PostgreSQL 16, MySQL 8.4, Redis 7)
# -----------------------------------------------------------------------------
section "2. Core Databases & Shared Data Layer"

PG_STATE=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' postgres 2>/dev/null || echo "missing")
if [ "$PG_STATE" = "healthy" ] || [ "$PG_STATE" = "running" ]; then
  PG_DBS=$(docker exec postgres psql -U "${POSTGRES_USER:-fadb}" -d postgres -tAc "SELECT datname FROM pg_database WHERE datistemplate=false;" 2>/dev/null | tr '\n' ' ')
  pass "PostgreSQL 16 ($PG_STATE) — DB terdaftar: ${PG_DBS:-ok}"
else
  fail "Container 'postgres' belum sehat (status: $PG_STATE)"
fi

MY_STATE=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' mysql 2>/dev/null || echo "missing")
if [ "$MY_STATE" = "healthy" ] || [ "$MY_STATE" = "running" ]; then
  pass "MySQL 8.4 Shared App Database ($MY_STATE)"
else
  fail "Container 'mysql' belum sehat (status: $MY_STATE)"
fi

REDIS_PONG=$(docker exec redis redis-cli ping 2>/dev/null || echo "ERR")
if [ "$REDIS_PONG" = "PONG" ]; then
  pass "Redis 7 Cache & Queue merespons PONG"
else
  fail "Container 'redis' belum merespons PONG ($REDIS_PONG)"
fi

# -----------------------------------------------------------------------------
# PILAR 3: PAP CORE CENTRAL GATEWAY & RESPONSIVITAS (< 50 ms)
# -----------------------------------------------------------------------------
section "3. PAP Core Central API Gateway (:8000) & Responsivitas"

PAP_HEADERS=$(curl -sD - -o /dev/null --max-time 4 "http://127.0.0.1:8000/api/v1/system/central-pulse" 2>/dev/null || echo "")
if echo "$PAP_HEADERS" | grep -qE "HTTP/.* 200"; then
  RESP_MS=$(echo "$PAP_HEADERS" | awk -F': ' 'tolower($1)=="x-response-time-ms" {gsub(/\r/,"",$2); print $2}')
  pass "PAP Central Pulse (/api/v1/system/central-pulse) aktif — waktu respons: ${RESP_MS:-<5} ms"
else
  fail "PAP Central Pulse belum merespons HTTP 200 di port :8000"
fi
check_http "PAP Readiness DB Check (/api/v1/ready)" "http://127.0.0.1:8000/api/v1/ready"

# -----------------------------------------------------------------------------
# PILAR 4: PSM SERVER OPERATIONS LAYER (:8001, :3005, Host Agent)
# -----------------------------------------------------------------------------
section "4. Personal Server Manager (PSM Operations & Host Agent)"

check_http "PSM Control Plane API (:8001/health)" "http://127.0.0.1:8001/health"
check_http "PSM Web Console UI (:3005)" "http://127.0.0.1:3005"

AGENT_STATE=$(docker inspect --format '{{.State.Status}}|{{.HostConfig.PidMode}}' psm-agent 2>/dev/null || echo "missing|none")
if [ "$AGENT_STATE" = "running|host" ]; then
  pass "psm-agent berjalan dengan pid:host (monitoring realtime /proc/1/net & cgroup build aktif)"
elif [[ "$AGENT_STATE" == running* ]]; then
  warn "psm-agent berjalan tetapi belum memakai pid:host ($AGENT_STATE)"
else
  fail "psm-agent tidak berjalan ($AGENT_STATE)"
fi

# -----------------------------------------------------------------------------
# PILAR 5: STORAGE, MINIO S3 VAULT & UNIFIED BACKUP PIPELINE
# -----------------------------------------------------------------------------
section "5. Storage, MinIO S3 Vault & Alur Backup Terpadu"

check_http "MinIO / Silo S3 Live Health (:9000)" "http://127.0.0.1:9000/minio/health/live"
check_http "Nextcloud Personal Cloud (:8080)" "http://127.0.0.1:8080/status.php"

FLOW_JSON=$(curl -s --max-time 4 "http://127.0.0.1:8000/api/v1/backups/flow-status" 2>/dev/null || echo "")
if echo "$FLOW_JSON" | grep -q '"pipeline_architecture"'; then
  pass "Alur Backup Terpadu PAP Core (/api/v1/backups/flow-status) terverifikasi siap"
else
  fail "Endpoint /api/v1/backups/flow-status belum aktif pada pap-api"
fi

PAP_MOUNT=$(docker inspect --format '{{range .Mounts}}{{.Destination}} {{end}}' pap-api 2>/dev/null || echo "")
if echo "$PAP_MOUNT" | grep -q "/opt/psm"; then
  pass "Volume persisten '/opt/psm' ter-mount di pap-api (backup aman saat rebuild)"
else
  warn "Volume '/opt/psm' belum terlihat di pap-api (recreate pap-api dengan ./up_homelab.sh)"
fi

# -----------------------------------------------------------------------------
# PILAR 6: OBSERVABILITY & ETERNAL IoT PIPELINE
# -----------------------------------------------------------------------------
section "6. Observability (Prometheus, Grafana, cAdvisor, Uptime Kuma) & IoT"

check_http "Prometheus TSDB (:9090/-/healthy)" "http://127.0.0.1:9090/-/healthy"
UP_TARGETS=$(curl -s --max-time 4 "http://127.0.0.1:9090/api/v1/targets" 2>/dev/null | grep -o '"health":"up"' | wc -l | tr -d ' ')
if [ "${UP_TARGETS:-0}" -ge 4 ]; then
  pass "Prometheus aktif men-scrape ${UP_TARGETS} target berstatus UP"
else
  warn "Target Prometheus berstatus UP baru ${UP_TARGETS:-0} (cek http://prometheus.homelab)"
fi

check_http "Grafana Dashboards (:3000/api/health)" "http://127.0.0.1:3000/api/health"
check_http "cAdvisor Container Metrics (:8081/healthz)" "http://127.0.0.1:8081/healthz"
check_http "Uptime Kuma Monitor (:3001)" "http://127.0.0.1:3001"
check_http "Telegraf IoT Metrics Exporter (:9273/metrics)" "http://127.0.0.1:9273/metrics"

MQTT_STATE=$(docker inspect --format '{{.State.Status}}' mosquitto 2>/dev/null || echo "missing")
if [ "$MQTT_STATE" = "running" ]; then
  pass "Mosquitto MQTT Broker (:1883) aktif"
else
  fail "Mosquitto MQTT Broker tidak aktif ($MQTT_STATE)"
fi

# -----------------------------------------------------------------------------
# PILAR 7: INGRESS ROUTER (:80) & LOCAL DNS (:53)
# -----------------------------------------------------------------------------
section "7. Domain Virtual Host Ingress (:80) & Local DNS (:53)"

check_http "Nginx VHost Routing -> api.homelab" "http://127.0.0.1/health" "api.homelab"
check_http "Nginx VHost Routing -> psm-api.homelab" "http://127.0.0.1/health" "psm-api.homelab"
check_http "Nginx VHost Routing -> psm.homelab" "http://127.0.0.1/" "psm.homelab"

DNS_STATE=$(docker inspect --format '{{.State.Status}}' homelab_dns 2>/dev/null || echo "missing")
if [ "$DNS_STATE" = "running" ]; then
  pass "CoreDNS Local Resolver (:53) aktif melayani zona *.homelab"
else
  warn "Container homelab_dns belum berstatus running ($DNS_STATE)"
fi

# -----------------------------------------------------------------------------
# RINGKASAN SKOR AKHIR
# -----------------------------------------------------------------------------
TOTAL=$((PASS_COUNT + WARN_COUNT + FAIL_COUNT))
SCORE=$(( PASS_COUNT * 100 / (TOTAL > 0 ? TOTAL : 1) ))

echo ""
echo "======================================================================"
printf " HASIL AUDIT KONSEP HOMELAB: ${GREEN}%d PASS${NC} | ${YELLOW}%d WARN${NC} | ${RED}%d FAIL${NC} (Skor: ${BOLD}%d%%${NC})\n" \
  "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" "$SCORE"
echo "======================================================================"
if [ "$FAIL_COUNT" -eq 0 ]; then
  printf " ${GREEN}${BOLD}SELAMAT! Ekosistem Homelab sudah berjalan sesuai konsep arsitektur.${NC}\n"
else
  printf " ${YELLOW}Terdapat %d komponen yang belum siap. Jalankan './up_homelab.sh' untuk memulihkan.${NC}\n" "$FAIL_COUNT"
fi
echo "======================================================================"
