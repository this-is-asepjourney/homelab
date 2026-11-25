# Panduan Operasional Server & Fitur Homelab

Manual operasional lengkap untuk mengelola server Ubuntu homelab (`fa`, direktori kerja `~/homelab`) dan menggunakan seluruh layanan serta fitur yang sudah terpasang di [docker-compose.yml](docker-compose.yml).

---

## 1. Ringkasan Server & Tabel Akses Layanan Lengkap

| Parameter | Nilai |
|---|---|
| Hostname Server | `fa` (Ubuntu Server) |
| Direktori Project | `~/homelab` |
| IP LAN | `192.168.25.105` |
| IP Tailscale (VPN) | `100.78.113.53` |
| Zona Waktu (`TZ`) | `Asia/Jakarta` |
| Docker Network | `homelab` (bridge) |

### 1.1 Daftar 19 Layanan Terpasang (`docker-compose.yml`)

| Service | Container | Port Host | Akses LAN | Akses Tailscale | Domain `.homelab` | Hostname Internal Docker | Fungsi |
|---|---|---|---|---|---|---|---|
| `psm-web` | `psm-web` | `3005` | `http://192.168.25.105:3005` | `http://100.78.113.53:3005` | `http://psm.homelab` | `psm-web:3005` | UI Dashboard Personal Server Manager |
| `psm-api` | `psm-api` | `8001` | `http://192.168.25.105:8001` | `http://100.78.113.53:8001` | `http://psm-api.homelab` | `psm-api:8000` / `api:8000` | Control Plane API PSM |
| `psm-agent` | `psm-agent` | — | Internal | Internal | — | `psm-agent` | Agent eksekusi Docker, build & deploy project |
| `pap-api` | `pap-api` | `8000` | `http://192.168.25.105:8000` | `http://100.78.113.53:8000` | `http://homelab` / `http://api.homelab` | `pap-api:8000` | Personal API Platform Core Gateway & IoT Consumer |
| `minio` | `minio` | `9000`, `9002` | `:9000` (S3), `:9002` (UI) | `:9000` (S3), `:9002` (UI) | `http://s3.homelab`, `http://minio.homelab` | `minio:9000` (`silo`, `homelab_minio`) | S3 Object Storage (`pgsty/silo:latest`) |
| `minio-init` | `minio-init` | — | One-shot init | One-shot init | — | — | Inisialisasi bucket default (`pgsty/mc:latest`) |
| `nextcloud` | `nextcloud` | `8080` | `http://192.168.25.105:8080` | `http://100.78.113.53:8080` | `http://nextcloud.homelab` | `nextcloud:80` | Cloud Drive & Kolaborasi File |
| `grafana` | `grafana` | `3000` | `http://192.168.25.105:3000` | `http://100.78.113.53:3000` | `http://grafana.homelab` | `grafana:3000` | Dashboard Observability & IoT |
| `prometheus` | `prometheus` | `9090` | `http://192.168.25.105:9090` | `http://100.78.113.53:9090` | `http://prometheus.homelab` | `prometheus:9090` | Time-Series Metrics Database (retensi 30 hari) |
| `node-exporter` | `node-exporter` | — | Internal (`:9100`) | Internal | — | `node-exporter:9100` | Metrik CPU/RAM/Disk host Ubuntu |
| `cadvisor` | `cadvisor` | `8081` | `http://192.168.25.105:8081` | `http://100.78.113.53:8081` | `http://cadvisor.homelab` | `cadvisor:8080` | Metrik resource per container Docker |
| `uptime-kuma` | `uptime-kuma` | `3001` | `http://192.168.25.105:3001` | `http://100.78.113.53:3001` | `http://kuma.homelab` / `uptime.homelab` | `uptime-kuma:3001` | Pemantauan uptime & notifikasi status layanan |
| `mosquitto` | `mosquitto` | `1883` | `192.168.25.105:1883` | `100.78.113.53:1883` | — | `mosquitto:1883` | Broker MQTT untuk perangkat ESP32 / IoT |
| `telegraf` | `telegraf` | `9273` | `192.168.25.105:9273` | `100.78.113.53:9273` | — | `telegraf:9273` | Subscriber MQTT → ekspos metrik ke Prometheus |
| `postgres` | `postgres` | `5432` | `192.168.25.105:5432` | `100.78.113.53:5432` | — | `postgres:5432` | PostgreSQL 16 (`nextcloud`, `pap_core`, `psm`, `eternal_iot`) |
| `mysql` | `mysql` | `3306` | `192.168.25.105:3306` | `100.78.113.53:3306` | — | `mysql:3306` | MySQL 8.4 (`fadb` dan database app seperti `stock_umkm`) |
| `redis` | `redis` | — | Internal (`:6379`) | Internal | — | `redis:6379` | Redis 7 cache & session backend |
| `homelab-router` | `homelab_router` | `80` | `http://192.168.25.105` | `http://100.78.113.53` | Semua `*.homelab` | `homelab-router:80` | Nginx Reverse Proxy & halaman Maintenance 503 |
| `homelab-dns` | `homelab_dns` | `53` (LAN) | `192.168.25.105:53` | — | `*.homelab` & `*.tailscale.homelab` | `homelab-dns:53` | CoreDNS resolver domain lokal homelab |

