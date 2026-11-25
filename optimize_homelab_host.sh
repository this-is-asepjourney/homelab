#!/usr/bin/env bash
# =============================================================================
# Homelab Host Performance & Memory Optimization Script (Ubuntu Server)
# Aman dijalankan berulang kali (idempotent) — TIDAK menghapus volume data.
# =============================================================================
set -euo pipefail

echo "=========================================================="
echo " Homelab Ubuntu Server Performance & Memory Optimizer"
echo "=========================================================="

if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

# 1. Kernel VM / Swap / Page-Cache Tuning
# Mencegah kernel agresif memindahkan memori aplikasi/database ke disk swap
# (terutama pada server RAM 4-8 GB).
SYSCTL_FILE="/etc/sysctl.d/99-homelab-performance.conf"
echo "[1/4] Mengatur kernel sysctl ($SYSCTL_FILE)..."
$SUDO tee "$SYSCTL_FILE" >/dev/null <<'EOF'
# Kurangi kecenderungan swap agar proses aktif (DB, API, SSH) tetap di RAM fisik
vm.swappiness = 20
# Pertahankan inode/dentry cache agar operasi filesystem Docker & Git lebih cepat
vm.vfs_cache_pressure = 50
# Hindari burst write besar yang membekukan I/O disk sekaligus saat docker build
vm.dirty_background_ratio = 5
vm.dirty_ratio = 15
EOF
$SUDO sysctl --system >/dev/null 2>&1 || true
echo "      -> vm.swappiness=20, vm.vfs_cache_pressure=50, dirty_ratio=15 aktif."

# 2. Pastikan Swap minimal 4 GB tersedia sebagai jaring pengaman OOM saat build
TOTAL_SWAP_MB=$(free -m | awk '/^Swap:/ {print $2}')
echo "[2/4] Memeriksa kapasitas Swap saat ini: ${TOTAL_SWAP_MB:-0} MB..."
if [ "${TOTAL_SWAP_MB:-0}" -lt 3500 ] && [ ! -f /swapfile_homelab ]; then
  echo "      -> Membuat swap tambahan /swapfile_homelab (2 GB) agar aman dari OOM..."
  $SUDO fallocate -l 2G /swapfile_homelab || $SUDO dd if=/dev/zero of=/swapfile_homelab bs=1M count=2048 status=none
  $SUDO chmod 600 /swapfile_homelab
  $SUDO mkswap /swapfile_homelab >/dev/null
  $SUDO swapon /swapfile_homelab
  if ! grep -q "/swapfile_homelab" /etc/fstab; then
    echo "/swapfile_homelab none swap sw 0 0" | $SUDO tee -a /etc/fstab >/dev/null
  fi
  echo "      -> /swapfile_homelab aktif."
else
  echo "      -> Kapasitas Swap sudah memadai."
fi

# 3. Bersihkan dangling Docker images & cache build lama (TANPA menyentuh volume!)
echo "[3/4] Membersihkan dangling images & BuildKit cache usang (data volume AMAN)..."
if command -v docker >/dev/null 2>&1; then
  docker image prune -f >/dev/null 2>&1 || true
  docker builder prune --keep-storage=2GB -f >/dev/null 2>&1 || true
  echo "      -> Dangling images & cache build berlebih dibersihkan (simpan 2 GB cache terbaru)."
fi

# 4. Ringkasan kondisi akhir
echo "[4/4] Status memori & disk saat ini:"
free -h
echo ""
df -h /
echo "=========================================================="
echo " Optimasi host selesai! Lanjutkan dengan:"
echo "   docker compose up -d --build psm-api psm-agent psm-web"
echo "   docker compose up -d"
echo "=========================================================="
