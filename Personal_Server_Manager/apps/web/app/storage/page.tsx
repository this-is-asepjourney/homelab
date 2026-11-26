'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import {
  Database,
  Download,
  RefreshCw,
  FolderArchive,
  ShieldCheck,
  RotateCcw,
  Trash2,
  HardDrive,
  ArrowDownToLine,
  ArrowUpFromLine,
  Layers,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  Copy,
  Check,
  Cloud,
  Server,
  Link2,
  Rocket,
  Activity,
  Plus,
  Zap,
} from 'lucide-react';
import { api, formatBytes } from '../../lib/api';

export default function StoragePage() {
  const [backups, setBackups] = useState<any[]>([]);
  const [storageStats, setStorageStats] = useState<any>(null);
  const [dbOverview, setDbOverview] = useState<any>(null);
  const [creating, setCreating] = useState(false);
  const [pruning, setPruning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [copiedSnippet, setCopiedSnippet] = useState<string | null>(null);
  const [configTab, setConfigTab] = useState<'laravel' | 'node' | 'python'>('laravel');
  const [browserHost, setBrowserHost] = useState<string>('192.168.25.105');

  // S3 Bucket Creator state
  const [newBucketName, setNewBucketName] = useState('');
  const [newBucketPublic, setNewBucketPublic] = useState(true);
  const [creatingBucket, setCreatingBucket] = useState(false);

  // Backup Target Selectors
  const [selectedPgDb, setSelectedPgDb] = useState('psm');
  const [selectedMysqlDb, setSelectedMysqlDb] = useState('fadb');
  const [syncBackupToS3, setSyncBackupToS3] = useState(true);

  const isTailscaleDomain = browserHost.endsWith('.tailscale.homelab');
  const isHomelabDomain = browserHost === 'homelab' || browserHost.endsWith('.homelab');
  const minioConsoleUrl = isTailscaleDomain
    ? 'http://minio.tailscale.homelab'
    : isHomelabDomain
      ? 'http://minio.homelab'
      : `http://${browserHost}:9002`;
  const minioApiUrl = isTailscaleDomain
    ? 'http://s3.tailscale.homelab'
    : isHomelabDomain
      ? 'http://s3.homelab'
      : `http://${browserHost}:9000`;
  const nextcloudUrl = isTailscaleDomain
    ? 'http://cloud.tailscale.homelab'
    : isHomelabDomain
      ? 'http://cloud.homelab'
      : `http://${browserHost}:8082`;

  const handleCopy = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedSnippet(id);
    setTimeout(() => setCopiedSnippet(null), 2000);
  };

  const loadData = async () => {
    try {
      setLoading(true);
      const [list, stats, dbOv] = await Promise.all([
        api.getBackups().catch(() => []),
        api.getStorageTelemetry().catch(() => null),
        api.getDatabasesOverview().catch(() => null),
      ]);
      setBackups(list);
      if (stats) {
        setStorageStats(stats);
      }
      if (dbOv) {
        setDbOverview(dbOv);
        const dbs: any[] = dbOv.databases || [];
        const firstPg = dbs.find((d) => d.db_type === 'postgres');
        const firstMy = dbs.find((d) => d.db_type === 'mysql');
        if (firstPg && !selectedPgDb) setSelectedPgDb(firstPg.db_name);
        if (firstMy && selectedMysqlDb === 'fadb' && !dbs.some((d) => d.db_name === 'fadb')) {
          setSelectedMysqlDb(firstMy.db_name);
        }
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (typeof window !== 'undefined' && window.location.hostname) {
      setBrowserHost(window.location.hostname);
    }
    loadData();
    const interval = setInterval(() => {
      api.getStorageTelemetry().then((st) => {
        if (st) setStorageStats(st);
      }).catch(() => {});
    }, 8000);
    return () => clearInterval(interval);
  }, []);

  const handleCreateBackup = async (backupType: string, databaseName?: string) => {
    try {
      setCreating(true);
      const dateStr = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
      const labelPrefix = databaseName ? `${backupType}-${databaseName}` : backupType;
      await api.createBackup({
        name: `${labelPrefix}-${dateStr}`,
        backup_type: backupType,
        database_name: databaseName,
        sync_to_s3: syncBackupToS3,
      });
      await loadData();
    } catch (err: any) {
      alert(err.message || 'Gagal membuat backup snapshot');
    } finally {
      setCreating(false);
    }
  };

  const handleCreateBucket = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = newBucketName.trim().toLowerCase();
    if (!clean) return;
    try {
      setCreatingBucket(true);
      const res = await api.createS3Bucket(clean, newBucketPublic);
      setNewBucketName('');
      if (res?.minio) {
        setStorageStats((prev: any) => (prev ? { ...prev, minio: res.minio } : prev));
      } else {
        await loadData();
      }
    } catch (err: any) {
      alert(err.message || 'Gagal membuat bucket S3');
    } finally {
      setCreatingBucket(false);
    }
  };

  const handleSafeDockerPrune = async () => {
    const sid = storageStats?.server_id;
    if (!sid) return;
    if (
      !confirm(
        'Bersihkan dangling Docker images & build cache di server? (Seluruh 10 persistent volume data PostgreSQL/MySQL/MinIO/Nextcloud 100% AMAN dan tidak tersentuh).'
      )
    ) {
      return;
    }
    try {
      setPruning(true);
      await api.pruneServer(sid, false);
      alert('Job pembersihan Docker build cache & dangling image telah dikirim ke psm-agent!');
      await loadData();
    } catch (err: any) {
      alert(err.message || 'Gagal mengirim perintah Docker prune');
    } finally {
      setPruning(false);
    }
  };

  const handleRestore = async (id: string, name: string) => {
    if (!confirm(`Pulihkan konfigurasi project & environment dari snapshot "${name}"?`)) return;
    try {
      const res = await api.restoreBackup(id);
      const extra =
        res.restored_projects !== undefined
          ? ` (Project dipulihkan: ${res.restored_projects}, Env vars: ${res.restored_env_vars ?? 0})`
          : '';
      alert(`${res.message || 'Snapshot berhasil dipulihkan.'}${extra}`);
      await loadData();
    } catch (err: any) {
      alert(err.message || 'Restore gagal');
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Hapus file backup snapshot ini dari server?')) return;
    try {
      await api.deleteBackup(id);
      loadData();
    } catch (err: any) {
      alert(err.message || 'Gagal menghapus snapshot');
    }
  };

  const summary = storageStats?.summary || {
    total_bytes: 0,
    used_bytes: 0,
    free_bytes: 0,
    usage_percent: 0,
    free_percent: 100,
    read_bytes_per_sec: 0,
    write_bytes_per_sec: 0,
    total_read_bytes: 0,
    total_write_bytes: 0,
    total_read_count: 0,
    total_write_count: 0,
  };

  const partitions = storageStats?.partitions || [];
  const volumeInfo = storageStats?.volumes || { total: 0, in_use: 0, unused: 0, items: [] };
  const psmFootprint = storageStats?.psm_footprint || {
    base_dir: '/opt/psm',
    workspaces_bytes: 0,
    workspaces_files: 0,
    uploads_bytes: 0,
    uploads_files: 0,
    backups_bytes: 0,
    backups_files: 0,
    total_psm_bytes: 0,
    docker_images_count: 0,
  };
  const minioInfo = storageStats?.minio || {
    online: false,
    buckets: [
      { name: 'images', access: 'public-read', purpose: 'Static assets & image uploads' },
      { name: 'stock-umkm', access: 'public-read', purpose: 'Laravel / UMKM product catalog media' },
      { name: 'homelab-storage', access: 'private', purpose: 'Shared Homelab object storage' },
      { name: 'backups', access: 'private', purpose: 'PSM & Database disaster-recovery archives' },
    ],
  };

  const dbListAll: any[] = dbOverview?.databases || [];
  const pgDbOptions = Array.from(
    new Set(['psm', 'pap_core', 'eternal_iot', ...dbListAll.filter((d) => d.db_type === 'postgres').map((d) => d.db_name)])
  );
  const mysqlDbOptions = Array.from(
    new Set(['fadb', 'stock_umkm', ...dbListAll.filter((d) => d.db_type === 'mysql').map((d) => d.db_name)])
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Homelab Storage, S3 &amp; Backup Center</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Kapasitas disk &amp; I/O real-time, 10 persistent Docker volumes Homelab, MinIO/Silo S3 Object Storage, database terhubung &amp; backup otomatis.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 self-start">
          <a
            href={nextcloudUrl}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-blue-500/30 bg-blue-500/10 hover:bg-blue-500/20 text-xs font-semibold text-blue-400 transition-all"
          >
            <Cloud className="w-3.5 h-3.5" />
            <span>Nextcloud (:8082)</span>
            <ExternalLink className="w-3 h-3" />
          </a>
          <button
            onClick={handleSafeDockerPrune}
            disabled={pruning || !storageStats?.server_id}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-amber-500/30 bg-amber-500/10 hover:bg-amber-500/20 text-xs font-semibold text-amber-400 transition-all disabled:opacity-50"
            title="Hapus dangling image & build cache tanpa menyentuh volume data"
          >
            <Zap className={`w-3.5 h-3.5 ${pruning ? 'animate-pulse' : ''}`} />
            <span>{pruning ? 'Pruning Cache...' : 'Prune Build Cache'}</span>
          </button>
          <button
            onClick={loadData}
            className="flex items-center gap-2 px-3.5 py-2 rounded-lg border border-border hover:bg-card text-xs font-medium text-foreground transition-all"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Real-Time Storage Capacity & Disk Read/Write Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* 1. Used vs Unused Space */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 font-semibold uppercase">
            <span>Disk Used vs Unused</span>
            <HardDrive className="w-4 h-4 text-blue-500" />
          </div>
          <div className="flex items-baseline justify-between">
            <div>
              <div className="text-2xl font-bold font-mono text-foreground">{summary.usage_percent}%</div>
              <div className="text-[11px] text-gray-400">Terpakai: {formatBytes(summary.used_bytes)}</div>
            </div>
            <div className="text-right">
              <div className="text-sm font-bold font-mono text-emerald-500">{summary.free_percent}% Free</div>
              <div className="text-[11px] text-gray-400">Kosong: {formatBytes(summary.free_bytes)}</div>
            </div>
          </div>
          <div className="h-2.5 w-full bg-emerald-500/20 rounded-full overflow-hidden flex">
            <div
              className="h-full bg-blue-500 transition-all duration-500"
              style={{ width: `${Math.min(summary.usage_percent, 100)}%` }}
              title={`Used: ${formatBytes(summary.used_bytes)}`}
            ></div>
          </div>
          <div className="flex items-center justify-between text-[11px] text-gray-500 font-mono">
            <span>Total Root Disk:</span>
            <span className="text-foreground font-semibold">{formatBytes(summary.total_bytes)}</span>
          </div>
        </div>

        {/* 2. Disk Read Speed & Total Read */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 font-semibold uppercase">
            <span>Disk Read (I/O)</span>
            <ArrowDownToLine className="w-4 h-4 text-emerald-500" />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-emerald-500">
              {formatBytes(summary.read_bytes_per_sec)}/s
            </div>
            <p className="text-[11px] text-gray-400 mt-0.5">Kecepatan Baca Disk Host</p>
          </div>
          <div className="pt-2 border-t border-border space-y-1 text-[11px] font-mono">
            <div className="flex justify-between">
              <span className="text-gray-500">Total Read:</span>
              <span className="text-foreground">{formatBytes(summary.total_read_bytes)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Read Ops:</span>
              <span className="text-foreground">{Number(summary.total_read_count || 0).toLocaleString()} IOPS</span>
            </div>
          </div>
        </div>

        {/* 3. Disk Write Speed & Total Write */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 font-semibold uppercase">
            <span>Disk Write (I/O)</span>
            <ArrowUpFromLine className="w-4 h-4 text-amber-500" />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-amber-500">
              {formatBytes(summary.write_bytes_per_sec)}/s
            </div>
            <p className="text-[11px] text-gray-400 mt-0.5">Kecepatan Tulis Disk Host</p>
          </div>
          <div className="pt-2 border-t border-border space-y-1 text-[11px] font-mono">
            <div className="flex justify-between">
              <span className="text-gray-500">Total Written:</span>
              <span className="text-foreground">{formatBytes(summary.total_write_bytes)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Write Ops:</span>
              <span className="text-foreground">{Number(summary.total_write_count || 0).toLocaleString()} IOPS</span>
            </div>
          </div>
        </div>

        {/* 4. Docker Volumes & PSM Storage Footprint */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 font-semibold uppercase">
            <span>Homelab Volumes &amp; PSM</span>
            <Layers className="w-4 h-4 text-purple-500" />
          </div>
          <div className="flex items-baseline justify-between">
            <div className="flex items-baseline gap-2">
              <div className="text-2xl font-bold font-mono text-foreground">{volumeInfo.total}</div>
              <span className="text-xs text-gray-400">Volumes</span>
            </div>
            <span className="text-xs font-mono text-purple-400 font-semibold">
              {psmFootprint.docker_images_count} Images
            </span>
          </div>
          <div className="pt-2 border-t border-border space-y-1.5 text-xs">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-emerald-500 font-medium">
                <CheckCircle2 className="w-3.5 h-3.5" /> Mounted (In-Use)
              </span>
              <span className="font-mono font-bold text-foreground">{volumeInfo.in_use}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-amber-500 font-medium">
                <AlertCircle className="w-3.5 h-3.5" /> Unmounted / Idle
              </span>
              <span className="font-mono font-bold text-foreground">{volumeInfo.unused}</span>
            </div>
          </div>
        </div>
      </div>

      {/* PSM Storage Footprint Strip (/opt/psm) */}
      <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-lg bg-purple-500/10 text-purple-400 border border-purple-500/20">
              <Server className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-bold text-sm text-foreground">
                Jejak Penyimpanan PSM Control Plane &amp; Agent (<code className="text-purple-400">{psmFootprint.base_dir}</code>)
              </h3>
              <p className="text-xs text-gray-400">
                Rincian pemakaian ruang oleh build workspace project, paket upload ZIP, dan arsip backup lokal pada volume <code>homelab_psm_storage</code>.
              </p>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3 text-xs font-mono">
            <div className="px-3 py-2 rounded-lg bg-background border border-border">
              <div className="text-[10px] uppercase text-gray-400">Workspaces</div>
              <div className="font-bold text-foreground">{formatBytes(psmFootprint.workspaces_bytes)}</div>
              <div className="text-[10px] text-gray-500">{psmFootprint.workspaces_files} file</div>
            </div>
            <div className="px-3 py-2 rounded-lg bg-background border border-border">
              <div className="text-[10px] uppercase text-gray-400">ZIP Uploads</div>
              <div className="font-bold text-foreground">{formatBytes(psmFootprint.uploads_bytes)}</div>
              <div className="text-[10px] text-gray-500">{psmFootprint.uploads_files} arsip</div>
            </div>
            <div className="px-3 py-2 rounded-lg bg-background border border-border">
              <div className="text-[10px] uppercase text-gray-400">Backups</div>
              <div className="font-bold text-emerald-400">{formatBytes(psmFootprint.backups_bytes)}</div>
              <div className="text-[10px] text-gray-500">{psmFootprint.backups_files} snapshot</div>
            </div>
          </div>
        </div>
      </div>

      {/* Mounted Partitions & Filesystems Table */}
      <div className="rounded-xl border border-border bg-card overflow-hidden shadow-sm">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div>
            <h3 className="font-bold text-sm text-foreground">Partisi Disk &amp; Mountpoint Host</h3>
            <p className="text-xs text-gray-500">
              Rincian ruang terpakai vs kosong beserta mode mount Read/Write pada filesystem host Ubuntu Server
            </p>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-background/60 text-gray-400 uppercase tracking-wider border-b border-border">
              <tr>
                <th className="p-3.5">Device</th>
                <th className="p-3.5">Mountpoint</th>
                <th className="p-3.5">Filesystem</th>
                <th className="p-3.5">Mode (R/W)</th>
                <th className="p-3.5">Used Space</th>
                <th className="p-3.5">Unused (Free)</th>
                <th className="p-3.5">Total Size</th>
                <th className="p-3.5 w-44">Utilization</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border font-mono">
              {partitions.map((part: any, i: number) => {
                const isRw = !part.opts || part.opts.includes('rw');
                return (
                  <tr key={i} className="hover:bg-gray-500/5 transition-colors">
                    <td className="p-3.5 font-semibold text-foreground">{part.device}</td>
                    <td className="p-3.5 text-blue-500 font-bold">{part.mountpoint}</td>
                    <td className="p-3.5 text-gray-400 uppercase">{part.fstype}</td>
                    <td className="p-3.5">
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                          isRw
                            ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                            : 'bg-amber-500/10 text-amber-500 border border-amber-500/20'
                        }`}
                      >
                        {isRw ? 'READ / WRITE' : 'READ-ONLY'}
                      </span>
                    </td>
                    <td className="p-3.5 text-foreground font-semibold">{formatBytes(part.used_bytes)}</td>
                    <td className="p-3.5 text-emerald-500 font-semibold">{formatBytes(part.free_bytes)}</td>
                    <td className="p-3.5 text-gray-400">{formatBytes(part.total_bytes)}</td>
                    <td className="p-3.5">
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-2 bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${
                              part.percent > 85
                                ? 'bg-rose-500'
                                : part.percent > 70
                                ? 'bg-amber-500'
                                : 'bg-blue-500'
                            }`}
                            style={{ width: `${Math.min(part.percent, 100)}%` }}
                          ></div>
                        </div>
                        <span className="text-[11px] font-bold text-foreground w-10 text-right">
                          {part.percent}%
                        </span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Docker Volumes Breakdown (Accurate Mount Inspection) */}
      <div className="rounded-xl border border-border bg-card overflow-hidden shadow-sm">
        <div className="p-4 border-b border-border flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <h3 className="font-bold text-sm text-foreground">Docker Persistent Volumes Inventory ({volumeInfo.total})</h3>
            <p className="text-xs text-gray-500">
              Inspeksi mount aktual dari Docker daemon: menampilkan fungsi volume Homelab beserta container yang menggunakannya.
            </p>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] text-emerald-400 font-medium">
            <ShieldCheck className="w-4 h-4" />
            <span>Protected dari Auto-Prune</span>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-background/60 text-gray-400 uppercase tracking-wider border-b border-border">
              <tr>
                <th className="p-3.5">Volume Name</th>
                <th className="p-3.5">Peran &amp; Data Homelab</th>
                <th className="p-3.5">Mounted Containers</th>
                <th className="p-3.5">Status</th>
                <th className="p-3.5">Host Mountpoint</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {volumeInfo.items.map((v: any) => {
                const attached: string[] = v.attached_containers || [];
                return (
                  <tr key={v.id || v.name} className="hover:bg-gray-500/5 transition-colors">
                    <td className="p-3.5 font-mono font-bold text-foreground">{v.name}</td>
                    <td className="p-3.5 text-gray-300">{v.role || 'Docker Persistent Volume'}</td>
                    <td className="p-3.5">
                      {attached.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {attached.map((cname) => (
                            <span
                              key={cname}
                              className="px-2 py-0.5 rounded bg-blue-500/10 border border-blue-500/20 text-blue-400 font-mono text-[10px]"
                            >
                              {cname}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-gray-500 font-mono text-[11px]">—</span>
                      )}
                    </td>
                    <td className="p-3.5 font-mono">
                      <span
                        className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${
                          v.in_use
                            ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                            : 'bg-amber-500/10 text-amber-500 border border-amber-500/20'
                        }`}
                      >
                        {v.in_use ? 'IN-USE (MOUNTED)' : 'UNUSED'}
                      </span>
                    </td>
                    <td className="p-3.5 font-mono text-gray-400 truncate max-w-xs" title={v.mountpoint}>
                      {v.mountpoint}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── MinIO / Silo S3-Compatible Object Storage ────────────────────────── */}
      <div className="rounded-xl border border-border bg-card p-6 shadow-sm space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
          <div className="flex items-center gap-3">
            <div className="p-3 rounded-xl bg-amber-500/10 text-amber-500 border border-amber-500/20">
              <Cloud className="w-6 h-6" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-bold text-base text-foreground">MinIO / Silo S3 Object Storage</h3>
                <span
                  className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase border ${
                    minioInfo.online
                      ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
                      : 'bg-blue-500/10 text-blue-400 border-blue-500/20'
                  }`}
                >
                  {minioInfo.online ? 'ONLINE (:9000 / :9002)' : 'CONFIGURED (:9000 / :9002)'}
                </span>
              </div>
              <p className="text-xs text-gray-400 mt-0.5">
                Penyimpanan objek S3 lokal (<code>pgsty/silo</code>) untuk upload media project (Laravel, Next.js, FastAPI), aset publik, dan replika backup otomatis.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <a
              href={minioConsoleUrl}
              target="_blank"
              rel="noreferrer"
              className="px-3.5 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold flex items-center gap-2 shadow-sm transition-all"
            >
              <span>Buka S3 Web Console</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </div>
        </div>

        {/* 4 Info Boxes */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="p-4 rounded-xl bg-background border border-border space-y-1.5">
            <span className="text-[11px] font-semibold uppercase text-gray-400 tracking-wider">S3 API Endpoint</span>
            <div className="font-mono text-sm font-bold text-foreground break-all">{minioApiUrl}</div>
            <p className="text-[11px] text-gray-500">
              Internal Docker: <code className="text-emerald-400">http://minio:9000</code>
            </p>
          </div>

          <div className="p-4 rounded-xl bg-background border border-border space-y-1.5">
            <span className="text-[11px] font-semibold uppercase text-gray-400 tracking-wider">Web Console</span>
            <div className="font-mono text-sm font-bold text-amber-500 break-all">{minioConsoleUrl}</div>
            <p className="text-[11px] text-gray-500">
              Domain: <code className="text-amber-400">http://minio.homelab</code> · Port <code className="text-gray-300">:9002</code>
            </p>
          </div>

          <div className="p-4 rounded-xl bg-background border border-border space-y-1.5">
            <span className="text-[11px] font-semibold uppercase text-gray-400 tracking-wider">Active Buckets ({minioInfo.buckets?.length || 0})</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {(minioInfo.buckets || []).map((b: any) => (
                <span
                  key={b.name}
                  title={`${b.purpose || 'S3 Bucket'} (${b.access})`}
                  className={`px-1.5 py-0.5 rounded bg-card border text-[10px] font-mono ${
                    b.access === 'public-read'
                      ? 'border-emerald-500/30 text-emerald-400'
                      : 'border-purple-500/30 text-purple-400'
                  }`}
                >
                  {b.name}
                </span>
              ))}
            </div>
            <p className="text-[11px] text-gray-500">Hijau = Public Read · Ungu = Private</p>
          </div>

          {/* Inline Create S3 Bucket Form */}
          <form onSubmit={handleCreateBucket} className="p-4 rounded-xl bg-background border border-border space-y-2">
            <span className="text-[11px] font-semibold uppercase text-gray-400 tracking-wider block">
              Buat Bucket S3 Baru
            </span>
            <div className="flex gap-1.5">
              <input
                type="text"
                value={newBucketName}
                onChange={(e) => setNewBucketName(e.target.value)}
                placeholder="nama-project-media"
                className="flex-1 min-w-0 px-2.5 py-1.5 rounded-lg bg-card border border-border text-xs font-mono text-foreground"
              />
              <button
                type="submit"
                disabled={creatingBucket || !newBucketName.trim()}
                className="px-2.5 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold flex items-center gap-1 disabled:opacity-50"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>{creatingBucket ? '...' : 'Buat'}</span>
              </button>
            </div>
            <label className="flex items-center gap-1.5 text-[11px] text-gray-400 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={newBucketPublic}
                onChange={(e) => setNewBucketPublic(e.target.checked)}
                className="rounded border-border"
              />
              <span>Izinkan akses baca publik (aset/gambar)</span>
            </label>
          </form>
        </div>

        {/* Quick Config Snippets for Projects */}
        <div className="rounded-xl border border-border bg-background/50 p-4 space-y-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border pb-2.5">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-bold text-foreground">Contoh Konfigurasi .env Project:</span>
              <div className="flex items-center gap-1 bg-card p-0.5 rounded-lg border border-border text-[11px]">
                <button
                  type="button"
                  onClick={() => setConfigTab('laravel')}
                  className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
                    configTab === 'laravel' ? 'bg-amber-600 text-white' : 'text-gray-400 hover:text-foreground'
                  }`}
                >
                  Laravel (stock_umkm)
                </button>
                <button
                  type="button"
                  onClick={() => setConfigTab('node')}
                  className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
                    configTab === 'node' ? 'bg-amber-600 text-white' : 'text-gray-400 hover:text-foreground'
                  }`}
                >
                  Node.js / Next.js
                </button>
                <button
                  type="button"
                  onClick={() => setConfigTab('python')}
                  className={`px-2.5 py-1 rounded-md font-medium transition-colors ${
                    configTab === 'python' ? 'bg-amber-600 text-white' : 'text-gray-400 hover:text-foreground'
                  }`}
                >
                  Python (Boto3)
                </button>
              </div>
            </div>

            <button
              type="button"
              onClick={() => {
                const snippets = {
                  laravel: `FILESYSTEM_DISK=s3\nAWS_ACCESS_KEY_ID=admin\nAWS_SECRET_ACCESS_KEY=admin12345\nAWS_DEFAULT_REGION=us-east-1\nAWS_BUCKET=stock-umkm\nAWS_USE_PATH_STYLE_ENDPOINT=true\nAWS_ENDPOINT=http://minio:9000\nAWS_URL=${minioApiUrl}/stock-umkm`,
                  node: `S3_ENDPOINT=http://minio:9000\nS3_PUBLIC_URL=${minioApiUrl}/images\nS3_REGION=us-east-1\nS3_ACCESS_KEY_ID=admin\nS3_SECRET_ACCESS_KEY=admin12345\nS3_BUCKET=images\nS3_FORCE_PATH_STYLE=true`,
                  python: `AWS_ACCESS_KEY_ID=admin\nAWS_SECRET_ACCESS_KEY=admin12345\nAWS_DEFAULT_REGION=us-east-1\nS3_ENDPOINT_URL=http://minio:9000\nS3_PUBLIC_URL=${minioApiUrl}/images\nS3_BUCKET_NAME=images`,
                };
                handleCopy(snippets[configTab], configTab);
              }}
              className="px-2.5 py-1 rounded bg-card hover:bg-card/80 border border-border text-[11px] font-medium text-foreground flex items-center gap-1.5 self-end sm:self-auto transition-colors"
            >
              {copiedSnippet === configTab ? (
                <>
                  <Check className="w-3.5 h-3.5 text-emerald-500" />
                  <span className="text-emerald-500 font-semibold">Tersalin!</span>
                </>
              ) : (
                <>
                  <Copy className="w-3.5 h-3.5 text-gray-400" />
                  <span>Salin ke Clipboard</span>
                </>
              )}
            </button>
          </div>

          <pre className="font-mono text-xs text-emerald-400 bg-background/90 p-3 rounded-lg border border-border overflow-x-auto whitespace-pre">
            {configTab === 'laravel' && (
              `# Tambahkan ke .env project Laravel (terhubung jaringan internal homelab_net)
FILESYSTEM_DISK=s3
AWS_ACCESS_KEY_ID=admin
AWS_SECRET_ACCESS_KEY=admin12345
AWS_DEFAULT_REGION=us-east-1
AWS_BUCKET=stock-umkm
AWS_USE_PATH_STYLE_ENDPOINT=true
AWS_ENDPOINT=http://minio:9000
AWS_URL=${minioApiUrl}/stock-umkm`
            )}
            {configTab === 'node' && (
              `# Konfigurasi S3 untuk Node.js / Next.js (mis. archflow)
S3_ENDPOINT=http://minio:9000
S3_PUBLIC_URL=${minioApiUrl}/images
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=admin
S3_SECRET_ACCESS_KEY=admin12345
S3_BUCKET=images
S3_FORCE_PATH_STYLE=true`
            )}
            {configTab === 'python' && (
              `# Konfigurasi S3 untuk Python / Boto3 / FastAPI
AWS_ACCESS_KEY_ID=admin
AWS_SECRET_ACCESS_KEY=admin12345
AWS_DEFAULT_REGION=us-east-1
S3_ENDPOINT_URL=http://minio:9000
S3_PUBLIC_URL=${minioApiUrl}/images
S3_BUCKET_NAME=images`
            )}
          </pre>
        </div>
      </div>

      {/* Database Server & Project Terhubung */}
      {(() => {
        const dbList: any[] = dbOverview?.databases || [];
        const dbSummary = dbOverview?.summary;
        const activeRunningCount = dbList.reduce((acc, item) => acc + (item.running_projects_count || 0), 0);

        return (
          <div className="rounded-xl border border-border bg-card overflow-hidden">
            <div className="p-4 border-b border-border flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
                  <Link2 className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="font-bold text-sm text-foreground">Database Server &amp; Project Terhubung</h3>
                  <p className="text-xs text-gray-400">
                    Daftar database PostgreSQL 16 (:5432), MySQL 8.4 (:3307), &amp; SQLite beserta keterhubungannya dengan project Homelab.
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 flex-wrap text-[11px] font-mono">
                <span className="px-2.5 py-1 rounded-full bg-background border border-border text-gray-300">
                  Total DB: <strong className="text-foreground">{dbSummary?.total_databases ?? dbList.length}</strong>
                </span>
                <span className="px-2.5 py-1 rounded-full bg-blue-500/10 border border-blue-500/20 text-blue-400">
                  Terhubung Project: <strong>{dbSummary?.connected_databases ?? 0}</strong>
                </span>
                <span className="px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
                  Container Aktif: <strong>{activeRunningCount}</strong>
                </span>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-background/60 text-gray-400 uppercase tracking-wider border-b border-border">
                  <tr>
                    <th className="p-3.5">Database</th>
                    <th className="p-3.5">Engine</th>
                    <th className="p-3.5">Host</th>
                    <th className="p-3.5">Size &amp; Tables</th>
                    <th className="p-3.5">Sesi</th>
                    <th className="p-3.5">Status Koneksi</th>
                    <th className="p-3.5">Project Ter-deploy Terhubung</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {dbList.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="p-8 text-center text-gray-500">
                        {loading ? 'Memuat daftar database & project...' : 'Belum ada database atau project database yang terdeteksi.'}
                      </td>
                    </tr>
                  ) : (
                    dbList.map((dbItem) => {
                      const connectedProjects: any[] = dbItem.connected_projects || [];
                      const hasRunning = (dbItem.running_projects_count || 0) > 0;
                      const hasLinked = connectedProjects.length > 0;
                      const engineLabel =
                        dbItem.db_type === 'mysql'
                          ? 'MySQL 8.4'
                          : dbItem.db_type === 'sqlite'
                          ? 'SQLite'
                          : 'PostgreSQL 16';
                      const engineCls =
                        dbItem.db_type === 'mysql'
                          ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                          : dbItem.db_type === 'sqlite'
                          ? 'bg-purple-500/10 text-purple-400 border-purple-500/20'
                          : 'bg-blue-500/10 text-blue-400 border-blue-500/20';

                      return (
                        <tr key={`${dbItem.db_type}:${dbItem.db_name}`} className="hover:bg-gray-500/5 transition-colors">
                          <td className="p-3.5 font-mono">
                            <div className="font-semibold text-foreground">{dbItem.db_name}</div>
                            {!dbItem.exists_on_server && (
                              <div className="text-[10px] text-amber-400 mt-0.5">Belum terverifikasi / belum dibuat di server</div>
                            )}
                          </td>
                          <td className="p-3.5">
                            <span className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold border ${engineCls}`}>
                              {engineLabel}
                            </span>
                          </td>
                          <td className="p-3.5 font-mono text-gray-400">
                            {dbItem.db_type === 'sqlite' ? 'localhost' : `${dbItem.host}:${dbItem.port}`}
                          </td>
                          <td className="p-3.5 font-mono text-gray-300">
                            {formatBytes(dbItem.size_bytes || 0)} &bull;{' '}
                            {dbItem.db_type === 'postgres' && dbItem.tables_count === 0
                              ? '—'
                              : `${dbItem.tables_count ?? 0} tbl`}
                          </td>
                          <td className="p-3.5 font-mono text-gray-300">
                            <span className="inline-flex items-center gap-1">
                              <Activity className="w-3 h-3 text-blue-400" />
                              {dbItem.active_connections ?? 0}
                            </span>
                          </td>
                          <td className="p-3.5">
                            {hasRunning ? (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                                Terhubung (Running)
                              </span>
                            ) : hasLinked ? (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase bg-blue-500/10 text-blue-400 border border-blue-500/20">
                                Terkait (Standby)
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase bg-gray-500/10 text-gray-400 border border-border">
                                Core / Standalone
                              </span>
                            )}
                          </td>
                          <td className="p-3.5">
                            {connectedProjects.length > 0 ? (
                              <div className="flex flex-wrap gap-1.5">
                                {connectedProjects.map((cp) => (
                                  <Link
                                    key={cp.id}
                                    href={`/projects/${cp.id}`}
                                    className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border font-mono text-[11px] transition-colors ${
                                      cp.container_running
                                        ? 'border-emerald-500/30 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300'
                                        : 'border-border bg-background hover:border-blue-500/40 text-gray-300'
                                    }`}
                                    title={`${cp.framework} • ${cp.domain} • Port ${cp.port_mappings}`}
                                  >
                                    <span
                                      className={`w-1.5 h-1.5 rounded-full ${
                                        cp.container_running ? 'bg-emerald-400 animate-pulse' : 'bg-gray-500'
                                      }`}
                                    />
                                    <span className="font-semibold">{cp.name}</span>
                                    <Rocket className="w-3 h-3 opacity-70" />
                                  </Link>
                                ))}
                              </div>
                            ) : (
                              <span className="text-gray-500 text-[11px]">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        );
      })()}

      {/* Homelab Backup & Disaster Recovery Center */}
      <div className="rounded-xl border border-border bg-card p-6 shadow-sm space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-4">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-blue-500/10 text-blue-500 border border-blue-500/20">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-bold text-base text-foreground">Homelab Backup &amp; Disaster Recovery</h3>
              <p className="text-xs text-gray-400">
                Ekspor state Control Plane (termasuk variabel <code>.env</code> terenkripsi), SQL dump PostgreSQL/MySQL, atau konfigurasi Workspace ke disk lokal &amp; bucket S3 <code>backups</code>.
              </p>
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs text-gray-300 bg-background px-3 py-2 rounded-lg border border-border cursor-pointer select-none">
            <input
              type="checkbox"
              checked={syncBackupToS3}
              onChange={(e) => setSyncBackupToS3(e.target.checked)}
              className="rounded border-border"
            />
            <span>Auto-copy ke MinIO Bucket <code>backups</code></span>
          </label>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* 1. Control Plane JSON */}
          <div className="p-4 rounded-xl bg-background border border-border flex flex-col justify-between space-y-3">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-foreground">Control Plane State</span>
                <span className="px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 text-[10px] font-mono">.JSON</span>
              </div>
              <p className="text-[11px] text-gray-400 leading-relaxed">
                Snapshot seluruh server, daftar project, domain, port mapping, dan rahasia <code>.env</code> terenkripsi (bisa di-Restore otomatis).
              </p>
            </div>
            <button
              onClick={() => handleCreateBackup('database')}
              disabled={creating}
              className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold transition-all disabled:opacity-50"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Snapshot Control Plane</span>
            </button>
          </div>

          {/* 2. PostgreSQL SQL Dump */}
          <div className="p-4 rounded-xl bg-background border border-border flex flex-col justify-between space-y-3">
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-foreground">PostgreSQL 16 Dump</span>
                <span className="px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400 text-[10px] font-mono">.SQL</span>
              </div>
              <select
                value={selectedPgDb}
                onChange={(e) => setSelectedPgDb(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded-lg bg-card border border-border text-xs font-mono text-foreground"
              >
                {pgDbOptions.map((dbn) => (
                  <option key={dbn} value={dbn}>
                    {dbn}
                  </option>
                ))}
              </select>
              <p className="text-[11px] text-gray-500">Ekspor skema &amp; baris data PostgreSQL.</p>
            </div>
            <button
              onClick={() => handleCreateBackup('postgres_sql', selectedPgDb)}
              disabled={creating}
              className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold transition-all disabled:opacity-50"
            >
              <Database className="w-3.5 h-3.5" />
              <span>Dump PostgreSQL ({selectedPgDb})</span>
            </button>
          </div>

          {/* 3. MySQL SQL Dump */}
          <div className="p-4 rounded-xl bg-background border border-border flex flex-col justify-between space-y-3">
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-foreground">MySQL 8.4 Dump</span>
                <span className="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400 text-[10px] font-mono">.SQL</span>
              </div>
              <select
                value={selectedMysqlDb}
                onChange={(e) => setSelectedMysqlDb(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded-lg bg-card border border-border text-xs font-mono text-foreground"
              >
                {mysqlDbOptions.map((dbn) => (
                  <option key={dbn} value={dbn}>
                    {dbn}
                  </option>
                ))}
              </select>
              <p className="text-[11px] text-gray-500">Ekspor tabel &amp; data MySQL project.</p>
            </div>
            <button
              onClick={() => handleCreateBackup('mysql_sql', selectedMysqlDb)}
              disabled={creating}
              className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold transition-all disabled:opacity-50"
            >
              <Database className="w-3.5 h-3.5" />
              <span>Dump MySQL ({selectedMysqlDb})</span>
            </button>
          </div>

          {/* 4. Workspaces ZIP Archive */}
          <div className="p-4 rounded-xl bg-background border border-border flex flex-col justify-between space-y-3">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-foreground">Project Workspaces</span>
                <span className="px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400 text-[10px] font-mono">.ZIP</span>
              </div>
              <p className="text-[11px] text-gray-400 leading-relaxed">
                Arsipkan seluruh <code>Dockerfile</code>, <code>docker-compose.yml</code>, <code>.dockerignore</code>, dan manifes project dari <code>/opt/psm/workspaces</code>.
              </p>
            </div>
            <button
              onClick={() => handleCreateBackup('project_workspace')}
              disabled={creating}
              className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-xs font-semibold transition-all disabled:opacity-50"
            >
              <FolderArchive className="w-3.5 h-3.5" />
              <span>Arsipkan Workspaces</span>
            </button>
          </div>
        </div>
      </div>

      {/* Backup History Table */}
      <div className="rounded-xl border border-border bg-card overflow-hidden">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <h3 className="font-bold text-sm text-foreground">Riwayat Arsip Backup ({backups.length})</h3>
          <button
            onClick={loadData}
            className="text-xs text-blue-500 hover:underline flex items-center gap-1"
          >
            <RefreshCw className="w-3 h-3" /> Refresh
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-background/60 text-gray-400 uppercase tracking-wider border-b border-border">
              <tr>
                <th className="p-3.5">Archive Name</th>
                <th className="p-3.5">Type &amp; Target</th>
                <th className="p-3.5">Size</th>
                <th className="p-3.5">Storage</th>
                <th className="p-3.5">Created At</th>
                <th className="p-3.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {backups.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-8 text-center text-gray-500">
                    {loading ? 'Memuat daftar snapshot...' : 'Belum ada snapshot backup yang dibuat.'}
                  </td>
                </tr>
              ) : (
                backups.map((b) => {
                  const meta = b.metadata_json || {};
                  const ext = meta.format || (b.backup_type?.includes('sql') ? 'sql' : b.backup_type === 'project_workspace' ? 'zip' : 'json');
                  const dlFilename = meta.filename || `${b.name}.${ext}`;
                  return (
                    <tr key={b.id} className="hover:bg-gray-500/5 transition-colors">
                      <td className="p-3.5 font-mono font-semibold text-foreground">{b.name}</td>
                      <td className="p-3.5">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="capitalize text-gray-300">{(b.backup_type || 'database').replace(/_/g, ' ')}</span>
                          {meta.database_name && (
                            <span className="px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 font-mono text-[10px]">
                              {meta.database_name}
                            </span>
                          )}
                          <span className="px-1.5 py-0.5 rounded bg-card border border-border text-gray-400 font-mono uppercase text-[10px]">
                            .{ext}
                          </span>
                        </div>
                      </td>
                      <td className="p-3.5 font-mono text-gray-300">{formatBytes(b.size_bytes)}</td>
                      <td className="p-3.5">
                        <div className="flex items-center gap-1.5">
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
                            Local
                          </span>
                          {meta.s3_synced && (
                            <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase bg-amber-500/10 text-amber-400 border border-amber-500/20">
                              S3 Synced
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="p-3.5 text-gray-400">{new Date(b.created_at).toLocaleString()}</td>
                      <td className="p-3.5 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => api.downloadBackup(b.id, dlFilename)}
                            className="flex items-center gap-1 px-2.5 py-1 rounded border border-border hover:bg-card text-foreground font-medium"
                            title="Download Snapshot File"
                          >
                            <Download className="w-3 h-3 text-emerald-500" />
                            <span>Download</span>
                          </button>
                          {ext === 'json' && (
                            <button
                              onClick={() => handleRestore(b.id, b.name)}
                              className="flex items-center gap-1 px-2.5 py-1 rounded border border-border hover:bg-blue-500/10 text-blue-500 font-medium"
                            >
                              <RotateCcw className="w-3 h-3" />
                              <span>Restore</span>
                            </button>
                          )}
                          <button
                            onClick={() => handleDelete(b.id)}
                            className="p-1.5 rounded hover:bg-rose-500/10 text-rose-500"
                            title="Delete Snapshot"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