### 1.2 Kredensial Default

> Semua kredensial berikut diambil dari nilai default di `docker-compose.yml`, `Personal_Server_Manager/apps/api/app/main.py`, dan `Personal_Api_Platform/app/core/config.py`. Jika kamu menggantinya di file `.env` server, gunakan nilai `.env` milikmu.

| Layanan | Username / Email | Password / Token Default | Catatan |
|---|---|---|---|
| **PSM Web (`:3005`)** | `admin@psm.local` | `Admin12345!` | Role `owner` (auto-seed saat start) |
| **Grafana (`:3000`)** | `fadb` | `wokeh123` | Variabel `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` |
| **Nextcloud (`:8080`)** | `fadb` | `wokeh123` | Variabel `NEXTCLOUD_ADMIN_USER` / `NEXTCLOUD_ADMIN_PASSWORD` |
| **MinIO (`:9002` / `:9000`)** | `admin` | `admin12345` | Variabel `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |
| **PostgreSQL 16 (`:5432`)** | `fadb` | `wokeh123` | Superuser default |
| **MySQL 8.4 (`:3306`)** | `fadb` *(root: `root`)* | `wokeh123` *(root: lihat `MYSQL_ROOT_PASSWORD`)* | User aplikasi `fadb` |
| **Mosquitto MQTT (`:1883`)** | `useriot` | `25022002` | `allow_anonymous true` aktif di `mosquitto.conf` |
| **PSM Agent Token** | — | `psm_agent_homelab_default_token` | Variabel `HOMELAB_AGENT_TOKEN` |
| **PAP Service Keys** | — | `pap_key_psm_control_plane_default_secret_key_2026` | Header `X-API-Key` / Bearer |

---

## 2. Operasional Harian Server (Docker Compose)

Selalu masuk ke folder kerja sebelum menjalankan perintah `docker compose`:

```bash
cd ~/homelab
```

### 2.1 Mengecek Status & Log Layanan
```bash
# Lihat status semua container
docker compose ps

# Lihat penggunaan CPU/RAM real-time
docker stats --no-stream

# Lihat log layanan tertentu (mis. grafana, pap-api, psm-api, psm-agent, telegraf)
docker logs --tail 80 pap-api
docker logs -f psm-agent
```

### 2.2 Menjalankan, Restart, & Menghentikan Layanan
```bash
# Menyalakan seluruh stack
docker compose up -d

# Menyalakan atau me-restart layanan spesifik saja
docker compose up -d minio minio-init
docker compose restart grafana
docker compose up -d --force-recreate homelab-router

