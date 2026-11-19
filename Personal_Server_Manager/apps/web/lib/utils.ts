import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes: number, decimals = 1) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

export function formatDuration(seconds: number) {
  if (!seconds || seconds <= 0) return '0s';
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (mins === 0) return `${secs}s`;
  return `${mins}m ${secs}s`;
}

export interface ProjectAccessInfo {
  domain: string;
  domainUrl: string;
  hostPort: string | null;
  lanIp: string;
  lanUrl: string | null;
  tailscaleIp: string | null;
  tailscaleUrl: string | null;
  hostsFileUrl: string;
}

const IPV4_REGEX = /^\d{1,3}(?:\.\d{1,3}){3}$/;

export function resolveProjectAccess(project: any, servers: any[] = []): ProjectAccessInfo {
  const rawName = (project?.name || 'app').toLowerCase().trim();
  const domainSlug = rawName.replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '') || 'app';
  const rawDomain = (project?.domain || '').trim().toLowerCase();
  const domain = rawDomain
    ? (rawDomain.includes('.') ? rawDomain : `${rawDomain}.homelab`)
    : `${domainSlug}.homelab`;

  const portMappings = (project?.port_mappings || '3000:3000').trim();
  const firstPair = portMappings.split(',')[0]?.trim() || '';
  const rawHostPort = firstPair.includes(':') ? firstPair.split(':')[0].trim() : firstPair;
  const hostPort = rawHostPort && /^\d+$/.test(rawHostPort) ? rawHostPort : null;

  const targetServer = Array.isArray(servers)
    ? servers.find((s) => s.id === project?.server_id)
    : undefined;
  const serverHostname = (targetServer?.hostname || '').trim();
  const windowHost =
    typeof window !== 'undefined' ? (window.location.hostname || '').trim() : '';

  let lanIp = '192.168.25.105';
  if (project?.server_ip && IPV4_REGEX.test(String(project.server_ip).trim())) {
    lanIp = String(project.server_ip).trim();
  } else if (
    serverHostname &&
    IPV4_REGEX.test(serverHostname) &&
    serverHostname !== '127.0.0.1' &&
    serverHostname !== '0.0.0.0'
  ) {
    lanIp = serverHostname;
  } else if (
    windowHost &&
    IPV4_REGEX.test(windowHost) &&
    windowHost !== '127.0.0.1' &&
    windowHost !== '0.0.0.0'
  ) {
    lanIp = windowHost;
  }

  const tailscaleIp =
    project?.server_tailscale_ip && String(project.server_tailscale_ip).trim()
      ? String(project.server_tailscale_ip).trim()
      : null;

  const domainUrl = project?.access_urls?.domain || `http://${domain}`;
  const lanUrl = project?.access_urls?.lan_ip || (hostPort ? `http://${lanIp}:${hostPort}` : null);
  const tailscaleUrl =
    project?.access_urls?.tailscale_ip ||
    (tailscaleIp && hostPort ? `http://${tailscaleIp}:${hostPort}` : null);
  const hostsFileUrl = `http://${lanIp}:8000/api/v1/dns/hosts-file`;

  return {
    domain,
    domainUrl,
    hostPort,
    lanIp,
    lanUrl,
    tailscaleIp,
    tailscaleUrl,
    hostsFileUrl,
  };
}


