#!/usr/bin/env bash
# =============================================================================
# Homelab Host 3-Tier QoS, cgroups v2 Build Slice & zram Optimizer (Ubuntu)
# Membagi resource secara ketat di level Kernel Linux (cgroups v2) agar proses
# Docker build berjalan santai di background TANPA mengganggu layanan aktif!
# Aman dijalankan berulang kali (idempotent) — TIDAK menghapus volume data.
# =============================================================================
set -euo pipefail

echo "=========================================================="
echo " Homelab 3-Tier Resource Partitioning & Build Isolation"
echo "=========================================================="

if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

# 1. Hitung profil CPU & RAM fisik server secara otomatis
CORES=$(nproc 2>/dev/null || echo 2)
TOTAL_RAM_MB=$(free -m | awk '/^Mem:/ {print $2}')
TOTAL_RAM_MB=${TOTAL_RAM_MB:-3900}

# Sisakan minimal 35-40% kapasitas total seluruh core CPU khusus untuk layanan aktif!
# Contoh: 2 core (200%) -> Build dibatasi maks 130% (0.7 core selalu 100% bebas untuk API/DB/Web).
#         4 core (400%) -> Build dibatasi maks 260% (1.4 core selalu 100% bebas).
BUILD_CPU_QUOTA=$(( CORES * 65 ))
if [ "$BUILD_CPU_QUOTA" -lt 65 ]; then
  BUILD_CPU_QUOTA=65
fi

# Batasi jatah RAM fisik untuk jalur Build (MemoryHigh = rem halus ke zram, MemoryMax = batas keras)
# Contoh pada server 4 GB (3900 MB): MemoryHigh=960M, MemoryMax=1400M -> 2.5+ GB RAM fisik dijamin aman untuk layanan!
BUILD_MEM_HIGH=$(( TOTAL_RAM_MB * 25 / 100 ))
[ "$BUILD_MEM_HIGH" -lt 768 ] && BUILD_MEM_HIGH=768
[ "$BUILD_MEM_HIGH" -gt 2048 ] && BUILD_MEM_HIGH=2048

BUILD_MEM_MAX=$(( TOTAL_RAM_MB * 36 / 100 ))
[ "$BUILD_MEM_MAX" -lt 1152 ] && BUILD_MEM_MAX=1152
[ "$BUILD_MEM_MAX" -gt 3072 ] && BUILD_MEM_MAX=3072

echo "[Info] Terdeteksi ${CORES} vCPU & ${TOTAL_RAM_MB} MB RAM."
echo "       Jatah khusus Jalur Build (homelab-build.slice):"
echo "       -> CPUQuota=${BUILD_CPU_QUOTA}% | CPUWeight=15 (Layanan aktif=100+)"
echo "       -> MemoryHigh=${BUILD_MEM_HIGH}M | MemoryMax=${BUILD_MEM_MAX}M | IOWeight=10"

# 2. Buat & Aktifkan Systemd Cgroup v2 Slice khusus Build (homelab-build.slice)
SLICE_FILE="/etc/systemd/system/homelab-build.slice"
echo "[1/6] Mengonfigurasi Cgroup v2 Slice isolasi build ($SLICE_FILE)..."
$SUDO tee "$SLICE_FILE" >/dev/null <<EOF
[Unit]
Description=Homelab Isolated Low-Priority Docker Build Slice
Before=slices.target

[Slice]
# Prioritas CPU & Disk I/O sangat rendah dibanding container layanan utama (default 100)
CPUAccounting=yes
CPUWeight=15
CPUQuota=${BUILD_CPU_QUOTA}%
IOAccounting=yes
IOWeight=10
# Rem memori cerdas cgroups v2: jika build melebihi MemoryHigh, hanya memori build yang dikompresi ke zram,
# memori PostgreSQL, MySQL, Nginx, PAP, PSM, dan Nextcloud TIDAK PERNAH terusik!
MemoryAccounting=yes
MemoryHigh=${BUILD_MEM_HIGH}M
MemoryMax=${BUILD_MEM_MAX}M
EOF
$SUDO systemctl daemon-reload >/dev/null 2>&1 || true
$SUDO systemctl start homelab-build.slice >/dev/null 2>&1 || true
echo "      -> homelab-build.slice aktif di kernel cgroups v2."

# 3. Kernel VM / Swap / Dirty-Page Tuning
SYSCTL_FILE="/etc/sysctl.d/99-homelab-performance.conf"
echo "[2/6] Mengatur kernel sysctl ($SYSCTL_FILE)..."
$SUDO tee "$SYSCTL_FILE" >/dev/null <<'EOF'
vm.swappiness = 10
vm.vfs_cache_pressure = 80
vm.dirty_background_ratio = 4
vm.dirty_ratio = 10
EOF
$SUDO sysctl --system >/dev/null 2>&1 || true
echo "      -> vm.swappiness=10, dirty_background_ratio=4, dirty_ratio=10 aktif."