# Menghentikan satu layanan tanpa menghapus container/data
docker compose stop nextcloud
```

> **PERINGATAN KERAS — DATA VOLUME:**
> **JANGAN PERNAH** menjalankan `docker compose down -v`! Flag `-v` akan menghapus **seluruh 10 persistent volume** (`mysql_data`, `postgres_data`, `redis_data`, `minio_data`, `nextcloud_data`, `prometheus_data`, `grafana_data`, `uptime_kuma_data`, `mosquitto_data`, `psm_storage`), yang berarti seluruh database aplikasi, file Nextcloud, gambar MinIO, dan data monitoring akan hilang permanen.

### 2.3 Update Kode dari GitHub & Rebuild Selektif
```bash
cd ~/homelab
git pull origin main

# (Direkomendasikan 1x di awal) Optimasi kernel swap/cache host Ubuntu & bersihkan build cache lama
chmod +x optimize_homelab_host.sh
./optimize_homelab_host.sh

# Rebuild layanan backend & agent dengan Resource Guard baru
docker compose up -d --build pap-api psm-api psm-agent

# Jika mengubah UI Next.js (psm-web, sudah mendukung standalone hemat RAM), jalankan dengan nice/nohup
nohup nice -n 15 ionice -c 2 -n 7 docker compose up -d --build psm-web > /tmp/build-psm-web.log 2>&1 &
tail -f /tmp/build-psm-web.log
```

### 2.4 Cek Kesehatan Cepat (One-Liner)
```bash
for url in \
  "http://127.0.0.1:8000/health" \
  "http://127.0.0.1:8001/health" \
  "http://127.0.0.1:3000/api/health" \
  "http://127.0.0.1:9000/minio/health/live" \
  "http://127.0.0.1:9090/-/healthy"; do
  echo "=== $url ==="
  curl -s -o /dev/null -w "HTTP %{http_code}\n" "$url"
done
```

---

## 3. Menggunakan Personal Server Manager (`PSM` — `:3005` / `psm.homelab`)

Buka **`http://100.78.113.53:3005`** (Tailscale) atau **`http://192.168.25.105:3005`** (LAN), lalu login dengan `admin@psm.local` / `Admin12345!`.

### 3.1 Peta Menu Dashboard PSM
- **`/dashboard`** & **`/mission-control`**: Ringkasan server, metrik CPU/RAM/Disk, status container aktif, dan kendali cepat.
- **`/projects`**: Daftar aplikasi yang dideploy, tombol deploy baru, detail project (`/projects/<id>`), konfigurasi `.env`, database, domain, dan Maintenance Mode.
- **`/deployments`**: Riwayat build & log deployment real-time.
- **`/servers`**: Daftar node server (default `Homelab-Primary`) & status koneksi `psm-agent`.
- **`/storage`**: Telemetri disk & Docker volume, daftar database + keterhubungannya ke project aktif, info & snippet `.env` **MinIO S3**, serta fitur **Backup & Restore** snapshot.
- **`/monitoring`**, **`/network`**, **`/templates`**, **`/activity`**, **`/settings`**: Grafik metrik, status jaringan/ingress, template deploy, log audit aktivitas, dan pengaturan sistem.

### 3.2 Deploy Project Baru (Git / ZIP)
1. Masuk ke **`Projects`** → klik **Deploy Project**.
2. Pilih sumber kode: **Git Repository** (isi URL & branch) atau **Upload ZIP**.
3. **Alokasi Port Aman Otomatis**: Endpoint `/api/v1/projects/next-port` otomatis mencarikan port bebas mulai `:3100` dan melindungi seluruh port sistem homelab (termasuk `:3000` Grafana, `:9000`/`:9002` MinIO, `:8000` PAP, `:8001` PSM, `:3306` MySQL, `:5432` Postgres). Jika terjadi bentrok port saat deploy, `psm-agent` juga memiliki deteksi & pemulihan otomatis ke port kosong berikutnya.
4. **Auto-Wiring Database & MinIO**:
   - Pilih engine database (`postgres`, `mysql`, `sqlite`, atau `none`).
   - Jika project memiliki `FILESYSTEM_DISK=s3` atau `AWS_BUCKET` di `.env` / `.env.example`, `psm-agent` otomatis membuatkan bucket di MinIO (`myminio/<bucket>`) dengan akses baca publik dan menyuntikkan variabel `AWS_*`.
