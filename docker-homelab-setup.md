# Setup Docker Homelab: Nextcloud, Prometheus, Grafana, MySQL, PostgreSQL

Panduan lengkap dari nol sampai jalan di Ubuntu Server. Semua command siap copy-paste.

| Item | Nilai |
|---|---|
| IP LAN server | `192.168.25.105` |
| IP Tailscale | `100.78.113.53` |
| OS | Ubuntu Server 22.04 / 24.04 |
| Folder kerja | `~/homelab` |

## Daftar Port

| Service | Port | Akses LAN | Akses Tailscale |
|---|---|---|---|
| Nextcloud | 8080 | http://192.168.25.105:8080 | http://100.78.113.53:8080 |
| Grafana | 3000 | http://192.168.25.105:3000 | http://100.78.113.53:3000 |
| Prometheus | 9090 | http://192.168.25.105:9090 | http://100.78.113.53:9090 |
| MySQL | 3306 | 192.168.25.105:3306 | 100.78.113.53:3306 |
| PostgreSQL | 5432 | 192.168.25.105:5432 | 100.78.113.53:5432 |
| Node Exporter | 9100 | internal | internal |
| cAdvisor | 8081 | http://192.168.25.105:8081 | http://100.78.113.53:8081 |

> Catatan: Nextcloud memakai database PostgreSQL (database `nextcloud`), sedangkan MySQL disediakan terpisah untuk aplikasi Laravel/CodeIgniter kamu.

---

## 1. Update Sistem & Tool Dasar

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ca-certificates curl gnupg ufw nano htop git
```

## 2. Install Docker Engine + Compose Plugin

```bash
# Hapus paket lama (aman kalau belum ada)
for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
  sudo apt remove -y $pkg 2>/dev/null
done

# Tambah GPG key & repo resmi Docker
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Jalan otomatis saat boot
sudo systemctl enable --now docker

# Pakai docker tanpa sudo (logout/login ulang setelah ini)
sudo usermod -aG docker $USER
newgrp docker
```

Verifikasi:

```bash
docker --version
docker compose version
docker run --rm hello-world
```

## 3. Install Tailscale (jika belum)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4      # harus menampilkan 100.78.113.53
```

## 4. Buat Struktur Folder

```bash
mkdir -p ~/homelab/{prometheus,grafana/provisioning/datasources,postgres-init}
cd ~/homelab
```

Struktur akhir:

```
~/homelab
├── .env
├── docker-compose.yml
├── prometheus/
│   └── prometheus.yml
├── grafana/
│   └── provisioning/datasources/datasource.yml
└── postgres-init/
    └── 01-nextcloud.sql
```

## 5. File `.env` (kredensial)

```bash
cat > .env << 'EOF'
# ===== Umum =====
TZ=Asia/Jakarta
SERVER_LAN_IP=192.168.25.105
SERVER_TS_IP=100.78.113.53

# ===== MySQL =====
MYSQL_ROOT_PASSWORD=ganti_root_password_mysql
MYSQL_USER=fadb
MYSQL_PASSWORD=wokeh123
MYSQL_DATABASE=fadb

# ===== PostgreSQL =====
POSTGRES_USER=fadb
POSTGRES_PASSWORD=wokeh123
POSTGRES_DB=fadb

# ===== Nextcloud =====
NEXTCLOUD_ADMIN_USER=fadb
NEXTCLOUD_ADMIN_PASSWORD=wokeh123
NEXTCLOUD_DB_NAME=nextcloud

# ===== Grafana =====
GRAFANA_ADMIN_USER=fadb
GRAFANA_ADMIN_PASSWORD=wokeh123
EOF

chmod 600 .env
```

> Ganti `MYSQL_ROOT_PASSWORD` dengan password root yang kuat. Karena port database dibuka ke jaringan, sebaiknya password `wokeh123` diganti dengan yang lebih kuat setelah semuanya berjalan (lihat bagian Keamanan).

## 6. File `docker-compose.yml`