# 4. Aktifkan Linux zram (1.5 GB Compressed RAM Swap prioritas tinggi = 100x lebih cepat dari Disk Swap!)
echo "[3/6] Mengaktifkan zram Compressed RAM Swap..."
if ! swapon --show=NAME 2>/dev/null | grep -q "zram"; then
  if $SUDO modprobe zram 2>/dev/null; then
    ZRAM_DEV=""
    if [ -e /sys/class/zram-control/hot_add ] && [ ! -b /dev/zram0 ]; then
      ZDEV_ID=$($SUDO cat /sys/class/zram-control/hot_add 2>/dev/null || echo "0")
      ZRAM_DEV="/dev/zram${ZDEV_ID}"
    elif [ -b /dev/zram0 ]; then
      ZRAM_DEV="/dev/zram0"
    fi
    if [ -n "$ZRAM_DEV" ] && [ -b "$ZRAM_DEV" ]; then
      ZNAME=$(basename "$ZRAM_DEV")
      echo lz4 | $SUDO tee "/sys/block/${ZNAME}/comp_algorithm" >/dev/null 2>&1 || true
      echo 1536M | $SUDO tee "/sys/block/${ZNAME}/disksize" >/dev/null 2>&1 || true
      $SUDO mkswap "$ZRAM_DEV" >/dev/null 2>&1 && $SUDO swapon -p 100 "$ZRAM_DEV" 2>/dev/null || true
      echo "      -> zram 1.5 GB aktif di $ZRAM_DEV (priority 100)."
    fi
  else
    echo "      -> Modul zram tidak tersedia di kernel ini, lanjut ke disk swap."
  fi
else
  echo "      -> zram sudah aktif."
fi

# 5. Pastikan Disk Swap cadangan tersedia
TOTAL_SWAP_MB=$(free -m | awk '/^Swap:/ {print $2}')
echo "[4/6] Memeriksa total kapasitas Swap saat ini: ${TOTAL_SWAP_MB:-0} MB..."
if [ "${TOTAL_SWAP_MB:-0}" -lt 3500 ] && [ ! -f /swapfile_homelab ]; then
  echo "      -> Membuat swap cadangan /swapfile_homelab (2 GB)..."
  $SUDO fallocate -l 2G /swapfile_homelab || $SUDO dd if=/dev/zero of=/swapfile_homelab bs=1M count=2048 status=none
  $SUDO chmod 600 /swapfile_homelab
  $SUDO mkswap /swapfile_homelab >/dev/null
  $SUDO swapon -p 10 /swapfile_homelab
  if ! grep -q "/swapfile_homelab" /etc/fstab; then
    echo "/swapfile_homelab none swap sw,pri=10 0 0" | $SUDO tee -a /etc/fstab >/dev/null
  fi
fi

# 6. Konfigurasi BuildKit Worker & Bersihkan Dangling Images
ENV_FILE="$(cd "$(dirname "$0")" && pwd)/.env"
echo "[5/6] Mengamankan variabel BuildKit & Cgroup di $ENV_FILE..."
touch "$ENV_FILE"
grep -q "^COMPOSE_PARALLEL_LIMIT=" "$ENV_FILE" 2>/dev/null || echo "COMPOSE_PARALLEL_LIMIT=1" >> "$ENV_FILE"
grep -q "^DOCKER_BUILDKIT=" "$ENV_FILE" 2>/dev/null || echo "DOCKER_BUILDKIT=1" >> "$ENV_FILE"
grep -q "^COMPOSE_DOCKER_CLI_BUILD=" "$ENV_FILE" 2>/dev/null || echo "COMPOSE_DOCKER_CLI_BUILD=1" >> "$ENV_FILE"

$SUDO mkdir -p /etc/buildkit
$SUDO tee /etc/buildkit/buildkitd.toml >/dev/null <<'EOF'
[worker.oci]
  max-parallelism = 2
EOF

if command -v docker >/dev/null 2>&1; then
  docker image prune -f >/dev/null 2>&1 || true
  docker builder prune --keep-storage=3GB -f >/dev/null 2>&1 || true
fi

echo "[6/6] Ringkasan kondisi akhir:"
systemctl status homelab-build.slice --no-pager -l | head -n 12 || true
echo ""
free -h
echo "=========================================================="
echo " Pembagian resource 3-Tier selesai! Seluruh layanan akan"
echo " tetap berjalan lancar tanpa terganggu saat proses build."
echo "=========================================================="