5. **Domain Ingress Otomatis**: Isi subdomain `.homelab` (mis. `stock-umkm.homelab`). Nginx `homelab-router` dan `dns/homelab.hosts` akan otomatis diperbarui melalui PAP DNS service.

### 3.3 Mengelola Environment Variables (`.env`) Project
Di halaman detail project (`/projects/<id>`):
- **Tambah / Edit / Hapus Satuan**: Tambah atau ubah key individu tanpa menimpa key lain; nilai sensitif tetap aman (bisa dibuka dengan tombol *Reveal*).
- **Bulk `.env` Editor, Import & Download**: Paste seluruh isi `.env` sekaligus, unggah file `.env`, atau unduh `.env` aktif.
- **Auto-Diagnostics & Setup Ulang Env Wizard**:
  - Jika ada kesalahan konfigurasi umum (misal `APP_ENV=production` pada Laravel HTTP yang memaksa `https://` aset CSS, atau `DB_HOST=127.0.0.1` alih-alih `mysql`/`postgres`), banner diagnostik akan muncul.
  - Gunakan tombol **Setup Ulang Env** (`repair` untuk memperbaiki tanpa menghapus key kustom, atau `clean` untuk reset bersih ke konfigurasi standar container).

### 3.4 Mode Maintenance Project (HTTP 503)
Gunakan fitur ini saat project sedang diperbarui atau diperbaiki agar pengunjung melihat halaman maintenance rapi (bukan error `502 Bad Gateway`):
1. Buka **`Projects` → pilih project → tab `Settings`**.
2. Aktifkan toggle **Maintenance Mode**.
3. Isi **Judul**, **Pesan**, dan **Estimasi Selesai (ETA)**, lalu klik **Preview** jika ingin melihat tampilannya sebelum disimpan.
4. Klik **Simpan**:
   - Nginx `homelab-router` langsung mengembalikan status **HTTP 503** dengan halaman statis maintenance untuk domain `.homelab` project tersebut (`router/conf.d/maintenance`).
   - Jika container merupakan aplikasi **Laravel**, `psm-api` juga menyinkronkan status maintenance ke `storage/framework/down` di dalam container.
5. Matikan kembali toggle untuk mengembalikan aplikasi online.

### 3.5 Backup & Restore Snapshot (`/storage`)
Di menu **`Storage`**, kamu bisa membuat snapshot database/konfigurasi (`POST /backups`), melihat daftar Docker volume (`IN-USE` vs `UNUSED`), memeriksa relasi Database ↔ Project, serta melakukan **Restore** atau hapus snapshot lama.

---

## 4. Menggunakan Personal API Platform (`PAP` — `:8000` / `api.homelab`)

`pap-api` adalah pusat gateway API, manajemen klien/API key, registry database, DNS/Nginx vhost, serta pemroses data IoT.

- **Swagger / OpenAPI Docs**: `http://100.78.113.53:8000/docs` (atau `http://192.168.25.105:8000/docs`)
- **Health Check**: `GET /health`
- **Prometheus Metrics**: `GET /metrics`
- **Modul Aktif**: `auth`, `clients`, `permissions`, `projects`, `database_registry`, `dns`, `docker`, `iot`, `metrics`, `monitoring`, `automation`, `backups`, `webhooks`, `audit`, `traffic`, `infra`, `integrations`, `server`, `users`.
- **Project Terhubung (Auto-seeded)**: `stock-umkm`, `pos-cashier`, `iot-dashboard`, dan `personal-server-manager`.
- **Sinkronisasi DNS & VHost Manual via API/CLI**:
  ```bash
  # Cek status service PAP
  curl -s http://127.0.0.1:8000/health
  ```