```bash
cat > docker-compose.yml << 'EOF'
name: homelab

services:
  # ---------- MySQL ----------
  mysql:
    image: mysql:8.4
    container_name: mysql
    restart: unless-stopped
    environment:
      TZ: ${TZ}
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: ${MYSQL_DATABASE}
      MYSQL_USER: ${MYSQL_USER}
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
    ports:
      - "3306:3306"
    volumes:
      - mysql_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-uroot", "-p${MYSQL_ROOT_PASSWORD}"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks: [homelab]

  # ---------- PostgreSQL ----------
  postgres:
    image: postgres:16-alpine
    container_name: postgres
    restart: unless-stopped
    environment:
      TZ: ${TZ}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres-init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks: [homelab]

  # ---------- Nextcloud ----------
  nextcloud:
    image: nextcloud:stable
    container_name: nextcloud
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
    environment:
      TZ: ${TZ}
      POSTGRES_HOST: postgres
      POSTGRES_DB: ${NEXTCLOUD_DB_NAME}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      NEXTCLOUD_ADMIN_USER: ${NEXTCLOUD_ADMIN_USER}
      NEXTCLOUD_ADMIN_PASSWORD: ${NEXTCLOUD_ADMIN_PASSWORD}
      NEXTCLOUD_TRUSTED_DOMAINS: "${SERVER_LAN_IP} ${SERVER_TS_IP} localhost"
      REDIS_HOST: redis
      PHP_MEMORY_LIMIT: 1G
      PHP_UPLOAD_LIMIT: 2G
    ports:
      - "8080:80"
    volumes:
      - nextcloud_data:/var/www/html
    networks: [homelab]

  redis:
    image: redis:7-alpine
    container_name: redis
    restart: unless-stopped
    networks: [homelab]

  # ---------- Prometheus ----------
  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    restart: unless-stopped
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--storage.tsdb.retention.time=30d"
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    networks: [homelab]

  node-exporter:
    image: prom/node-exporter:latest
    container_name: node-exporter
    restart: unless-stopped
    pid: host
    command:
      - "--path.rootfs=/host"
    volumes:
      - /:/host:ro,rslave
    networks: [homelab]

  cadvisor:
    image: gcr.io/cadvisor/cadvisor:latest
    container_name: cadvisor
    restart: unless-stopped
    privileged: true
    ports:
      - "8081:8080"
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro
    networks: [homelab]

  # ---------- Grafana ----------
  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    restart: unless-stopped
    depends_on: [prometheus]
    environment:
      TZ: ${TZ}
      GF_SECURITY_ADMIN_USER: ${GRAFANA_ADMIN_USER}
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      GF_USERS_ALLOW_SIGN_UP: "false"
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
    networks: [homelab]

volumes:
  mysql_data:
  postgres_data:
  nextcloud_data:
  prometheus_data:
  grafana_data:

networks:
  homelab:
    driver: bridge
EOF
```

## 7. File Konfigurasi Pendukung

### Prometheus

```bash
cat > prometheus/prometheus.yml << 'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["prometheus:9090"]

  - job_name: node
    static_configs:
      - targets: ["node-exporter:9100"]

  - job_name: cadvisor
    static_configs:
      - targets: ["cadvisor:8080"]
EOF
```

### Grafana (datasource otomatis)

```bash
cat > grafana/provisioning/datasources/datasource.yml << 'EOF'
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
EOF
```

### PostgreSQL (buat database Nextcloud saat pertama kali jalan)

```bash
cat > postgres-init/01-nextcloud.sql << 'EOF'
CREATE DATABASE nextcloud;
EOF
```

> File init hanya dijalankan **sekali**, saat volume PostgreSQL masih kosong. Jika sudah terlanjur jalan, buat manual: `docker exec -it postgres psql -U fadb -d fadb -c "CREATE DATABASE nextcloud;"`

## 8. Jalankan Semua Service

```bash
cd ~/homelab
docker compose config -q && echo "Compose valid"
docker compose pull
docker compose up -d
docker compose ps
```

Lihat log jika ada yang bermasalah:

```bash
docker compose logs -f nextcloud
docker compose logs -f mysql
```

> Nextcloud butuh sekitar 1-3 menit untuk instalasi awal. Tunggu sampai log menampilkan Apache berjalan, baru buka browser.

## 9. Firewall (UFW)

Batasi akses hanya dari LAN dan Tailscale.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing

sudo ufw allow OpenSSH

# Izinkan dari LAN
for p in 8080 3000 9090 8081 3306 5432; do
  sudo ufw allow from 192.168.25.0/24 to any port $p proto tcp
done

