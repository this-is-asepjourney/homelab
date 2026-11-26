'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import Link from 'next/link';
import {
  Activity,
  Cpu,
  HardDrive,
  AlertTriangle,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Settings2,
  Copy,
  Check,
  ExternalLink,
  Terminal,
  Server,
  Layers,
  Database,
  Cloud,
  Radio,
  ShieldCheck,
  Zap,
} from 'lucide-react';
import { api, formatBytes, formatDuration, getWsBase } from '../../lib/api';

function parseMemSizeToBytes(raw: string): number {
  const m = raw?.trim().match(/^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)$/);
  if (!m) return 0;
  const val = parseFloat(m[1]);
  const unit = m[2].toUpperCase();
  const mult: Record<string, number> = {
    B: 1,
    KB: 1000,
    KIB: 1024,
    MB: 1000 ** 2,
    MIB: 1024 ** 2,
    GB: 1000 ** 3,
    GIB: 1024 ** 3,
  };
  return Math.round(val * (mult[unit] || 1));
}

function normalizeWsContainerStats(rawList: any[]): any[] {
  if (!Array.isArray(rawList)) return [];
  return rawList
    .map((item) => {
      const cpuVal = parseFloat(String(item.cpu_percent || '0').replace('%', '')) || 0;
      const memPct = parseFloat(String(item.mem_percent || '0').replace('%', '')) || 0;
      const memUsage = String(item.mem_usage || '0B / 0B');
      const parts = memUsage.split('/');
      const usedB = item.mem_used_bytes ?? parseMemSizeToBytes(parts[0] || '0B');
      const limitB = item.mem_limit_bytes ?? parseMemSizeToBytes(parts[1] || '0B');
      return {
        id: item.id || item.name,
        name: item.name,
        cpu_percent: Number(cpuVal.toFixed(2)),
        mem_percent: Number(memPct.toFixed(2)),
        mem_used_bytes: usedB,
        mem_limit_bytes: limitB,
        mem_usage: memUsage,
        net_io: item.net_io || '0B / 0B',
        block_io: item.block_io || '0B / 0B',
      };
    })
    .sort((a, b) => b.cpu_percent - a.cpu_percent || b.mem_used_bytes - a.mem_used_bytes)
    .slice(0, 15);
}

const CATEGORY_META: Record<string, { title: string; badgeCls: string }> = {
  databases: {
    title: 'Core Databases (Shared SQL & Cache)',
    badgeCls: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  },
  platform: {
    title: 'Control Plane, API Hub & Ingress',
    badgeCls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  },
  storage_cloud: {
    title: 'Personal Cloud & S3 Object Storage',
    badgeCls: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  },
  observability: {
    title: 'Observability & Metrics Stack',
    badgeCls: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
  },
  iot_pipeline: {
    title: 'Eternal IoT Telemetry Pipeline',
    badgeCls: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
  },
};