---

## 5. Menggunakan MinIO / Silo S3 Object Storage (`:9000` API & `:9002` Console)

Server menggunakan **`pgsty/silo:latest`** + **`pgsty/mc:latest`** (fork MinIO kompatibel penuh S3 API beserta Web Admin Console).

### 5.1 Akses & Bucket Bawaan
- **Web Console**: `http://100.78.113.53:9002` | `http://192.168.25.105:9002` | `http://minio.homelab`
- **S3 API Endpoint**:
  - Dari sesama container Docker: `http://minio:9000`
  - Dari luar Docker (browser/HP/laptop): `http://100.78.113.53:9000` (Tailscale), `http://192.168.25.105:9000` (LAN), atau `http://s3.homelab`
- **Login Console**: `admin` / `admin12345`
- **Bucket Otomatis (`minio-init`)**:
  - `images` — **Public read-only (`download`)**, siap untuk URL gambar langsung (`<img src="http://100.78.113.53:9000/images/nama-file.jpg">`)
  - `stock-umkm` — **Public read-only (`download`)** untuk gambar produk `stock_umkm`
  - `homelab-storage` — **Public read-only (`download`)** untuk aset umum
  - `backups` — **Private** (hanya dapat diakses menggunakan Access Key)

### 5.2 Contoh Konfigurasi `.env` Aplikasi

**Laravel (`stock_umkm`):**
*(Pastikan package S3 Laravel terpasang: `composer require league/flysystem-aws-s3-v3`)*
```env
FILESYSTEM_DISK=s3
AWS_ACCESS_KEY_ID=admin
AWS_SECRET_ACCESS_KEY=admin12345
AWS_DEFAULT_REGION=us-east-1
AWS_BUCKET=stock-umkm
AWS_USE_PATH_STYLE_ENDPOINT=true
AWS_ENDPOINT=http://minio:9000
AWS_URL=http://100.78.113.53:9000/stock-umkm
```
*(Ganti `100.78.113.53` menjadi `192.168.25.105` jika aplikasi hanya diakses lewat LAN, atau `http://s3.homelab/stock-umkm` jika memakai DNS `.homelab`.)*

**Node.js / Next.js:**
```env
S3_ENDPOINT=http://minio:9000
S3_PUBLIC_URL=http://100.78.113.53:9000/images
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=admin
S3_SECRET_ACCESS_KEY=admin12345
S3_BUCKET=images
S3_FORCE_PATH_STYLE=true
```

**Python (`boto3`):**
```env
AWS_ACCESS_KEY_ID=admin
AWS_SECRET_ACCESS_KEY=admin12345
AWS_DEFAULT_REGION=us-east-1
S3_ENDPOINT_URL=http://minio:9000
S3_PUBLIC_URL=http://100.78.113.53:9000/images
S3_BUCKET_NAME=images
```

### 5.3 Mengelola Bucket via CLI di Server
```bash
# Buat bucket baru (mis. "produk-baru") dan buka akses download publik untuk gambar
docker exec minio sh -c 'MC=$(command -v mc || command -v mcli); $MC alias set myminio http://127.0.0.1:9000 admin admin12345 && $MC mb --ignore-existing myminio/produk-baru && $MC anonymous set download myminio/produk-baru'
```

---

## 6. Menggunakan Database Server (PostgreSQL 16, MySQL 8.4, Redis 7)