# Izinkan dari Tailscale
sudo ufw allow in on tailscale0

sudo ufw enable
sudo ufw status numbered
```

> Docker mem-publish port lewat iptables sendiri dan bisa melewati UFW. Karena itu, kalau mau benar-benar ketat, bind port database ke IP tertentu, misalnya `"192.168.25.105:3306:3306"` dan `"100.78.113.53:3306:3306"` di compose, daripada `"3306:3306"`.

## 10. Verifikasi Tiap Service

### Nextcloud
Buka `http://192.168.25.105:8080` atau `http://100.78.113.53:8080`, login `fadb` / `wokeh123`.

Jika muncul "Access through untrusted domain":

```bash
docker exec -u www-data nextcloud php occ config:system:set trusted_domains 3 --value=192.168.25.105
docker exec -u www-data nextcloud php occ config:system:set trusted_domains 4 --value=100.78.113.53
```

### Grafana
Buka `http://192.168.25.105:3000`, login `fadb` / `wokeh123`. Datasource Prometheus sudah otomatis ada. Import dashboard:
- **Node Exporter Full**: ID `1860`
- **cAdvisor / Docker**: ID `193`

Caranya: Dashboards, New, Import, masukkan ID, pilih datasource Prometheus.

### Prometheus
Buka `http://192.168.25.105:9090/targets`, semua target harus berstatus **UP**.

### MySQL

```bash
docker exec -it mysql mysql -ufadb -pwokeh123 -e "SHOW DATABASES;"
```

Dari komputer lain (misalnya Laravel):

```env
DB_CONNECTION=mysql
DB_HOST=192.168.25.105      # atau 100.78.113.53 via Tailscale
DB_PORT=3306
DB_DATABASE=fadb
DB_USERNAME=fadb
DB_PASSWORD=wokeh123
```

### PostgreSQL

```bash
docker exec -it postgres psql -U fadb -d fadb -c "\l"
```

Dari aplikasi:

```env
DB_CONNECTION=pgsql
DB_HOST=192.168.25.105
DB_PORT=5432
DB_DATABASE=fadb
DB_USERNAME=fadb
DB_PASSWORD=wokeh123
```

## 11. Perintah Harian

```bash
cd ~/homelab

docker compose ps                  # status
docker compose logs -f --tail=100  # log semua service
docker compose restart grafana     # restart satu service
docker compose stop                # stop semua
docker compose up -d               # start semua

# Update image ke versi terbaru
docker compose pull && docker compose up -d
docker image prune -f
```

## 12. Backup

```bash
mkdir -p ~/backup

# MySQL
docker exec mysql sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --all-databases' \
  > ~/backup/mysql-$(date +%F).sql

# PostgreSQL
docker exec postgres pg_dumpall -U fadb > ~/backup/postgres-$(date +%F).sql

# Volume Nextcloud, Grafana
docker run --rm -v homelab_nextcloud_data:/data -v ~/backup:/backup alpine \
  tar czf /backup/nextcloud-$(date +%F).tar.gz -C /data .
docker run --rm -v homelab_grafana_data:/data -v ~/backup:/backup alpine \
  tar czf /backup/grafana-$(date +%F).tar.gz -C /data .
```

Otomatis tiap hari jam 02:00 lewat cron: `crontab -e`, lalu tambahkan baris:

```
0 2 * * * docker exec postgres pg_dumpall -U fadb > /home/$USER/backup/postgres-$(date +\%F).sql
```

## 13. Keamanan (Disarankan)

1. Ganti password `wokeh123` setelah semua berjalan. Password ini singkat dan sudah tertulis di dokumen ini. Setelah mengubah `.env`, untuk MySQL/PostgreSQL password di dalam database juga harus diubah:
   ```bash
   docker exec -it postgres psql -U fadb -c "ALTER USER fadb PASSWORD 'password_baru_kuat';"
   docker exec -it mysql mysql -uroot -p -e "ALTER USER 'fadb'@'%' IDENTIFIED BY 'password_baru_kuat';"
   ```
2. Jangan port-forward 3306 / 5432 di router ke internet. Akses dari luar rumah cukup via Tailscale.
3. Jangan commit `.env` ke Git: `echo ".env" >> .gitignore`
4. Aktifkan HTTPS untuk Nextcloud jika diakses publik (reverse proxy seperti Caddy/Nginx Proxy Manager atau `tailscale serve`).