export default function MonitoringPage() {
  const [overview, setOverview] = useState<any>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [promStatus, setPromStatus] = useState<any>(null);
  const [promUrlInput, setPromUrlInput] = useState('http://prometheus:9090');
  const [showPromConfig, setShowPromConfig] = useState(false);
  const [savingProm, setSavingProm] = useState(false);
  const [copiedCmd, setCopiedCmd] = useState(false);
  const [loading, setLoading] = useState(true);
  const [wsConnected, setWsConnected] = useState(false);
  const [livePaused, setLivePaused] = useState(false);
  const [browserHost, setBrowserHost] = useState('192.168.25.105');
  const livePausedRef = useRef(false);

  useEffect(() => {
    livePausedRef.current = livePaused;
  }, [livePaused]);

  const isTailscaleDomain = browserHost.endsWith('.tailscale.homelab');
  const isHomelabDomain = browserHost === 'homelab' || browserHost.endsWith('.homelab');

  const buildServiceUrl = (sub: string, port: number) => {
    if (isTailscaleDomain) return `http://${sub}.tailscale.homelab`;
    if (isHomelabDomain) return `http://${sub}.homelab`;
    return `http://${browserHost}:${port}`;
  };

  const loadData = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const [ov, hist, pStat] = await Promise.all([
        api.getMonitoringOverview().catch(() => null),
        api.getMonitoringHistory().catch(() => []),
        api.getPrometheusStatus().catch(() => null),
      ]);
      if (ov) setOverview(ov);
      if (Array.isArray(hist)) setHistory(hist);
      if (pStat) {
        setPromStatus(pStat);
        if (pStat.configured_url) {
          setPromUrlInput(pStat.configured_url);
        }
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (typeof window !== 'undefined' && window.location.hostname) {
      setBrowserHost(window.location.hostname);
    }
    loadData(false);
  }, [loadData]);

  // Real-time WebSocket stream (5s agent push) for instant CPU / RAM / Swap / Container metrics
  const primaryServerId = overview?.primary_server_id;
  useEffect(() => {
    if (!primaryServerId || typeof window === 'undefined') return;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let unmounted = false;

    const connectWs = () => {
      if (unmounted) return;
      try {
        ws = new WebSocket(`${getWsBase()}/ws/servers/${encodeURIComponent(primaryServerId)}/metrics`);
        ws.onopen = () => {
          if (!unmounted) setWsConnected(true);
        };
        ws.onmessage = (evt) => {
          if (unmounted || livePausedRef.current) return;
          try {
            const msg = JSON.parse(evt.data);
            if (msg?.event === 'server.metrics.updated' && msg?.data) {
              const d = msg.data;
              const swapUsed = d.swap_used_bytes ?? 0;
              const swapTotal = d.swap_total_bytes ?? 0;
              const swapPct = swapTotal > 0 ? Number(((swapUsed / swapTotal) * 100).toFixed(1)) : 0;

              setOverview((prev: any) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  resources: {
                    ...prev.resources,
                    avg_cpu_percent: Number((d.cpu_usage ?? prev.resources?.avg_cpu_percent ?? 0).toFixed(1)),
                    cpu_cores: d.cpu_cores || prev.resources?.cpu_cores || 1,
                    load_avg: Array.isArray(d.load_avg) ? d.load_avg : prev.resources?.load_avg || [0, 0, 0],
                    uptime_seconds: d.uptime ?? prev.resources?.uptime_seconds ?? 0,
                    avg_memory_percent: Number((d.memory_usage ?? prev.resources?.avg_memory_percent ?? 0).toFixed(1)),
                    memory_used_bytes: d.memory_used_bytes ?? prev.resources?.memory_used_bytes ?? 0,
                    memory_available_bytes: d.memory_available_bytes ?? prev.resources?.memory_available_bytes ?? 0,
                    memory_total_bytes: d.memory_total_bytes ?? prev.resources?.memory_total_bytes ?? 0,
                    swap_used_bytes: swapUsed,
                    swap_total_bytes: swapTotal,
                    swap_percent: swapPct,
                    avg_disk_percent: Number((d.disk_usage ?? prev.resources?.avg_disk_percent ?? 0).toFixed(1)),
                    disk_used_bytes: d.disk_used_bytes ?? prev.resources?.disk_used_bytes ?? 0,
                    disk_total_bytes: d.disk_total_bytes ?? prev.resources?.disk_total_bytes ?? 0,
                    disk_read_bytes_per_sec: d.disk_read_bytes_per_sec ?? prev.resources?.disk_read_bytes_per_sec ?? 0,
                    disk_write_bytes_per_sec: d.disk_write_bytes_per_sec ?? prev.resources?.disk_write_bytes_per_sec ?? 0,
                    network_rx_bytes_per_sec: d.network_rx ?? prev.resources?.network_rx_bytes_per_sec ?? 0,
                    network_tx_bytes_per_sec: d.network_tx ?? prev.resources?.network_tx_bytes_per_sec ?? 0,
                  },
                  top_containers: d.container_stats?.length
                    ? normalizeWsContainerStats(d.container_stats)
                    : prev.top_containers,
                };
              });

              const tsLabel = new Date(d.recorded_at || Date.now()).toTimeString().slice(0, 5);
              setHistory((prevHist) => {
                const pt = {
                  time: tsLabel,
                  cpu: Number((d.cpu_usage ?? 0).toFixed(1)),
                  memory: Number((d.memory_usage ?? 0).toFixed(1)),
                  disk: Number((d.disk_usage ?? 0).toFixed(1)),
                };
                if (prevHist.length > 0 && prevHist[prevHist.length - 1]?.time === tsLabel) {
                  return [...prevHist.slice(0, -1), pt];
                }
                return [...prevHist.slice(-39), pt];
              });
            }
          } catch (_) {}
        };
        ws.onclose = () => {
          if (!unmounted) {
            setWsConnected(false);
            reconnectTimer = setTimeout(connectWs, 4000);
          }
        };
        ws.onerror = () => {
          try {
            ws?.close();
          } catch (_) {}
        };
      } catch (_) {
        setWsConnected(false);
        reconnectTimer = setTimeout(connectWs, 5000);
      }
    };

    connectWs();
    return () => {
      unmounted = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try {
        ws?.close();
      } catch (_) {}
    };
  }, [primaryServerId]);

  // Adaptive background sync (20s when WS live, 6s fallback when WS offline)
  useEffect(() => {
    if (livePaused) return;
    const pollMs = wsConnected ? 20000 : 6000;
    const interval = setInterval(() => {
      loadData(true);
    }, pollMs);
    return () => clearInterval(interval);
  }, [wsConnected, livePaused, loadData]);

  const handleSavePrometheusUrl = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setSavingProm(true);
      const res = await api.updatePrometheusConfig(promUrlInput);
      if (res?.status) {
        setPromStatus(res.status);
      } else {
        await loadData(true);
      }
    } catch (err: any) {
      alert(err.message || 'Gagal memperbarui URL Prometheus');
    } finally {
      setSavingProm(false);
    }
  };

  const composeScrapeSnippet = `# Konfigurasi aktif di prometheus/prometheus.yml (Homelab Stack :9090)
scrape_configs:
  - job_name: "prometheus"
    static_configs: [{ targets: ["localhost:9090"] }]
  - job_name: "node"
    static_configs: [{ targets: ["node-exporter:9100"] }]
  - job_name: "cadvisor"
    static_configs: [{ targets: ["cadvisor:8080"] }]
  - job_name: "pap-core"
    static_configs: [{ targets: ["pap-api:8000"] }]
  - job_name: "psm-api"
    metrics_path: "/api/v1/monitoring/prometheus/metrics"
    static_configs: [{ targets: ["psm-api:8000"] }]
  - job_name: "telegraf"
    static_configs: [{ targets: ["telegraf:9273"] }]`;

  const handleCopyCommands = () => {
    navigator.clipboard.writeText(composeScrapeSnippet);
    setCopiedCmd(true);
    setTimeout(() => setCopiedCmd(false), 2000);
  };

  const isPromConnected = Boolean(promStatus?.connected);
  const res = overview?.resources || {};
  const loadAvg: number[] = Array.isArray(res.load_avg) && res.load_avg.length >= 3 ? res.load_avg : [0, 0, 0];
  const swapPct = res.swap_percent ?? 0;
  const ecoServices: any[] = overview?.homelab_services?.items || [];
  const topContainers: any[] = overview?.top_containers || [];
  const alertRules: any[] = overview?.alert_rules || [];
  const activeAlerts: any[] = overview?.alerts || [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5 flex-wrap">
            <h1 className="text-2xl font-bold tracking-tight text-foreground">Homelab Monitoring &amp; Observability</h1>
            <span
              className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-semibold border ${
                livePaused
                  ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                  : wsConnected
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'bg-blue-500/10 text-blue-400 border-blue-500/20'
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  livePaused ? 'bg-amber-400' : wsConnected ? 'bg-emerald-400 animate-pulse' : 'bg-blue-400 animate-pulse'
                }`}
              />
              {livePaused ? 'PAUSED' : wsConnected ? 'WS LIVE (5s)' : 'POLLING SYNC'}
            </span>
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            Telemetri real-time host Ubuntu (CPU, RAM, Swap, Disk I/O), matriks 18 container ekosistem Homelab, dan integrasi Prometheus/Grafana.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setLivePaused((p) => !p)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium border border-border hover:bg-card text-foreground transition-all"
          >
            {livePaused ? 'Resume Stream' : 'Pause Stream'}
          </button>

          <button
            onClick={() => setShowPromConfig(!showPromConfig)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
              isPromConnected
                ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20 hover:bg-emerald-500/20'
                : 'bg-amber-500/10 text-amber-500 border-amber-500/20 hover:bg-amber-500/20'
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isPromConnected ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'
              }`}
            ></span>
            <span>
              {isPromConnected
                ? `Prometheus TSDB (${promStatus?.version || 'v2.x'})`
                : 'Prometheus: Offline / Configure'}
            </span>
            <Settings2 className="w-3.5 h-3.5 ml-0.5" />
          </button>

          <button
            onClick={() => loadData(false)}
            className="p-2 rounded-lg border border-border text-gray-500 hover:text-foreground hover:bg-card transition-all"
            title="Refresh Telemetry"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Homelab Observability Quick-Launch Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <a
          href={buildServiceUrl('grafana', 3000)}
          target="_blank"
          rel="noreferrer"
          className="p-3.5 rounded-xl border border-border bg-card hover:border-amber-500/40 transition-all flex items-center justify-between group"
        >
          <div>
            <div className="text-xs font-bold text-foreground group-hover:text-amber-400 transition-colors">Grafana Dashboards</div>
            <div className="text-[11px] font-mono text-gray-500">grafana.homelab (:3000)</div>
          </div>
          <ExternalLink className="w-4 h-4 text-gray-400 group-hover:text-amber-400" />
        </a>

        <a
          href={buildServiceUrl('prometheus', 9090)}
          target="_blank"
          rel="noreferrer"
          className="p-3.5 rounded-xl border border-border bg-card hover:border-orange-500/40 transition-all flex items-center justify-between group"
        >
          <div>
            <div className="text-xs font-bold text-foreground group-hover:text-orange-400 transition-colors">Prometheus TSDB</div>
            <div className="text-[11px] font-mono text-gray-500">prometheus.homelab (:9090)</div>
          </div>
          <ExternalLink className="w-4 h-4 text-gray-400 group-hover:text-orange-400" />
        </a>

        <a
          href={buildServiceUrl('cadvisor', 8081)}
          target="_blank"
          rel="noreferrer"
          className="p-3.5 rounded-xl border border-border bg-card hover:border-blue-500/40 transition-all flex items-center justify-between group"
        >
          <div>
            <div className="text-xs font-bold text-foreground group-hover:text-blue-400 transition-colors">cAdvisor Containers</div>
            <div className="text-[11px] font-mono text-gray-500">cadvisor.homelab (:8081)</div>
          </div>
          <ExternalLink className="w-4 h-4 text-gray-400 group-hover:text-blue-400" />
        </a>

        <a
          href={buildServiceUrl('status', 3001)}
          target="_blank"
          rel="noreferrer"
          className="p-3.5 rounded-xl border border-border bg-card hover:border-emerald-500/40 transition-all flex items-center justify-between group"
        >
          <div>
            <div className="text-xs font-bold text-foreground group-hover:text-emerald-400 transition-colors">Uptime Kuma SLA</div>
            <div className="text-[11px] font-mono text-gray-500">status.homelab (:3001)</div>
          </div>
          <ExternalLink className="w-4 h-4 text-gray-400 group-hover:text-emerald-400" />
        </a>
      </div>

      {/* Containerized Prometheus (:9090) Configuration & Scrape Targets */}
      {(showPromConfig || !isPromConnected) && (
        <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border pb-3">
            <div className="flex items-center gap-2.5">
              {isPromConnected ? (
                <CheckCircle2 className="w-5 h-5 text-emerald-500" />
              ) : (
                <XCircle className="w-5 h-5 text-amber-500" />
              )}
              <div>
                <h3 className="font-bold text-sm text-foreground">
                  Homelab Prometheus Container Integration (Port 9090)
                </h3>
                <p className="text-xs text-gray-500">
                  {isPromConnected
                    ? `Terhubung ke container Prometheus di ${promStatus.url}`
                    : `Belum terjangkau (${promStatus?.error || 'Pastikan container homelab_prometheus aktif di port 9090'})`}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2 text-xs">
              <a
                href="/api/v1/monitoring/prometheus/metrics"
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-border hover:bg-background text-blue-500 font-mono"
              >
                <span>OpenMetrics /metrics</span>
                <ExternalLink className="w-3 h-3" />
              </a>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 text-xs">
            {/* Left: Endpoint configuration + Scrape Targets */}
            <div className="space-y-3">
              <form onSubmit={handleSavePrometheusUrl} className="space-y-2.5">
                <label className="block font-semibold text-foreground">
                  Prometheus Internal / LAN URL
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={promUrlInput}
                    onChange={(e) => setPromUrlInput(e.target.value)}
                    placeholder="http://prometheus:9090"
                    className="flex-1 px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono text-xs"
                  />
                  <button
                    type="submit"
                    disabled={savingProm}
                    className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold disabled:opacity-50 whitespace-nowrap"
                  >
                    {savingProm ? 'Testing...' : 'Save & Test'}
                  </button>
                </div>
                <p className="text-[11px] text-gray-400">
                  Default jaringan Docker Homelab: <code>http://prometheus:9090</code> (atau LAN <code>http://192.168.25.105:9090</code> / Tailscale <code>http://100.78.113.53:9090</code>).
                </p>
              </form>

              {isPromConnected && Array.isArray(promStatus?.active_targets) && (
                <div className="pt-2 space-y-1.5">
                  <span className="font-semibold text-gray-400 block">
                    Active Scrape Targets ({promStatus.active_targets.length}):
                  </span>
                  {promStatus.active_targets.length === 0 ? (
                    <div className="p-2.5 rounded bg-background border border-border text-gray-500">
                      Belum ada scrape target aktif.
                    </div>
                  ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-40 overflow-y-auto">
                      {promStatus.active_targets.map((t: any, i: number) => (
                        <div
                          key={i}
                          className="flex items-center justify-between p-2 rounded bg-background border border-border font-mono text-[11px]"
                        >
                          <span className="truncate pr-2">
                            <strong>{t.job}</strong> <span className="text-gray-500">({t.instance})</span>
                          </span>
                          <span
                            className={`px-2 py-0.5 rounded uppercase text-[10px] font-bold ${
                              t.health === 'up'
                                ? 'bg-emerald-500/10 text-emerald-500'
                                : 'bg-rose-500/10 text-rose-500'
                            }`}
                          >
                            {t.health}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Right: Homelab prometheus.yml reference */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-foreground flex items-center gap-1.5">
                  <Terminal className="w-3.5 h-3.5 text-blue-500" />
                  <span>Arsitektur Scrape Homelab (<code>prometheus/prometheus.yml</code>):</span>
                </span>
                <button
                  onClick={handleCopyCommands}
                  className="flex items-center gap-1 px-2 py-1 rounded bg-background border border-border hover:text-blue-500 text-[11px]"
                >
                  {copiedCmd ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                  <span>{copiedCmd ? 'Copied!' : 'Copy YAML'}</span>
                </button>
              </div>
              <pre className="p-3 rounded-lg bg-slate-950 text-slate-200 font-mono text-[11px] overflow-x-auto leading-relaxed border border-slate-800">
                {composeScrapeSnippet}
              </pre>
            </div>
          </div>
        </div>
      )}

      {/* 4-Card Real-Time Host & Swap Gauges */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* 1. CPU & Load Average */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 uppercase tracking-wider font-semibold">
            <span>Host CPU &amp; Load</span>
            <Cpu className="w-4 h-4 text-blue-500" />
          </div>
          <div className="flex items-baseline justify-between">
            <div className="text-3xl font-bold font-mono text-foreground">
              {res.avg_cpu_percent ?? 0}%
            </div>
            <span className="text-xs font-mono text-gray-400">{res.cpu_cores || 1} vCPU</span>
          </div>
          <div className="h-2 w-full bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                (res.avg_cpu_percent ?? 0) > 85 ? 'bg-rose-500' : 'bg-blue-500'
              }`}
              style={{ width: `${Math.min(res.avg_cpu_percent ?? 0, 100)}%` }}
            ></div>
          </div>
          <div className="flex items-center justify-between text-[11px] text-gray-400 font-mono pt-1">
            <span>Load: {loadAvg.join(' / ')}</span>
            {res.uptime_seconds > 0 && <span>Up {formatDuration(res.uptime_seconds)}</span>}
          </div>
        </div>

        {/* 2. Physical RAM */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 uppercase tracking-wider font-semibold">
            <span>Physical RAM</span>
            <Activity className="w-4 h-4 text-purple-500" />
          </div>
          <div className="flex items-baseline justify-between">
            <div className="text-3xl font-bold font-mono text-foreground">
              {res.avg_memory_percent ?? 0}%
            </div>
            {res.memory_available_bytes > 0 && (
              <span className="text-xs font-mono text-emerald-400">
                {formatBytes(res.memory_available_bytes)} Avail
              </span>
            )}
          </div>
          <div className="h-2 w-full bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                (res.avg_memory_percent ?? 0) > 88 ? 'bg-rose-500' : 'bg-purple-500'
              }`}
              style={{ width: `${Math.min(res.avg_memory_percent ?? 0, 100)}%` }}
            ></div>
          </div>
          <div className="text-[11px] text-gray-400 font-mono pt-1">
            {res.memory_total_bytes
              ? `${formatBytes(res.memory_used_bytes)} / ${formatBytes(res.memory_total_bytes)} used`
              : 'Menunggu sampel agen...'}
          </div>
        </div>

        {/* 3. Linux Swap Memory Pressure */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 uppercase tracking-wider font-semibold">
            <span>Swap Memory</span>
            <Layers className="w-4 h-4 text-amber-500" />
          </div>
          <div className="flex items-baseline justify-between">
            <div className="text-3xl font-bold font-mono text-foreground">{swapPct}%</div>
            <span
              className={`text-[10px] font-mono font-bold uppercase px-1.5 py-0.5 rounded ${
                swapPct > 65
                  ? 'bg-rose-500/10 text-rose-400'
                  : swapPct > 30
                  ? 'bg-amber-500/10 text-amber-400'
                  : 'bg-emerald-500/10 text-emerald-400'
              }`}
            >
              {swapPct > 65 ? 'HIGH SWAP' : swapPct > 30 ? 'WARM' : 'HEALTHY'}
            </span>
          </div>
          <div className="h-2 w-full bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                swapPct > 65 ? 'bg-rose-500' : 'bg-amber-500'
              }`}
              style={{ width: `${Math.min(swapPct, 100)}%` }}
            ></div>
          </div>
          <div className="text-[11px] text-gray-400 font-mono pt-1">
            {res.swap_total_bytes
              ? `${formatBytes(res.swap_used_bytes)} / ${formatBytes(res.swap_total_bytes)} (swappiness=10)`
              : '0 B / 0 B Swap'}
          </div>
        </div>

        {/* 4. Root Disk & Live I/O */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 uppercase tracking-wider font-semibold">
            <span>Disk &amp; Live I/O</span>
            <HardDrive className="w-4 h-4 text-emerald-500" />
          </div>
          <div className="flex items-baseline justify-between">
            <div className="text-3xl font-bold font-mono text-foreground">
              {res.avg_disk_percent ?? 0}%
            </div>
            <Link href="/storage" className="text-[11px] text-blue-400 hover:underline font-medium">
              Detail Storage →
            </Link>
          </div>
          <div className="h-2 w-full bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-emerald-500 rounded-full transition-all duration-500"
              style={{ width: `${Math.min(res.avg_disk_percent ?? 0, 100)}%` }}
            ></div>
          </div>
          <div className="flex items-center justify-between text-[11px] text-gray-400 font-mono pt-1">
            <span>R: {formatBytes(res.disk_read_bytes_per_sec || 0)}/s</span>
            <span>W: {formatBytes(res.disk_write_bytes_per_sec || 0)}/s</span>
          </div>
        </div>
      </div>

      {/* Homelab 18-Service Ecosystem Health Matrix */}
      <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border pb-3">
          <div className="flex items-center gap-2.5">
            <Server className="w-5 h-5 text-emerald-500" />
            <div>
              <h3 className="font-bold text-sm text-foreground">
                Homelab Core Ecosystem Matrix ({overview?.homelab_services?.running ?? 0}/{overview?.homelab_services?.total ?? 18} Active)
              </h3>
              <p className="text-xs text-gray-500">
                Status kontainer infrastruktur utama sesuai arsitektur <code>docker-compose.yml</code> Homelab.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs font-mono">
            <span className="px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
              Running Containers: <strong>{overview?.containers?.running ?? 0}</strong> / {overview?.containers?.total ?? 0}
            </span>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {Object.entries(CATEGORY_META).map(([catKey, meta]) => {
            const items = ecoServices.filter((s) => s.category === catKey);
            if (items.length === 0) return null;
            return (
              <div key={catKey} className="p-3.5 rounded-xl bg-background border border-border space-y-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-foreground">{meta.title}</span>
                  <span className={`px-2 py-0.5 rounded-full text-[10px] font-mono border ${meta.badgeCls}`}>
                    {items.filter((i) => i.status === 'running').length}/{items.length} UP
                  </span>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {items.map((svc) => {
                    const isUp = svc.status === 'running';
                    const isStopped = svc.status === 'stopped';
                    return (
                      <div
                        key={svc.key}
                        className="p-2.5 rounded-lg bg-card border border-border flex items-center justify-between gap-2 text-xs"
                      >
                        <div className="min-w-0">
                          <div className="font-semibold text-foreground truncate flex items-center gap-1.5">
                            <span
                              className={`w-2 h-2 rounded-full shrink-0 ${
                                isUp
                                  ? 'bg-emerald-400 animate-pulse'
                                  : isStopped
                                  ? 'bg-rose-500'
                                  : 'bg-gray-500'
                              }`}
                            />
                            <span className="truncate">{svc.label}</span>
                          </div>
                          <div className="text-[10px] font-mono text-gray-500 truncate mt-0.5">
                            {svc.domain} {svc.port > 0 ? `(:${svc.port})` : ''}
                          </div>
                        </div>
                        <span
                          className={`px-1.5 py-0.5 rounded text-[9px] font-mono font-bold uppercase shrink-0 ${
                            isUp
                              ? 'bg-emerald-500/10 text-emerald-400'
                              : isStopped
                              ? 'bg-rose-500/10 text-rose-400'
                              : 'bg-gray-500/10 text-gray-400'
                          }`}
                        >
                          {svc.status}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Per-Container Live CPU & Memory Breakdown */}
      <div className="rounded-xl border border-border bg-card overflow-hidden shadow-sm">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div>
            <h3 className="font-bold text-sm text-foreground">Top Container CPU &amp; RAM Telemetry</h3>
            <p className="text-xs text-gray-500">
              Pemakaian resource kontainer secara real-time untuk mendeteksi bottleneck memori/CPU lebih awal
            </p>
          </div>
          <Link href="/network" className="text-xs text-blue-400 hover:underline font-medium">
            Lihat Network Flow →
          </Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-background/60 text-gray-400 uppercase tracking-wider border-b border-border">
              <tr>
                <th className="p-3.5">Container</th>
                <th className="p-3.5">CPU %</th>
                <th className="p-3.5">RAM Usage / Limit</th>
                <th className="p-3.5 w-40">RAM %</th>
                <th className="p-3.5">Network I/O</th>
                <th className="p-3.5">Block Disk I/O</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border font-mono">
              {topContainers.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-6 text-center text-gray-500 font-sans">
                    Menunggu sampel <code>container_stats</code> dari psm-agent...
                  </td>
                </tr>
              ) : (
                topContainers.map((c: any) => (
                  <tr key={c.id || c.name} className="hover:bg-gray-500/5 transition-colors">
                    <td className="p-3.5 font-bold text-foreground">{c.name}</td>
                    <td className="p-3.5">
                      <span className={c.cpu_percent > 50 ? 'text-amber-400 font-bold' : 'text-blue-400'}>
                        {c.cpu_percent}%
                      </span>
                    </td>
                    <td className="p-3.5 text-gray-300">{c.mem_usage}</td>
                    <td className="p-3.5">
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-gray-200 dark:bg-gray-800 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${
                              c.mem_percent > 85 ? 'bg-rose-500' : 'bg-purple-500'
                            }`}
                            style={{ width: `${Math.min(c.mem_percent || 0, 100)}%` }}
                          />
                        </div>
                        <span className="text-[11px] text-gray-300 w-11 text-right">{c.mem_percent}%</span>
                      </div>
                    </td>
                    <td className="p-3.5 text-gray-400">{c.net_io}</td>
                    <td className="p-3.5 text-gray-400">{c.block_io}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Historical Metrics Timeline Visualizer */}
      <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-bold text-sm text-foreground">Historical CPU &amp; RAM Timeline</h3>
            <p className="text-xs text-gray-500">Tren sampel telemetri host (24 jam terakhir + live stream)</p>
          </div>
          <div className="flex items-center gap-4 text-xs font-medium">
            <span className="flex items-center gap-1.5 text-blue-500">
              <span className="w-2.5 h-2.5 rounded-full bg-blue-500"></span> CPU %
            </span>
            <span className="flex items-center gap-1.5 text-purple-500">
              <span className="w-2.5 h-2.5 rounded-full bg-purple-500"></span> RAM %
            </span>
          </div>
        </div>

        <div className="h-44 flex items-end gap-1.5 pt-6 pb-2 border-b border-border">
          {history.length === 0 ? (
            <div className="w-full text-center text-xs text-gray-500 self-center">
              Mengumpulkan sampel metrik dari psm-agent...
            </div>
          ) : (
            history.slice(-40).map((pt, idx) => (
              <div key={idx} className="flex-1 flex flex-col justify-end items-center gap-1 group relative h-full">
                <div className="absolute -top-8 hidden group-hover:flex flex-col items-center bg-slate-900 text-white text-[10px] p-1.5 rounded shadow z-10 whitespace-nowrap">
                  <span>
                    {pt.time}: CPU {pt.cpu}% | RAM {pt.memory}%
                  </span>
                </div>
                <div
                  className="w-full bg-blue-500/80 rounded-t hover:bg-blue-400 transition-all"
                  style={{ height: `${Math.max(pt.cpu || 0, 4)}%` }}
                ></div>
                <div
                  className="w-full bg-purple-500/80 rounded-t hover:bg-purple-400 transition-all"
                  style={{ height: `${Math.max(pt.memory || 0, 4)}%` }}
                ></div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Dynamic Alert Rules & Active Alerts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-bold text-sm text-foreground flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-emerald-500" />
              <span>Aturan Guard &amp; Threshold Aktif</span>
            </h3>
            <Link href="/settings" className="text-xs text-blue-400 hover:underline">
              Atur Discord/Telegram →
            </Link>
          </div>
          <div className="space-y-2 text-xs">
            {(alertRules.length > 0
              ? alertRules
              : [
                  { id: 'r1', label: 'High CPU Utilization (> 90%)', current_value: `${res.avg_cpu_percent ?? 0}%`, status: 'ok' },
                  { id: 'r2', label: 'High RAM Utilization (> 90%)', current_value: `${res.avg_memory_percent ?? 0}%`, status: 'ok' },
                  { id: 'r3', label: 'Root Disk Capacity (> 90%)', current_value: `${res.avg_disk_percent ?? 0}%`, status: 'ok' },
                  { id: 'r4', label: 'Agent Heartbeat Watchdog (> 90s)', current_value: 'Online', status: 'ok' },
                ]
            ).map((rule: any) => {
              const firing = rule.status === 'firing';
              return (
                <div
                  key={rule.id}
                  className="flex items-center justify-between p-3 rounded-lg border border-border bg-background"
                >
                  <div className="flex items-center gap-2.5">
                    <span
                      className={`w-2 h-2 rounded-full ${
                        firing ? 'bg-rose-500 animate-ping' : 'bg-emerald-500'
                      }`}
                    ></span>
                    <span className="font-medium text-foreground">{rule.label}</span>
                  </div>
                  <div className="flex items-center gap-2 font-mono">
                    <span className="text-gray-400">{rule.current_value}</span>
                    <span
                      className={`font-semibold uppercase text-[10px] px-1.5 py-0.5 rounded ${
                        firing
                          ? 'bg-rose-500/10 text-rose-400'
                          : 'bg-emerald-500/10 text-emerald-500'
                      }`}
                    >
                      {firing ? 'FIRING' : 'OK'}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <h3 className="font-bold text-sm text-foreground mb-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-500" />
            <span>Insiden &amp; Peringatan Aktif ({activeAlerts.length})</span>
          </h3>
          {activeAlerts.length === 0 ? (
            <div className="p-6 rounded-xl bg-background border border-border text-center space-y-1">
              <CheckCircle2 className="w-6 h-6 text-emerald-500 mx-auto" />
              <div className="text-xs font-semibold text-foreground">Seluruh Node Homelab Berjalan Normal</div>
              <p className="text-[11px] text-gray-500">
                Tidak ada lonjakan CPU, memori, ruang disk, atau agen yang terputus.
              </p>
            </div>
          ) : (
            <div className="space-y-2 max-h-56 overflow-y-auto text-xs">
              {activeAlerts.map((alt: any) => (
                <div
                  key={alt.id}
                  className="p-3 rounded-lg border border-rose-500/30 bg-rose-500/5 space-y-1"
                >
                  <div className="flex items-center justify-between font-semibold text-rose-400">
                    <span>{alt.title}</span>
                    <span className="text-[10px] uppercase font-mono">{alt.level}</span>
                  </div>
                  <p className="text-gray-300 text-[11px]">{alt.description}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