### 6.1 Aturan Hostname Koneksi
- **Aplikasi di dalam Docker (`stock_umkm`, `pap-api`, `psm-api`, dll.):** gunakan nama service (`postgres`, `mysql`, `redis`). **Jangan gunakan `127.0.0.1` atau `localhost`**.
- **Alat eksternal dari laptop (DBeaver / TablePlus / Navicat):** gunakan IP server `192.168.25.105` (LAN) atau `100.78.113.53` (Tailscale) dengan port `5432` (PostgreSQL) atau `3306` (MySQL).

### 6.2 Daftar Database Awal PostgreSQL (`postgres-init/01-databases.sql`)
- `fadb` (default database)
- `nextcloud` (Nextcloud)
- `pap_core` (Personal API Platform)
- `psm` (Personal Server Manager)
- `eternal_iot` (Schema & data perangkat IoT Eternal)

### 6.3 Perintah CLI, Backup & Restore Database
```bash
# Masuk shell PostgreSQL
docker exec -it postgres psql -U fadb -d postgres

# List seluruh database PostgreSQL
docker exec postgres psql -U fadb -l

# Backup & Restore PostgreSQL (contoh database psm)
docker exec postgres pg_dump -U fadb psm > ~/backup_psm_$(date +%F).sql
cat ~/backup_psm.sql | docker exec -i postgres psql -U fadb -d psm

# Masuk shell MySQL
docker exec -it mysql mysql -ufadb -pwokeh123

# Backup & Restore MySQL (contoh database stock_umkm)
docker exec mysql mysqldump -ufadb -pwokeh123 stock_umkm > ~/backup_stock_umkm_$(date +%F).sql
cat ~/backup_stock_umkm.sql | docker exec -i mysql mysql -ufadb -pwokeh123 stock_umkm

# Cek Redis CLI
docker exec -it redis redis-cli ping
```

---

## 7. Monitoring, Observability & Uptime

### 7.1 Grafana (`:3000` / `grafana.homelab`)
- **Login**: `fadb` / `wokeh123`
- **Datasource ter-provisioning otomatis** (`grafana/provisioning/datasources/datasource.yml`):
  - `Prometheus` (`uid: prometheus`, `http://prometheus:9090`, default)
  - `PostgreSQL-EternalIoT` (`uid: postgres-iot-db`, `postgres:5432`, DB `eternal_iot`)
  - `MySQL-Homelab` (`uid: mysql-iot-db`, `mysql:3306`, DB `${MYSQL_DATABASE}`)
- **Dashboard ter-provisioning**: `Eternal IoT — Smart Lock & Sensor` (`grafana/provisioning/dashboards/json/eternal-iot.json`).
- **Catatan Boot Pertama**: Setelah reset volume `homelab_grafana_data`, Grafana v13 menjalankan ~728 migrasi SQLite dan memuat 54 plugin yang membutuhkan waktu **~8–10 menit** sebelum port `3000` merespons. Pantau dengan `curl -fsS http://127.0.0.1:3000/api/health`.

### 7.2 Prometheus (`:9090` / `prometheus.homelab`)
- Menyimpan metrik selama **30 hari** (`--storage.tsdb.retention.time=30d`).
- **6 Scrape Targets** (`prometheus/prometheus.yml`): `prometheus`, `node` (`node-exporter:9100`), `cadvisor` (`cadvisor:8080`), `pap-core` (`pap-api:8000/metrics`), `psm-api` (`psm-api:8000/metrics`), `telegraf` (`telegraf:9273`).
- Cek status target lewat browser di `http://100.78.113.53:9090/targets` atau CLI:
  ```bash
  curl -s http://127.0.0.1:9090/api/v1/targets | grep -oE '"job":"[^"]+"|"health":"[^"]+"'
  ```

### 7.3 cAdvisor (`:8081`) & Uptime Kuma (`:3001`)
- **cAdvisor** (`http://100.78.113.53:8081` / `cadvisor.homelab`): pantau pemakaian CPU, RAM, network I/O tiap container secara detail.
- **Uptime Kuma** (`http://100.78.113.53:3001` / `kuma.homelab`): tambahkan monitor HTTP/TCP/Ping untuk setiap layanan/project dan atur notifikasi (Telegram/Discord/Webhook) jika ada service down.