## 14. Troubleshooting

| Masalah | Solusi |
|---|---|
| `permission denied` saat `docker` | `sudo usermod -aG docker $USER`, lalu logout/login |
| Port sudah dipakai | `sudo ss -tulpn \| grep :PORT`, ganti port di compose |
| Nextcloud "untrusted domain" | Jalankan perintah `occ trusted_domains` di bagian 10 |
| Database `nextcloud` tidak ada | Buat manual (lihat catatan bagian 7) |
| Nextcloud error koneksi DB | Cek `docker compose logs postgres`, pastikan healthy |
| Prometheus target DOWN | `docker compose logs prometheus`, cek nama service di `prometheus.yml` |
| Grafana login gagal setelah ganti password di `.env` | Password hanya terbaca saat pertama kali. Reset: `docker exec -it grafana grafana cli admin reset-admin-password PASSWORD_BARU` |
| Ubah `.env` tapi tidak berlaku | `docker compose up -d --force-recreate <service>` |
| Reset total (HAPUS SEMUA DATA) | `docker compose down -v` |

---

## 15. Prompt untuk AI (Claude / ChatGPT / Agent)

Salin prompt di bawah jika ingin meminta AI membantu setup, debugging, atau memodifikasi stack ini.

```text
Kamu adalah DevOps engineer senior yang membantu saya menyiapkan homelab berbasis Docker.

KONTEKS
- Server: mini PC, Ubuntu Server (22.04/24.04)
- IP LAN: 192.168.25.105
- IP Tailscale: 100.78.113.53
- Folder kerja: ~/homelab
- Tool: Docker Engine + Docker Compose plugin (v2)

SERVICE YANG HARUS ADA
1. Nextcloud (port 8080), memakai PostgreSQL + Redis
2. Prometheus (port 9090) + node-exporter + cAdvisor
3. Grafana (port 3000), datasource Prometheus otomatis (provisioning)
4. MySQL 8.4 (port 3306)
5. PostgreSQL 16 (port 5432)

KREDENSIAL
- MySQL: user fadb, password wokeh123 (root password terpisah)
- PostgreSQL: user fadb, password wokeh123
- Nextcloud dan Grafana admin: fadb / wokeh123
- Semua kredensial disimpan di file .env, bukan hardcode di compose

PERSYARATAN
- Semua service restart: unless-stopped, memakai named volume agar data persisten
- Healthcheck untuk MySQL dan PostgreSQL, Nextcloud menunggu database healthy
- NEXTCLOUD_TRUSTED_DOMAINS berisi kedua IP di atas
- Akses hanya dari LAN 192.168.25.0/24 dan interface tailscale0 (konfigurasi UFW)
- Timezone Asia/Jakarta

OUTPUT YANG SAYA MINTA
1. Command instalasi Docker dari repo resmi (siap copy-paste)
2. File lengkap: .env, docker-compose.yml, prometheus.yml, datasource Grafana, init SQL PostgreSQL
3. Command menjalankan, verifikasi, backup, dan update
4. Troubleshooting untuk error umum
5. Catatan keamanan yang relevan

FORMAT
- Bahasa Indonesia, langkah berurutan, tiap command dalam code block
- Jelaskan singkat tujuan tiap langkah
- Jangan lewati langkah verifikasi
```

### Prompt tambahan (opsional)

**Tambah exporter database ke Prometheus:**

```text
Tambahkan mysqld-exporter dan postgres-exporter ke docker-compose.yml homelab saya,
daftarkan sebagai scrape job di prometheus.yml, lalu beri ID dashboard Grafana yang cocok.
Pakai user read-only khusus monitoring, bukan user fadb.
```

**Tambah Uptime Kuma + MQTT untuk IoT:**

```text
Tambahkan Uptime Kuma (port 3001) dan Eclipse Mosquitto (MQTT port 1883) ke stack homelab
yang sudah ada, termasuk file mosquitto.conf dengan autentikasi user/password dan
contoh publish/subscribe untuk test.
```

**Debugging:**

```text
Berikut output `docker compose ps` dan `docker compose logs --tail=100 <service>` saya:
[tempel di sini]
Tolong analisis penyebabnya dan beri langkah perbaikan yang berurutan.
```
