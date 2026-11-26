'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Network,
  ArrowDownLeft,
  ArrowUpRight,
  Activity,
  ShieldCheck,
  RefreshCw,
  Wifi,
  Server,
  Globe,
  Radio,
  Search,
  CheckCircle2,
  XCircle,
  Zap,
  Layers,
} from 'lucide-react';
import { api, formatBytes, getWsBase } from '../../lib/api';

export default function NetworkMonitoringPage() {
  const [netData, setNetData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [livePaused, setLivePaused] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);

  // Filters for Active Flows & Ports
  const [flowDirection, setFlowDirection] = useState<string>('all');
  const [flowZone, setFlowZone] = useState<string>('all');
  const [flowSearch, setFlowSearch] = useState('');
  const [portSearch, setPortSearch] = useState('');

  // Network Probe state
  const [probeTarget, setProbeTarget] = useState('1.1.1.1');
  const [probePort, setProbePort] = useState('53');
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<any>(null);

  const livePausedRef = useRef(livePaused);
  livePausedRef.current = livePaused;

  const loadNetwork = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const data = await api.getNetworkTelemetry();
      if (data) {
        setNetData(data);
      }
    } catch (err) {
      console.error(err);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  // Initial fetch
  useEffect(() => {
    loadNetwork(false);
  }, [loadNetwork]);

  // Real-time WebSocket subscription (/ws/servers/{server_id}/metrics)
  const activeServerId = netData?.server_id;
  useEffect(() => {
    if (!activeServerId || typeof window === 'undefined') return;

    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let unmounted = false;

    const connectWs = () => {
      if (unmounted) return;
      try {
        const wsUrl = `${getWsBase()}/ws/servers/${encodeURIComponent(activeServerId)}/metrics`;
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          if (!unmounted) setWsConnected(true);
        };

        ws.onmessage = (evt) => {
          if (unmounted || livePausedRef.current) return;
          try {
            const msg = JSON.parse(evt.data);
            if (msg?.event === 'server.metrics.updated' && msg?.data) {
              const d = msg.data;
              setNetData((prev: any) => {
                if (!prev) return prev;
                const nextPoint = d.network_history_point || {
                  time: new Date(d.recorded_at || Date.now()).toTimeString().slice(0, 8),
                  rx_bytes_per_sec: d.network_rx ?? 0,
                  tx_bytes_per_sec: d.network_tx ?? 0,
                };
                const prevHist = Array.isArray(prev.history) ? prev.history : [];
                const updatedHist =
                  prevHist.length > 0 && prevHist[prevHist.length - 1]?.time === nextPoint.time
                    ? [...prevHist.slice(0, -1), nextPoint]
                    : [...prevHist.slice(-35), nextPoint];

                return {
                  ...prev,
                  bandwidth: {
                    rx_bytes_per_sec: d.network_rx ?? prev.bandwidth?.rx_bytes_per_sec ?? 0,
                    tx_bytes_per_sec: d.network_tx ?? prev.bandwidth?.tx_bytes_per_sec ?? 0,
                    total_rx_bytes: d.network_total_rx_bytes ?? prev.bandwidth?.total_rx_bytes ?? 0,
                    total_tx_bytes: d.network_total_tx_bytes ?? prev.bandwidth?.total_tx_bytes ?? 0,
                    packets_recv: d.network_packets_recv ?? prev.bandwidth?.packets_recv ?? 0,
                    packets_sent: d.network_packets_sent ?? prev.bandwidth?.packets_sent ?? 0,
                    err_in: d.network_err_in ?? prev.bandwidth?.err_in ?? 0,
                    err_out: d.network_err_out ?? prev.bandwidth?.err_out ?? 0,
                    drop_in: d.network_drop_in ?? prev.bandwidth?.drop_in ?? 0,
                    drop_out: d.network_drop_out ?? prev.bandwidth?.drop_out ?? 0,
                  },
                  interfaces: d.network_interfaces?.length ? d.network_interfaces : prev.interfaces,
                  listening_ports: d.listening_ports?.length ? d.listening_ports : prev.listening_ports,
                  connection_stats: d.connection_stats || prev.connection_stats,
                  active_flows: d.active_flows || prev.active_flows,
                  flow_summary: d.flow_summary || prev.flow_summary,
                  history: updatedHist,
                  recorded_at: d.recorded_at || prev.recorded_at,
                };
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
  }, [activeServerId]);

  // Adaptive background sync: 20s for ingress/container metadata when WS is live, 5s fallback when WS is offline
  useEffect(() => {
    if (livePaused) return;
    const pollMs = wsConnected ? 20000 : 5000;
    const interval = setInterval(() => {
      loadNetwork(true);
    }, pollMs);
    return () => clearInterval(interval);
  }, [wsConnected, livePaused, loadNetwork]);

  const handleRunProbe = async (e?: React.FormEvent, customTarget?: string, customPort?: number) => {
    if (e) e.preventDefault();
    const t = customTarget ?? probeTarget;
    const p = customPort ?? (probePort ? parseInt(probePort, 10) : 80);
    if (!t) return;
    try {
      setProbing(true);
      const res = await api.probeNetworkTarget(t, p);
      setProbeResult(res);
    } catch (err: any) {
      setProbeResult({
        target: t,
        port: p,
        reachable: false,
        error: err.message || 'Probe failed',
      });
    } finally {
      setProbing(false);
    }
  };

  const bw = netData?.bandwidth || {
    rx_bytes_per_sec: 0,
    tx_bytes_per_sec: 0,
    total_rx_bytes: 0,
    total_tx_bytes: 0,
    packets_recv: 0,
    packets_sent: 0,
    err_in: 0,
    err_out: 0,
    drop_in: 0,
    drop_out: 0,
  };

  const connStats = netData?.connection_stats || {
    established: 0,
    listen: 0,
    time_wait: 0,
    close_wait: 0,
    syn_active: 0,
    total: 0,
  };

  const flowSummary = netData?.flow_summary || {
    by_direction: { inbound: 0, internal: 0, outbound: 0 },
    by_zone: { lan: 0, tailscale: 0, docker: 0, internet: 0, loopback: 0 },
    active_flows_count: 0,
  };

  const interfaces = netData?.interfaces || [];
  const listeningPorts = netData?.listening_ports || [];
  const activeFlows = netData?.active_flows || [];
  const containerNetwork = netData?.container_network || [];
  const ingressRoutes = netData?.ingress_routes || [];
  const dockerNetworks = netData?.docker_networks || [];
  const history = netData?.history || [];

  const filteredFlows = activeFlows.filter((f: any) => {
    if (flowDirection !== 'all' && f.direction !== flowDirection) return false;
    if (flowZone !== 'all' && f.zone !== flowZone) return false;
    if (!flowSearch) return true;
    const q = flowSearch.toLowerCase();
    return (
      String(f.local_port).includes(q) ||
      String(f.remote_port).includes(q) ||
      (f.remote_ip || '').toLowerCase().includes(q) ||
      (f.local_ip || '').toLowerCase().includes(q) ||
      (f.service || '').toLowerCase().includes(q) ||
      (f.zone || '').toLowerCase().includes(q)
    );
  });

  const filteredPorts = listeningPorts.filter(
    (p: any) =>
      !portSearch ||
      String(p.port).includes(portSearch) ||
      (p.service || '').toLowerCase().includes(portSearch.toLowerCase()) ||
      (p.bind_address || '').includes(portSearch)
  );

  const totalErrors = (bw.err_in || 0) + (bw.err_out || 0) + (bw.drop_in || 0) + (bw.drop_out || 0);
  const lastUpdatedStr = netData?.recorded_at
    ? new Date(netData.recorded_at).toLocaleTimeString()
    : '-';

  const zoneBadgeClass = (zone: string) => {
    switch (zone) {
      case 'tailscale':
        return 'bg-purple-500/10 text-purple-400 border-purple-500/20';
      case 'lan':
        return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
      case 'docker':
        return 'bg-blue-500/10 text-blue-400 border-blue-500/20';
      case 'internet':
        return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
      default:
        return 'bg-gray-500/10 text-gray-400 border-gray-500/20';
    }
  };

  const directionBadgeClass = (dir: string) => {
    switch (dir) {
      case 'inbound':
        return 'bg-emerald-500/10 text-emerald-500';
      case 'outbound':
        return 'bg-blue-500/10 text-blue-400';
      default:
        return 'bg-purple-500/10 text-purple-400';
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-2xl font-bold tracking-tight text-foreground">Network Monitoring Center</h1>
            <span
              className={`px-2.5 py-0.5 rounded-full border text-xs font-semibold flex items-center gap-1.5 ${
                livePaused
                  ? 'bg-gray-500/10 text-gray-400 border-gray-500/20'
                  : wsConnected
                  ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
                  : 'bg-blue-500/10 text-blue-400 border-blue-500/20'
              }`}
            >
              <span
                className={`w-2 h-2 rounded-full ${
                  livePaused
                    ? 'bg-gray-400'
                    : wsConnected
                    ? 'bg-emerald-500 animate-pulse'
                    : 'bg-blue-400 animate-pulse'
                }`}
              ></span>
              {livePaused ? 'Paused' : wsConnected ? 'WebSocket Real-Time (5s)' : 'Live Poll (5s)'}
            </span>
            <span className="text-xs font-mono text-gray-500">Updated: {lastUpdatedStr}</span>
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
            Deteksi alur jaringan server real-time (LAN, Tailscale VPN, Docker Bridge, Ingress `.homelab`, & socket kernel `/proc/net` ultra-ringan).
          </p>
        </div>

        <div className="flex items-center gap-2 self-start">
          <button
            onClick={() => setLivePaused((p) => !p)}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border hover:bg-card text-xs font-medium text-foreground transition-all"
          >
            <Activity className="w-3.5 h-3.5 text-emerald-500" />
            <span>{livePaused ? 'Resume Live' : 'Pause Stream'}</span>
          </button>
          <button
            onClick={() => loadNetwork(false)}
            className="flex items-center gap-2 px-3.5 py-2 rounded-lg border border-border hover:bg-card text-xs font-medium text-foreground transition-all"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Top 4 Network KPI Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* 1. Inbound Bandwidth (RX) */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 font-semibold uppercase">
            <span>Inbound Traffic (RX)</span>
            <ArrowDownLeft className="w-4 h-4 text-emerald-500" />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-emerald-500">
              {formatBytes(bw.rx_bytes_per_sec)}/s
            </div>
            <p className="text-[11px] text-gray-400 mt-0.5">Host Physical + VPN Download Rate</p>
          </div>
          <div className="pt-2 border-t border-border space-y-1 text-[11px] font-mono">
            <div className="flex justify-between">
              <span className="text-gray-500">Total Received:</span>
              <span className="text-foreground font-semibold">{formatBytes(bw.total_rx_bytes)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">RX Packets:</span>
              <span className="text-foreground">{Number(bw.packets_recv || 0).toLocaleString()} pkts</span>
            </div>
          </div>
        </div>

        {/* 2. Outbound Bandwidth (TX) */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 font-semibold uppercase">
            <span>Outbound Traffic (TX)</span>
            <ArrowUpRight className="w-4 h-4 text-blue-500" />
          </div>
          <div>
            <div className="text-2xl font-bold font-mono text-blue-500">
              {formatBytes(bw.tx_bytes_per_sec)}/s
            </div>
            <p className="text-[11px] text-gray-400 mt-0.5">Host Physical + VPN Upload Rate</p>
          </div>
          <div className="pt-2 border-t border-border space-y-1 text-[11px] font-mono">
            <div className="flex justify-between">
              <span className="text-gray-500">Total Sent:</span>
              <span className="text-foreground font-semibold">{formatBytes(bw.total_tx_bytes)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">TX Packets:</span>
              <span className="text-foreground">{Number(bw.packets_sent || 0).toLocaleString()} pkts</span>
            </div>
          </div>
        </div>

        {/* 3. Active Socket & Flow Distribution */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 font-semibold uppercase">
            <span>Alur & Koneksi Aktif</span>
            <Activity className="w-4 h-4 text-purple-500" />
          </div>
          <div className="flex items-baseline gap-2.5">
            <div className="text-2xl font-bold font-mono text-foreground">{connStats.established}</div>
            <span className="text-xs text-emerald-500 font-medium">
              Established ({activeFlows.length} alur)
            </span>
          </div>
          <div className="pt-2 border-t border-border grid grid-cols-3 gap-2 text-[11px] font-mono">
            <div>
              <span className="text-gray-500 block text-[10px]">INBOUND</span>
              <span className="text-emerald-500 font-bold">{flowSummary.by_direction?.inbound ?? 0}</span>
            </div>
            <div>
              <span className="text-gray-500 block text-[10px]">INTERNAL</span>
              <span className="text-purple-400 font-bold">{flowSummary.by_direction?.internal ?? 0}</span>
            </div>
            <div>
              <span className="text-gray-500 block text-[10px]">OUTBOUND</span>
              <span className="text-blue-400 font-bold">{flowSummary.by_direction?.outbound ?? 0}</span>
            </div>
          </div>
        </div>

        {/* 4. Zone Breakdown & Packet Integrity */}
        <div className="p-5 rounded-xl border border-border bg-card shadow-sm space-y-3">
          <div className="flex items-center justify-between text-xs text-gray-400 font-semibold uppercase">
            <span>Zona Trafik & Integritas</span>
            <ShieldCheck className="w-4 h-4 text-emerald-500" />
          </div>
          <div className="flex items-center gap-2">
            <div className="text-2xl font-bold font-mono text-foreground">
              {totalErrors === 0 ? '100%' : `${totalErrors} err`}
            </div>
            <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-emerald-500/10 text-emerald-500">
              {totalErrors === 0 ? 'Clean Link' : 'Check Drops'}
            </span>
          </div>
          <div className="pt-2 border-t border-border grid grid-cols-4 gap-1 text-[10px] font-mono">
            <div>
              <span className="text-gray-500 block">LAN</span>
              <span className="text-emerald-400 font-bold">{flowSummary.by_zone?.lan ?? 0}</span>
            </div>
            <div>
              <span className="text-gray-500 block">VPN</span>
              <span className="text-purple-400 font-bold">{flowSummary.by_zone?.tailscale ?? 0}</span>
            </div>
            <div>
              <span className="text-gray-500 block">DOCKER</span>
              <span className="text-blue-400 font-bold">{flowSummary.by_zone?.docker ?? 0}</span>
            </div>
            <div>
              <span className="text-gray-500 block">WAN</span>
              <span className="text-amber-400 font-bold">{flowSummary.by_zone?.internet ?? 0}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Server Network Flow Architecture & Ingress Route Map */}
      <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Globe className="w-4 h-4 text-emerald-500" />
            <div>
              <h3 className="font-bold text-sm text-foreground">
                Peta Alur Jaringan Server & Rute Ingress `.homelab` ({ingressRoutes.length})
              </h3>
              <p className="text-xs text-gray-500">
                Alur masuk trafik dari LAN ({netData?.hostname || '192.168.25.105'}) & Tailscale ({netData?.tailscale_ip || '100.78.113.53'}) menuju CoreDNS, Nginx Ingress, hingga Container tujuan
              </p>
            </div>
          </div>
          <span className="text-[11px] font-mono text-gray-400">
            LISTEN Ports: {connStats.listen} | TIME_WAIT: {connStats.time_wait}
          </span>
        </div>

        {/* 4-Stage Visual Flow Pipeline */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-xs font-mono">
          <div className="p-3.5 rounded-lg border border-border bg-background space-y-1">
            <div className="text-[10px] uppercase font-bold text-emerald-500">1. Sumber Klien Masuk</div>
            <div className="font-bold text-foreground">LAN & Tailscale VPN</div>
            <div className="text-[11px] text-gray-400">
              LAN: {flowSummary.by_zone?.lan ?? 0} sesi | VPN: {flowSummary.by_zone?.tailscale ?? 0} sesi
            </div>
          </div>
          <div className="p-3.5 rounded-lg border border-border bg-background space-y-1">
            <div className="text-[10px] uppercase font-bold text-blue-400">2. Resolusi & Gateway</div>
            <div className="font-bold text-foreground">CoreDNS :53 ➔ Nginx :80</div>
            <div className="text-[11px] text-gray-400">
              Wildcard *.homelab & Reverse Proxy
            </div>
          </div>
          <div className="p-3.5 rounded-lg border border-border bg-background space-y-1">
            <div className="text-[10px] uppercase font-bold text-purple-400">3. Aplikasi & Microservices</div>
            <div className="font-bold text-foreground">Bridge `homelab`</div>
            <div className="text-[11px] text-gray-400">
              {ingressRoutes.filter((r: any) => r.kind === 'project').length} Project App + 9 Core Services
            </div>
          </div>
          <div className="p-3.5 rounded-lg border border-border bg-background space-y-1">
            <div className="text-[10px] uppercase font-bold text-amber-400">4. Data & Upstream</div>
            <div className="font-bold text-foreground">PG :5432 • MySQL :3306 • S3</div>
            <div className="text-[11px] text-gray-400">
              Internal: {flowSummary.by_direction?.internal ?? 0} | Internet: {flowSummary.by_zone?.internet ?? 0}
            </div>
          </div>
        </div>

        {/* Active Domain -> Upstream Port Badges */}
        {ingressRoutes.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 pt-1">
            {ingressRoutes.map((route: any) => (
              <div
                key={route.domain}
                className="flex items-center justify-between px-3 py-2 rounded-lg border border-border bg-background/70 text-xs font-mono"
              >
                <div className="min-w-0 pr-2">
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`w-2 h-2 rounded-full shrink-0 ${
                        route.maintenance_mode
                          ? 'bg-amber-500'
                          : route.status === 'stopped'
                          ? 'bg-rose-500'
                          : 'bg-emerald-500'
                      }`}
                    ></span>
                    <span className="font-bold text-foreground truncate">{route.domain}</span>
                  </div>
                  <div className="text-[10px] text-gray-400 truncate">
                    ➔ {route.service} (:{route.host_port})
                  </div>
                </div>
                <span
                  className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase shrink-0 ${
                    route.kind === 'project'
                      ? 'bg-purple-500/10 text-purple-400 border border-purple-500/20'
                      : 'bg-blue-500/10 text-blue-400 border border-blue-500/20'
                  }`}
                >
                  {route.maintenance_mode ? '503 MAINT' : route.kind}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Real-Time Active Network Flows Explorer */}
      <div className="rounded-xl border border-border bg-card overflow-hidden shadow-sm">
        <div className="p-4 border-b border-border space-y-3">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Network className="w-4 h-4 text-purple-500" />
              <div>
                <h3 className="font-bold text-sm text-foreground">
                  Alur Koneksi Jaringan Aktif / Live Network Flows ({filteredFlows.length})
                </h3>
                <p className="text-xs text-gray-500">
                  Deteksi koneksi masuk, komunikasi antar-container internal, dan koneksi keluar secara real-time tanpa beban packet-capture
                </p>
              </div>
            </div>

            <div className="relative w-full lg:w-64">
              <Search className="w-3.5 h-3.5 text-gray-400 absolute left-3 top-2.5" />
              <input
                type="text"
                placeholder="Cari IP, port, atau service..."
                value={flowSearch}
                onChange={(e) => setFlowSearch(e.target.value)}
                className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          </div>

          {/* Direction & Zone Filter Pills */}
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex flex-wrap items-center gap-1.5">
              {[
                { key: 'all', label: 'Semua Arah' },
                { key: 'inbound', label: `Masuk / Inbound (${flowSummary.by_direction?.inbound ?? 0})` },
                { key: 'internal', label: `Internal Docker (${flowSummary.by_direction?.internal ?? 0})` },
                { key: 'outbound', label: `Keluar / Outbound (${flowSummary.by_direction?.outbound ?? 0})` },
              ].map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setFlowDirection(item.key)}
                  className={`px-2.5 py-1 rounded-md font-medium border transition-colors ${
                    flowDirection === item.key
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-background text-gray-400 border-border hover:text-foreground'
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-1.5">
              {[
                { key: 'all', label: 'Semua Zona' },
                { key: 'lan', label: 'LAN' },
                { key: 'tailscale', label: 'Tailscale' },
                { key: 'docker', label: 'Docker' },
                { key: 'internet', label: 'Internet' },
              ].map((z) => (
                <button
                  key={z.key}
                  type="button"
                  onClick={() => setFlowZone(z.key)}
                  className={`px-2.5 py-1 rounded-md font-mono text-[11px] border transition-colors ${
                    flowZone === z.key
                      ? 'bg-emerald-600 text-white border-emerald-600'
                      : 'bg-background text-gray-400 border-border hover:text-foreground'
                  }`}
                >
                  {z.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="overflow-x-auto max-h-96">
          <table className="w-full text-left text-xs">
            <thead className="bg-background/60 text-gray-400 uppercase tracking-wider border-b border-border sticky top-0">
              <tr>
                <th className="p-3.5">Arah</th>
                <th className="p-3.5">Zona</th>
                <th className="p-3.5">Alur Jaringan (Source ➔ Destination)</th>
                <th className="p-3.5">Layanan / Target</th>
                <th className="p-3.5">Proto / State</th>
                <th className="p-3.5">Sesi & Queue</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border font-mono">
              {filteredFlows.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-6 text-center text-gray-500">
                    Tidak ada alur jaringan aktif yang cocok dengan filter saat ini.
                  </td>
                </tr>
              ) : (
                filteredFlows.map((f: any, idx: number) => {
                  const qBytes = (f.tx_queue_bytes || 0) + (f.rx_queue_bytes || 0);
                  const flowPath =
                    f.direction === 'inbound'
                      ? `${f.remote_ip} ➔ ${f.local_ip}:${f.local_port}`
                      : `${f.local_ip}:${f.local_port} ➔ ${f.remote_ip}:${f.remote_port}`;
                  return (
                    <tr key={idx} className="hover:bg-gray-500/5 transition-colors">
                      <td className="p-3.5">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${directionBadgeClass(f.direction)}`}>
                          {f.direction}
                        </span>
                      </td>
                      <td className="p-3.5">
                        <span className={`px-2 py-0.5 rounded border text-[10px] font-bold uppercase ${zoneBadgeClass(f.zone)}`}>
                          {f.zone}
                        </span>
                      </td>
                      <td className="p-3.5 text-foreground font-semibold">{flowPath}</td>
                      <td className="p-3.5 font-sans font-medium text-blue-400">{f.service}</td>
                      <td className="p-3.5">
                        <span className="text-gray-400 mr-1.5">{f.protocol}</span>
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            f.state === 'ESTABLISHED'
                              ? 'bg-emerald-500/10 text-emerald-500'
                              : 'bg-amber-500/10 text-amber-400'
                          }`}
                        >
                          {f.state}
                        </span>
                      </td>
                      <td className="p-3.5 text-gray-300">
                        <span className="font-bold text-foreground">{f.connections}x</span>
                        {qBytes > 0 && (
                          <span className="ml-2 text-[10px] text-emerald-400">
                            Q: {formatBytes(qBytes)}
                          </span>
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

      {/* Bandwidth History Visualizer & Network Latency Probe */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Bandwidth Chart (2 cols) */}
        <div className="lg:col-span-2 rounded-xl border border-border bg-card p-5 shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-bold text-sm text-foreground">Real-Time Bandwidth Throughput (5s Interval)</h3>
              <p className="text-xs text-gray-500">Sampel kecepatan Inbound (RX) vs Outbound (TX) dari ring-buffer memori</p>
            </div>
            <div className="flex items-center gap-4 text-xs font-medium">
              <span className="flex items-center gap-1.5 text-emerald-500">
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-500"></span> RX (Download)
              </span>
              <span className="flex items-center gap-1.5 text-blue-500">
                <span className="w-2.5 h-2.5 rounded-full bg-blue-500"></span> TX (Upload)
              </span>
            </div>
          </div>

          <div className="h-44 flex items-end gap-1.5 pt-6 pb-2 border-b border-border">
            {history.length === 0 ? (
              <div className="w-full text-center text-xs text-gray-500 self-center">
                Collecting live network throughput samples...
              </div>
            ) : (
              (() => {
                const maxVal = Math.max(
                  ...history.map((h: any) => Math.max(h.rx_bytes_per_sec || 0, h.tx_bytes_per_sec || 0)),
                  1024
                );
                return history.map((pt: any, idx: number) => {
                  const rxPct = Math.min(100, Math.max(6, Math.round(((pt.rx_bytes_per_sec || 0) / maxVal) * 100)));
                  const txPct = Math.min(100, Math.max(6, Math.round(((pt.tx_bytes_per_sec || 0) / maxVal) * 100)));
                  return (
                    <div key={idx} className="flex-1 flex flex-col justify-end items-center gap-1 group relative h-full">
                      <div className="absolute -top-9 hidden group-hover:flex flex-col items-center bg-slate-900 text-white text-[10px] p-1.5 rounded shadow z-10 whitespace-nowrap font-mono">
                        <span>
                          {pt.time} | RX: {formatBytes(pt.rx_bytes_per_sec)}/s | TX: {formatBytes(pt.tx_bytes_per_sec)}/s
                        </span>
                      </div>
                      <div
                        className="w-full bg-emerald-500/80 rounded-t hover:bg-emerald-400 transition-all"
                        style={{ height: `${rxPct}%` }}
                      ></div>
                      <div
                        className="w-full bg-blue-500/80 rounded-t hover:bg-blue-400 transition-all"
                        style={{ height: `${txPct}%` }}
                      ></div>
                    </div>
                  );
                });
              })()
            )}
          </div>
        </div>

        {/* Network Connectivity & Latency Probe (1 col) */}
        <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-4">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-amber-500" />
            <div>
              <h3 className="font-bold text-sm text-foreground">Latency & Port Probe</h3>
              <p className="text-xs text-gray-500">Uji latensi TCP & konektivitas dari server</p>
            </div>
          </div>

          <form onSubmit={(e) => handleRunProbe(e)} className="space-y-3 text-xs">
            <div className="flex gap-2">
              <input
                type="text"
                value={probeTarget}
                onChange={(e) => setProbeTarget(e.target.value)}
                placeholder="Host / IP (e.g. 1.1.1.1)"
                className="flex-1 px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono"
              />
              <input
                type="number"
                value={probePort}
                onChange={(e) => setProbePort(e.target.value)}
                placeholder="Port"
                className="w-20 px-2.5 py-2 rounded-lg bg-background border border-border text-foreground font-mono"
              />
            </div>

            <button
              type="submit"
              disabled={probing}
              className="w-full py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold transition-all disabled:opacity-50"
            >
              {probing ? 'Probing Target...' : 'Run Connectivity Probe'}
            </button>
          </form>

          {/* Quick Presets */}
          <div className="space-y-1.5">
            <span className="text-[10px] text-gray-400 uppercase font-semibold">Quick Targets:</span>
            <div className="flex flex-wrap gap-1.5 text-[11px] font-mono">
              {[
                { label: 'Cloudflare DNS', host: '1.1.1.1', port: 53 },
                { label: 'GitHub HTTPS', host: 'github.com', port: 443 },
                { label: 'Prometheus :9090', host: 'prometheus', port: 9090 },
                { label: 'PostgreSQL :5432', host: 'postgres', port: 5432 },
                { label: 'MySQL :3306', host: 'mysql', port: 3306 },
                { label: 'MQTT :1883', host: 'mosquitto', port: 1883 },
              ].map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  onClick={() => {
                    setProbeTarget(preset.host);
                    setProbePort(String(preset.port));
                    handleRunProbe(undefined, preset.host, preset.port);
                  }}
                  className="px-2 py-1 rounded border border-border bg-background hover:border-blue-500 text-gray-300 transition-colors"
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </div>

          {probeResult && (
            <div
              className={`p-3 rounded-lg border text-xs font-mono space-y-1 ${
                probeResult.reachable
                  ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                  : 'bg-rose-500/10 border-rose-500/20 text-rose-400'
              }`}
            >
              <div className="flex items-center justify-between font-bold">
                <span className="flex items-center gap-1.5">
                  {probeResult.reachable ? (
                    <CheckCircle2 className="w-3.5 h-3.5" />
                  ) : (
                    <XCircle className="w-3.5 h-3.5" />
                  )}
                  {probeResult.target}:{probeResult.port}
                </span>
                <span>{probeResult.latency_ms} ms</span>
              </div>
              {probeResult.resolved_ip && (
                <div className="text-[11px] text-gray-400">Resolved IP: {probeResult.resolved_ip}</div>
              )}
              {probeResult.error && (
                <div className="text-[11px] text-rose-400 break-all">{probeResult.error}</div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Network Interfaces Table (with Per-Interface Live Speed RX/s & TX/s) */}
      <div className="rounded-xl border border-border bg-card overflow-hidden shadow-sm">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Wifi className="w-4 h-4 text-blue-500" />
            <div>
              <h3 className="font-bold text-sm text-foreground">
                Host Network Interfaces ({interfaces.length})
              </h3>
              <p className="text-xs text-gray-500">
                NIC Fisik (LAN), Tailscale VPN, & Docker Bridge beserta kecepatan trafik aktif per-interface
              </p>
            </div>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-background/60 text-gray-400 uppercase tracking-wider border-b border-border">
              <tr>
                <th className="p-3.5">Interface</th>
                <th className="p-3.5">Type</th>
                <th className="p-3.5">State</th>
                <th className="p-3.5">IPv4 Address</th>
                <th className="p-3.5">Live RX/s</th>
                <th className="p-3.5">Live TX/s</th>
                <th className="p-3.5">Total RX</th>
                <th className="p-3.5">Total TX</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border font-mono">
              {interfaces.length === 0 ? (
                <tr>
                  <td colSpan={8} className="p-6 text-center text-gray-500">
                    Loading network interfaces...
                  </td>
                </tr>
              ) : (
                interfaces.map((iface: any) => (
                  <tr key={iface.name} className="hover:bg-gray-500/5 transition-colors">
                    <td className="p-3.5">
                      <span className="font-bold text-foreground block">{iface.name}</span>
                      <span className="text-[10px] text-gray-500">
                        MTU {iface.mtu}
                        {iface.speed_mbps > 0 ? ` • ${iface.speed_mbps} Mbps` : ''}
                      </span>
                    </td>
                    <td className="p-3.5">
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                          iface.type === 'vpn'
                            ? 'bg-purple-500/10 text-purple-400 border border-purple-500/20'
                            : iface.type === 'docker'
                            ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20'
                            : iface.type === 'loopback'
                            ? 'bg-gray-500/10 text-gray-400'
                            : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                        }`}
                      >
                        {iface.type}
                      </span>
                    </td>
                    <td className="p-3.5">
                      <span
                        className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${
                          iface.is_up
                            ? 'bg-emerald-500/10 text-emerald-500'
                            : 'bg-rose-500/10 text-rose-500'
                        }`}
                      >
                        {iface.is_up ? 'UP' : 'DOWN'}
                      </span>
                    </td>
                    <td className="p-3.5 text-blue-500 font-bold">
                      {iface.ipv4 || '-'}
                      {iface.mac && (
                        <span className="text-gray-500 font-normal text-[10px] block">{iface.mac}</span>
                      )}
                    </td>
                    <td className="p-3.5 text-emerald-400 font-bold">
                      {formatBytes(iface.rx_bytes_per_sec || 0)}/s
                    </td>
                    <td className="p-3.5 text-blue-400 font-bold">
                      {formatBytes(iface.tx_bytes_per_sec || 0)}/s
                    </td>
                    <td className="p-3.5 text-gray-300">{formatBytes(iface.rx_bytes)}</td>
                    <td className="p-3.5 text-gray-300">{formatBytes(iface.tx_bytes)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Container Network Traffic Ranking */}
      {containerNetwork.length > 0 && (
        <div className="rounded-xl border border-border bg-card overflow-hidden shadow-sm">
          <div className="p-4 border-b border-border flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Server className="w-4 h-4 text-emerald-500" />
              <div>
                <h3 className="font-bold text-sm text-foreground">
                  Pemakaian Trafik Jaringan per Container ({containerNetwork.length})
                </h3>
                <p className="text-xs text-gray-500">
                  Urutan container berdasarkan volume kumulatif pertukaran data jaringan (RX / TX)
                </p>
              </div>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 p-4">
            {containerNetwork.slice(0, 12).map((c: any) => {
              const maxContBytes = Math.max(containerNetwork[0]?.total_bytes || 1, 1);
              const pct = Math.min(100, Math.max(4, Math.round((c.total_bytes / maxContBytes) * 100)));
              return (
                <div key={c.name} className="p-3 rounded-lg border border-border bg-background space-y-2 text-xs font-mono">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-foreground truncate">{c.name}</span>
                    <span className="text-[10px] text-gray-400">{c.net_io}</span>
                  </div>
                  <div className="w-full h-1.5 bg-gray-500/10 rounded-full overflow-hidden">
                    <div className="h-full bg-emerald-500 rounded-full" style={{ width: `${pct}%` }}></div>
                  </div>
                  <div className="flex justify-between text-[10px] text-gray-400">
                    <span>RX: {formatBytes(c.rx_bytes)}</span>
                    <span>TX: {formatBytes(c.tx_bytes)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Active Listening Ports & Docker Networks */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 rounded-xl border border-border bg-card overflow-hidden shadow-sm">
          <div className="p-4 border-b border-border flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Radio className="w-4 h-4 text-emerald-500" />
              <div>
                <h3 className="font-bold text-sm text-foreground">
                  Active Listening Ports ({filteredPorts.length})
                </h3>
                <p className="text-xs text-gray-500">
                  Port TCP/UDP yang sedang terbuka & siap menerima koneksi di host server
                </p>
              </div>
            </div>

            <div className="relative w-full sm:w-56">
              <Search className="w-3.5 h-3.5 text-gray-400 absolute left-3 top-2.5" />
              <input
                type="text"
                placeholder="Filter port or service..."
                value={portSearch}
                onChange={(e) => setPortSearch(e.target.value)}
                className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          </div>

          <div className="overflow-x-auto max-h-96">
            <table className="w-full text-left text-xs">
              <thead className="bg-background/60 text-gray-400 uppercase tracking-wider border-b border-border sticky top-0">
                <tr>
                  <th className="p-3.5">Port</th>
                  <th className="p-3.5">Protocol</th>
                  <th className="p-3.5">Bind Address</th>
                  <th className="p-3.5">Service / Process</th>
                  <th className="p-3.5">State</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border font-mono">
                {filteredPorts.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="p-6 text-center text-gray-500">
                      No matching listening ports found.
                    </td>
                  </tr>
                ) : (
                  filteredPorts.map((p: any, i: number) => (
                    <tr key={i} className="hover:bg-gray-500/5 transition-colors">
                      <td className="p-3.5 font-bold text-blue-500">:{p.port}</td>
                      <td className="p-3.5 text-gray-400">{p.protocol}</td>
                      <td className="p-3.5 text-foreground">{p.bind_address}</td>
                      <td className="p-3.5 font-sans font-medium text-foreground">{p.service}</td>
                      <td className="p-3.5">
                        <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-500 text-[10px] font-bold">
                          {p.status}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Docker Networks Card */}
        <div className="rounded-xl border border-border bg-card p-5 shadow-sm space-y-4">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-blue-500" />
            <div>
              <h3 className="font-bold text-sm text-foreground">Docker Networks</h3>
              <p className="text-xs text-gray-500">Container virtual bridge & host networks</p>
            </div>
          </div>

          <div className="space-y-2.5 text-xs font-mono">
            {dockerNetworks.map((dn: any) => (
              <div
                key={dn.id || dn.name}
                className="flex items-center justify-between p-3 rounded-lg border border-border bg-background"
              >
                <div>
                  <span className="font-bold text-foreground block">{dn.name}</span>
                  <span className="text-[10px] text-gray-500 uppercase">Scope: {dn.scope || 'local'}</span>
                </div>
                <span className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 text-[10px] font-bold uppercase border border-blue-500/20">
                  {dn.driver}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