---

## 8. Pipeline IoT Eternal (MQTT → Telegraf / PAP-API → PostgreSQL → Grafana)

Alur data perangkat ESP32 / Smart Lock:
1. Perangkat mengirim JSON ke broker **Mosquitto** (`192.168.25.105:1883` atau `100.78.113.53:1883`, kredensial `useriot` / `25022002`).
2. **Topik MQTT yang dipantau Telegraf (`telegraf/telegraf.conf`)**:
   - `+/SysCtrl`, `/SysCtrl`, `SysCtrl`, `Eternal/SysCtrl`
   - `/Eternal/telemetry`, `Eternal/telemetry`, `/Eternal/rfid`, `/Eternal/lock`, `/Eternal/#`, `Eternal/#`, `eternal/#`, `eternal/telemetry`, `/eternal/telemetry`
   - `+/Event/Cnt`, `+/Event/Handling`, `+/Event/Mchn_time`, `+/Heatpress`
3. **Telegraf** (`:9273`) menstandarkan tag (`MAC`, `MCID`, `model`) dan field (`wifi_rssi`, `uptime`, `device_status`, `nfc_taps_total`, `nfc_whitelist_users`, `mqtt_packets_total`, `database_sync_status`) untuk di-scrape oleh **Prometheus**.
4. **PAP-API** (`ENABLE_MQTT_CONSUMER=true`) mengonsumsi payload MQTT dan menyimpannya ke PostgreSQL database `eternal_iot`, yang ditampilkan oleh dashboard Grafana `Eternal IoT — Smart Lock & Sensor`.

### Inisialisasi / Sinkronisasi Ulang Pipeline IoT Eternal
Jalankan script helper yang tersedia di root repo:
```bash
cd ~/homelab
chmod +x setup_eternal_iot.sh
./setup_eternal_iot.sh
```

---

## 9. Menggunakan Nextcloud (`:8080` / `nextcloud.homelab`)

- **Akses**: `http://100.78.113.53:8080`, `http://192.168.25.105:8080`, atau `http://nextcloud.homelab`
- **Login**: `fadb` / `wokeh123`
- **Spesifikasi Terpasang**: Database PostgreSQL (`nextcloud`), Redis cache (`redis`), limit memori PHP `1G`, dan limit upload file hingga **`2G`**.
- `NEXTCLOUD_TRUSTED_DOMAINS` sudah mengizinkan LAN IP, Tailscale IP, `nextcloud.homelab`, `localhost`, dan `127.0.0.1`.

---

## 10. DNS Lokal (CoreDNS `:53`) & Nginx Ingress Router (`:80`)

### 10.1 Cara Kerja Routing Domain `.homelab`
- **CoreDNS (`homelab_dns`)** mendengarkan pada `${SERVER_LAN_IP}:53` (UDP/TCP), membaca `dns/homelab.hosts` (auto-reload setiap 5 detik), serta mendukung wildcard `*.tailscale.homelab` → `100.78.113.53` dan `*.homelab` → `192.168.25.105`. DNS publik diteruskan ke `1.1.1.1` & `8.8.8.8`.
- **Nginx (`homelab_router`)** mendengarkan pada port `80` dan meneruskan request berdasarkan header `Host` ke container tujuan sesuai `router/conf.d/00-core-services.conf` dan `router/conf.d/projects/*.conf`.

### 10.2 Cara Mengaktifkan Domain `.homelab` di Perangkat Klien (Laptop/HP)
1. **Lewat Jaringan LAN / WiFi Rumah**: Atur DNS perangkat (atau DNS Router WiFi) ke **`192.168.25.105`**.
2. **Lewat Tailscale (Jarak Jauh)**: Di **Tailscale Admin Console → DNS → Nameservers**, tambahkan Custom Nameserver atau Split DNS khusus domain `homelab` mengarah ke IP server (atau tambahkan isi `dns/homelab.hosts` ke file `hosts` Windows `C:\Windows\System32\drivers\etc\hosts`).
3. **Matikan DNS-over-HTTPS (DoH) di Browser** *(Opera GX / Chrome / Edge)* jika `.homelab` tidak terbuka padahal DNS sudah diarahkan — karena DoH mem-bypass DNS lokal ke Cloudflare/Google.

### 10.3 Menguji & Memuat Ulang Nginx Router
```bash
# Tes konfigurasi Nginx sebelum reload
docker exec homelab_router nginx -t

# Reload Nginx tanpa memutus koneksi aktif
docker exec homelab_router nginx -s reload

# Uji virtual host dari server
curl -s -I -H "Host: psm.homelab" http://127.0.0.1 | head -n 5
curl -s -I -H "Host: minio.homelab" http://127.0.0.1 | head -n 5
```

---

## 11. Troubleshooting Cepat & Checklist Keamanan

| Gejala | Penyebab Umum | Solusi Cepat |
|---|---|---|
| **Grafana `ERR_CONNECTION_REFUSED` setelah baru di-reset** | Grafana v13 sedang menjalankan ~728 migrasi SQLite + load 54 plugin (~8–10 menit) | Pantau `docker logs -f grafana` atau tunggu sampai `curl -fsS http://127.0.0.1:3000/api/health` mengembalikan `"database": "ok"` |
| **Grafana crash dengan `Datasource provisioning error: data source not found`** | Konflik state UID lama di `homelab_grafana_data` | Reset **hanya** volume Grafana: `docker compose stop grafana && docker compose rm -f grafana && docker volume rm homelab_grafana_data && docker compose up -d grafana` |
| **Web Laravel tampil tanpa CSS/JS saat dibuka via IP/HTTP** | `APP_ENV=production` memicu `URL::forceScheme('https')` sehingga aset diminta via `https://` | Set `APP_ENV=local` di `.env` project lewat PSM (atau sesuaikan `AppServiceProvider`) |
| **Aplikasi gagal konek MySQL/PostgreSQL/Redis** | `.env` project memakai `127.0.0.1` atau `localhost` | Ubah ke hostname Docker: `DB_HOST=mysql` (atau `postgres`), `REDIS_HOST=redis` |
| **Domain `.homelab` tidak bisa dibuka di browser** | Browser memakai DoH (`Use DNS-over-HTTPS`) atau OS belum memakai DNS `192.168.25.105` | Matikan DoH di browser, tes `curl -I -H "Host: <domain>" http://127.0.0.1` di server, lalu atur DNS/hosts klien |
| **Container `unhealthy` atau error mendadak** | Bug runtime / koneksi DB belum siap saat start | Cek `docker logs --tail 80 <nama_container>` dan `docker inspect --format '{{json .State.Health}}' <nama_container>` |
| **Disk server mulai penuh** | Cache build Docker & image lama menumpuk | Jalankan `docker image prune -f` dan `docker builder prune -f` *(hindari `docker volume prune` agar data aman)* |

### Checklist Keamanan Produksi
- Segera ubah password/secret default di file `.env` pada root `~/homelab` sebelum server diekspos ke publik (`MYSQL_ROOT_PASSWORD`, `POSTGRES_PASSWORD`, `MYSQL_PASSWORD`, `MINIO_ROOT_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, `NEXTCLOUD_ADMIN_PASSWORD`, `JWT_SECRET`, `PSM_SECRET_KEY`, `PSM_ENCRYPTION_MASTER_KEY`).
- Ubah password login default akun PSM (`admin@psm.local`) melalui halaman **`Settings`** setelah login pertama kali.
- Pastikan akses eksternal tetap melewati **Tailscale** (`100.78.113.53`) dan firewall Ubuntu (`ufw`) hanya membuka port yang diperlukan di interface LAN/Tailscale.
