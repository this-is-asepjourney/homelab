'use client';

import React, { useState, useEffect, useRef } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  FolderGit2,
  Rocket,
  Key,
  Settings,
  RefreshCw,
  ArrowLeft,
  Plus,
  Upload,
  Copy,
  Check,
  GitBranch,
  Database,
  CheckCircle2,
  Table,
  HardDrive,
  Globe,
  ExternalLink,
  Terminal,
  AlertTriangle,
  Play,
  Square,
  FileCode,
  Download,
  Eye,
  EyeOff,
  Pencil,
  Trash2,
  X,
  Search,
  FileText,
  List,
  Link2,
  Unlink,
  Activity,
  Server,
  Sparkles,
  Save,
  AlertCircle,
  RotateCcw,
  Wrench,
} from 'lucide-react';
import { api, formatBytes, getApiBase } from '../../../lib/api';
import { resolveProjectAccess } from '../../../lib/utils';
import { DeployProjectModal } from '../../../components/deploy-project-modal';

const RESERVED_SYSTEM_PORTS: Record<number, string> = {
  53: 'CoreDNS (:53)',
  80: 'Nginx Ingress Router (:80)',
  1883: 'Mosquitto MQTT (:1883)',
  3000: 'Grafana (:3000)',
  3001: 'Uptime Kuma (:3001)',
  3005: 'PSM Console Web (:3005)',
  3306: 'MySQL 8.4 (:3306)',
  5432: 'PostgreSQL 16 (:5432)',
  6379: 'Redis 7 (:6379)',
  8000: 'PAP Core API Gateway (:8000)',
  8001: 'PSM Control Plane API (:8001)',
  8080: 'Nextcloud (:8080)',
  8081: 'cAdvisor (:8081)',
  9000: 'MinIO S3 API (:9000)',
  9002: 'MinIO Web Console (:9002)',
  9090: 'Prometheus (:9090)',
  9100: 'Node Exporter (:9100)',
  9273: 'Telegraf Exporter (:9273)',
};

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const projectId = params.projectId as string;

  const [project, setProject] = useState<any>(null);
  const [servers, setServers] = useState<any[]>([]);
  const [deployments, setDeployments] = useState<any[]>([]);
  const [envVars, setEnvVars] = useState<any[]>([]);
  const [dbInfo, setDbInfo] = useState<any>(null);
  const [containerInfo, setContainerInfo] = useState<any>(null);
  const [containerLogs, setContainerLogs] = useState<string>('');
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [restartingContainer, setRestartingContainer] = useState(false);
  const [autoRefreshLogs, setAutoRefreshLogs] = useState(true);
  const [copiedLogs, setCopiedLogs] = useState(false);
  const [activeTab, setActiveTab] = useState<'overview' | 'deployments' | 'terminal' | 'dockerfile' | 'database' | 'environment' | 'settings'>('terminal');
  const [loading, setLoading] = useState(true);
  const [deploying, setDeploying] = useState(false);
  const [deployModalOpen, setDeployModalOpen] = useState(false);
  const [portMappingsInput, setPortMappingsInput] = useState('');
  const [recommendedPort, setRecommendedPort] = useState<number>(3100);
  const [reservedPorts, setReservedPorts] = useState<Record<number, string>>(RESERVED_SYSTEM_PORTS);
  const [savingPorts, setSavingPorts] = useState(false);
  const [provisioningDb, setProvisioningDb] = useState(false);
  const [customDbName, setCustomDbName] = useState('');
  const [selectedDbType, setSelectedDbType] = useState<string>('postgres');
  const [copiedWebhook, setCopiedWebhook] = useState(false);
  const [autoInstallingWebhook, setAutoInstallingWebhook] = useState(false);
  const [webhookInstallMsg, setWebhookInstallMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [copiedDbUrl, setCopiedDbUrl] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const terminalEndRef = useRef<HTMLDivElement>(null);

  // Dockerfile Setup Assistant state
  const [dockerfileData, setDockerfileData] = useState<any>(null);
  const [dockerfileContent, setDockerfileContent] = useState<string>('');
  const [dockerignoreContent, setDockerignoreContent] = useState<string>('');
  const [dockerfileSubTab, setDockerfileSubTab] = useState<'dockerfile' | 'dockerignore'>('dockerfile');
  const [savingDockerfile, setSavingDockerfile] = useState(false);
  const [loadingDockerfile, setLoadingDockerfile] = useState(false);
  const [dockerfileTemplates, setDockerfileTemplates] = useState<any>({});
  const [selectedTemplateKey, setSelectedTemplateKey] = useState<string>('');
  const [copiedDockerfile, setCopiedDockerfile] = useState(false);
  const [saveSuccessMsg, setSaveSuccessMsg] = useState('');

  // New & editable env var state
  const [newKey, setNewKey] = useState('');
  const [newValue, setNewValue] = useState('');
  const [showNewEnvValue, setShowNewEnvValue] = useState(false);
  const [envSearch, setEnvSearch] = useState('');
  const [envEditorMode, setEnvEditorMode] = useState<'list' | 'bulk'>('list');
  const [revealAllEnv, setRevealAllEnv] = useState(false);
  const [revealedEnvKeys, setRevealedEnvKeys] = useState<Record<string, boolean>>({});
  const [copiedEnvKey, setCopiedEnvKey] = useState<string | null>(null);
  const [editingEnvOriginalKey, setEditingEnvOriginalKey] = useState<string | null>(null);
  const [editingEnvKey, setEditingEnvKey] = useState('');
  const [editingEnvValue, setEditingEnvValue] = useState('');
  const [showEditEnvValue, setShowEditEnvValue] = useState(false);
  const [savingEnv, setSavingEnv] = useState(false);
  const [envNotice, setEnvNotice] = useState<{ type: 'success' | 'error'; text: string; needsRedeploy?: boolean } | null>(null);
  const [bulkEnvText, setBulkEnvText] = useState('');
  const envFileInputRef = useRef<HTMLInputElement>(null);
  const [showResetEnvPanel, setShowResetEnvPanel] = useState(false);
  const [resetMode, setResetMode] = useState<'repair' | 'clean'>('repair');
  const [resetAppEnv, setResetAppEnv] = useState('local');
  const [resetAppUrl, setResetAppUrl] = useState('');
  const [resetIncludeDbVars, setResetIncludeDbVars] = useState(true);
  const [resetRegenerateAppKey, setResetRegenerateAppKey] = useState(false);
  const [resettingEnv, setResettingEnv] = useState(false);

  // Project settings & server deploy state
  const [projectName, setProjectName] = useState('');
  const [projectDescription, setProjectDescription] = useState('');
  const [projectFramework, setProjectFramework] = useState('Next.js');
  const [projectDomain, setProjectDomain] = useState('');
  const [projectBranch, setProjectBranch] = useState('main');
  const [projectRepoUrl, setProjectRepoUrl] = useState('');
  const [projectDockerfilePath, setProjectDockerfilePath] = useState('Dockerfile');
  const [savingProjectSettings, setSavingProjectSettings] = useState(false);
  const [deployingToServer, setDeployingToServer] = useState(false);
  const [projectUpdateSuccessMsg, setProjectUpdateSuccessMsg] = useState('');
  const [syncingDomain, setSyncingDomain] = useState(false);
  const [domainSyncMsg, setDomainSyncMsg] = useState<string | null>(null);
  const [copiedAccessKey, setCopiedAccessKey] = useState<'domain' | 'lan' | 'ts' | null>(null);
  const [maintenanceMode, setMaintenanceMode] = useState(false);
  const [maintenanceTitle, setMaintenanceTitle] = useState('');
  const [maintenanceMessage, setMaintenanceMessage] = useState('');
  const [maintenanceEta, setMaintenanceEta] = useState('');
  const [savingMaintenance, setSavingMaintenance] = useState(false);
  const [maintenanceNotice, setMaintenanceNotice] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [showMaintenancePreview, setShowMaintenancePreview] = useState(false);

  const fetchContainerLogs = async () => {
    try {
      setLoadingLogs(true);
      const [cInfo, cLogs] = await Promise.all([
        api.getProjectContainer(projectId).catch(() => null),
        api.getProjectContainerLogs(projectId, 250).catch(() => ({ logs: '' })),
      ]);
      setContainerInfo(cInfo);
      setContainerLogs(cLogs?.logs || '');
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingLogs(false);
    }
  };

  const handleRestartContainer = async () => {
    try {
      setRestartingContainer(true);
      await api.restartProjectContainer(projectId);
      await fetchContainerLogs();
    } catch (err: any) {
      alert(err.message || 'Gagal me-restart container');
    } finally {
      setRestartingContainer(false);
    }
  };

  const loadData = async () => {
    try {
      setLoading(true);
      const [p, d, env, dbData, s, cInfo, cLogs, portRes] = await Promise.all([
        api.getProject(projectId),
        api.getDeployments().then((list) => list.filter((item: any) => item.project_id === projectId)),
        api.getProjectEnvironment(projectId, true),
        api.getProjectDatabase(projectId),
        api.getServers().catch(() => []),
        api.getProjectContainer(projectId).catch(() => null),
        api.getProjectContainerLogs(projectId, 250).catch(() => ({ logs: '' })),
        api.getNextAvailablePort().catch(() => null),
      ]);
      if (portRes?.recommended_host_port) {
        setRecommendedPort(portRes.recommended_host_port);
      }
      if (portRes?.reserved_ports) {
        setReservedPorts({ ...portRes.reserved_ports, ...RESERVED_SYSTEM_PORTS });
      }
      setProject(p);
      setProjectName(p?.name || '');
      setProjectDescription(p?.description || '');
      setProjectFramework(p?.framework || 'Next.js');
      setProjectDomain(p?.domain || '');
      setProjectBranch(p?.branch || 'main');
      setProjectRepoUrl(p?.repository_url || '');
      setProjectDockerfilePath(p?.dockerfile_path || 'Dockerfile');
      setMaintenanceMode(Boolean(p?.maintenance_mode || p?.status === 'maintenance'));
      setMaintenanceTitle(p?.maintenance_title || '');
      setMaintenanceMessage(p?.maintenance_message || '');
      setMaintenanceEta(p?.maintenance_eta || '');
      setDeployments(d);
      setEnvVars(env);
      setBulkEnvText(
        Array.isArray(env)
          ? env.map((v: any) => `${v.key}=${v.value ?? ''}`).join('\n')
          : ''
      );
      setDbInfo(dbData);
      setServers(s);
      setContainerInfo(cInfo);
      setContainerLogs(cLogs?.logs || '');
      const fallbackPort = portRes?.recommended_host_port ? `${portRes.recommended_host_port}:3000` : '3100:3000';
      setPortMappingsInput(p?.port_mappings || fallbackPort);
      setSelectedDbType(p?.db_type || dbData?.db_type || 'postgres');
      if (dbData?.db_name) {
        setCustomDbName(dbData.db_name);
      } else if (p?.db_name) {
        setCustomDbName(p.db_name);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [projectId]);

  const loadDockerfile = async () => {
    try {
      setLoadingDockerfile(true);
      const [dfRes, tmplRes] = await Promise.all([
        api.getProjectDockerfile(projectId).catch(() => null),
        api.getDockerfileTemplates().catch(() => ({ templates: {} })),
      ]);
      if (dfRes) {
        setDockerfileData(dfRes);
        setDockerfileContent(dfRes.dockerfile || '');
        setDockerignoreContent(dfRes.dockerignore || '');
      }
      if (tmplRes && tmplRes.templates) {
        setDockerfileTemplates(tmplRes.templates);
      }
    } catch (e) {
      console.error('Failed to load dockerfile:', e);
    } finally {
      setLoadingDockerfile(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'dockerfile') {
      loadDockerfile();
    }
  }, [activeTab, projectId]);

  const handleSaveDockerfile = async () => {
    try {
      setSavingDockerfile(true);
      setSaveSuccessMsg('');
      await api.updateProjectDockerfile(projectId, {
        dockerfile: dockerfileContent,
        dockerignore: dockerignoreContent,
        dockerfile_path: dockerfileData?.dockerfile_path || 'Dockerfile',
      });
      setSaveSuccessMsg('✓ Dockerfile dan .dockerignore berhasil disimpan ke workspace project!');
      setTimeout(() => setSaveSuccessMsg(''), 4000);
      loadDockerfile();
    } catch (err: any) {
      alert(err.message || 'Gagal menyimpan Dockerfile');
    } finally {
      setSavingDockerfile(false);
    }
  };

  const handleApplyTemplate = (tmplKey: string) => {
    setSelectedTemplateKey(tmplKey);
    const tmpl = dockerfileTemplates[tmplKey];
    if (tmpl && tmpl.sample_content) {
      setDockerfileContent(tmpl.sample_content);
    }
  };

  const handleRegenerateDockerfile = async () => {
    try {
      setLoadingDockerfile(true);
      const res = await api.generateDockerfilePreview({
        framework: project?.framework || 'Next.js',
        template_key: selectedTemplateKey || undefined,
        project_name: project?.name,
      });
      if (res && res.dockerfile) {
        setDockerfileContent(res.dockerfile);
        if (res.dockerignore) setDockerignoreContent(res.dockerignore);
        setSaveSuccessMsg('✓ Dockerfile profesional di-generate ulang! Klik "Simpan ke Workspace" untuk menerapkan.');
        setTimeout(() => setSaveSuccessMsg(''), 4000);
      }
    } catch (err: any) {
      alert(err.message || 'Gagal generate ulang Dockerfile');
    } finally {
      setLoadingDockerfile(false);
    }
  };

  useEffect(() => {
    if (!autoRefreshLogs || activeTab !== 'terminal') return;
    const interval = setInterval(() => {
      fetchContainerLogs();
    }, 3500);
    return () => clearInterval(interval);
  }, [autoRefreshLogs, activeTab, projectId]);

  const handleTriggerDeploy = async () => {
    try {
      setDeploying(true);
      const dep = await api.redeployProject(projectId);
      router.push(`/deployments/${dep.id}`);
    } catch (err: any) {
      alert(err.message || 'Deployment trigger failed');
      setDeploying(false);
    }
  };

  const handleZipUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      setDeploying(true);
      const dep = await api.uploadZipDeployment(projectId, file, portMappingsInput, undefined, selectedDbType);
      router.push(`/deployments/${dep.id}`);
    } catch (err: any) {
      alert(err.message || 'ZIP upload deployment failed');
      setDeploying(false);
    }
  };

  const firstPortPair = (portMappingsInput || '').split(',')[0]?.trim() || '';
  const [parsedHostPortStr, parsedContainerPortStr] = firstPortPair.includes(':')
    ? firstPortPair.split(':').map((s) => s.trim())
    : [firstPortPair, '3000'];
  const parsedHostPortNum = parseInt(parsedHostPortStr, 10);
  const effectiveContainerPort = /^\d+$/.test(parsedContainerPortStr || '') ? parsedContainerPortStr : '3000';
  const rawSettingsPortConflict = !isNaN(parsedHostPortNum) ? reservedPorts[parsedHostPortNum] : null;
  const isCorePortConflict = !isNaN(parsedHostPortNum) && Boolean(RESERVED_SYSTEM_PORTS[parsedHostPortNum]);
  const currentProjectHostPorts = (project?.port_mappings || '')
    .split(',')
    .map((entry: string) => entry.trim().split(':')[0].trim())
    .filter(Boolean);
  const isSelfSettingsPort = Boolean(
    project &&
      rawSettingsPortConflict &&
      !isCorePortConflict &&
      (
        rawSettingsPortConflict.toLowerCase().includes((project.name || '').toLowerCase()) ||
        currentProjectHostPorts.includes(String(parsedHostPortNum))
      )
  );
  const settingsPortConflictReason = isSelfSettingsPort ? null : rawSettingsPortConflict;

  const handleUpdateProjectSettings = async (deployNow: boolean = false) => {
    if (settingsPortConflictReason) {
      alert(
        `Port host ${parsedHostPortStr} bentrok dengan ${settingsPortConflictReason}. Klik 'Auto-Bebas :${recommendedPort}' atau ganti host port terlebih dahulu.`
      );
      return;
    }
    try {
      if (deployNow) {
        setDeployingToServer(true);
      } else {
        setSavingProjectSettings(true);
      }
      setProjectUpdateSuccessMsg('');

      const updatePayload: any = {
        name: projectName.trim() || undefined,
        description: projectDescription.trim() || undefined,
        framework: projectFramework,
        domain: projectDomain.trim() || undefined,
        port_mappings: portMappingsInput.trim() || undefined,
        db_type: selectedDbType,
        db_name: customDbName.trim() || undefined,
        repository_url: project?.source_type === 'git' ? (projectRepoUrl.trim() || undefined) : undefined,
        branch: project?.source_type === 'git' ? (projectBranch.trim() || undefined) : undefined,
        dockerfile_path: projectDockerfilePath.trim() || undefined,
        redeploy: deployNow,
      };

      const updatedProject = await api.updateProject(projectId, updatePayload);

      if (deployNow) {
        let depId = updatedProject.deployment_id;
        if (!depId) {
          const dep = await api.redeployProject(projectId);
          depId = dep.id;
        }
        router.push(`/deployments/${depId}`);
      } else {
        setProjectUpdateSuccessMsg('✓ Pengaturan project berhasil diperbarui dan disinkronkan ke server.');
        setTimeout(() => setProjectUpdateSuccessMsg(''), 5000);
        await loadData();
      }
    } catch (err: any) {
      alert(err.message || 'Gagal memperbarui pengaturan project ke server');
    } finally {
      setSavingProjectSettings(false);
      setDeployingToServer(false);
    }
  };

  const handleRollback = async (deploymentId: string) => {
    if (!confirm('Are you sure you want to rollback to this release?')) return;
    try {
      const dep = await api.rollbackDeployment(deploymentId);
      router.push(`/deployments/${dep.id}`);
    } catch (err: any) {
      alert(err.message || 'Rollback failed');
    }
  };

  const handleAutoInstallWebhook = async () => {
    const repoUrl = project?.source?.repository_url || '';
    if (!repoUrl || !repoUrl.includes('github.com')) {
      alert('Project ini belum memiliki URL GitHub yang valid.');
      return;
    }
    const clean = repoUrl.replace(/\.git$/, '').replace(/\/$/, '');
    const parts = clean.split('github.com/')[1] || clean.split('github.com:')[1];
    if (!parts) {
      alert('Format URL GitHub tidak dikenali.');
      return;
    }
    const [owner, repo] = parts.split('/');
    if (!owner || !repo) {
      alert('Format owner/repo tidak valid.');
      return;
    }

    try {
      setAutoInstallingWebhook(true);
      setWebhookInstallMsg(null);
      const payloadUrl = typeof window !== 'undefined'
        ? `${window.location.origin}/api/v1/deployments/webhook/${projectId}`
        : `/api/v1/deployments/webhook/${projectId}`;
      const res = await api.setupGitHubWebhook(owner, repo, payloadUrl);
      setWebhookInstallMsg({ type: 'success', text: res.message || 'Webhook berhasil dipasang ke repositori GitHub Anda!' });
    } catch (err: any) {
      setWebhookInstallMsg({ type: 'error', text: err.message || 'Gagal memasang webhook otomatis ke GitHub.' });
    } finally {
      setAutoInstallingWebhook(false);
    }
  };

  const handleProvisionDatabase = async (e?: React.FormEvent, forcedType?: string) => {
    if (e) e.preventDefault();
    try {
      setProvisioningDb(true);
      const targetType = forcedType || selectedDbType || project?.db_type || 'postgres';
      const targetName = customDbName.trim() || undefined;
      // Update project record with the chosen engine & name
      await api.updateProject(projectId, {
        db_type: targetType,
        db_name: targetName,
      });
      // Provision/sync with provider engine
      await api.provisionProjectDatabase(projectId, targetName, true, targetType);
      await loadData();
    } catch (err: any) {
      alert(err.message || 'Failed to provision or sync database');
    } finally {
      setProvisioningDb(false);
    }
  };

  const handleSavePortMappings = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!portMappingsInput.trim()) return;
    try {
      setSavingPorts(true);
      await api.updateProject(projectId, { port_mappings: portMappingsInput.trim() });
      await loadData();
      alert('Port mappings Docker berhasil diperbarui!');
    } catch (err: any) {
      alert(err.message || 'Gagal memperbarui port mappings');
    } finally {
      setSavingPorts(false);
    }
  };

  const toggleRevealEnvKey = (key: string) => {
    setRevealedEnvKeys((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const isEnvValueVisible = (key: string) => revealAllEnv || Boolean(revealedEnvKeys[key]);

  const handleCopyEnvValue = (key: string, val: string) => {
    navigator.clipboard.writeText(val);
    setCopiedEnvKey(key);
    setTimeout(() => setCopiedEnvKey((cur) => (cur === key ? null : cur)), 1800);
  };

  const handleStartEditEnv = (item: any) => {
    setEditingEnvOriginalKey(item.key);
    setEditingEnvKey(item.key);
    setEditingEnvValue(item.value ?? '');
    setShowEditEnvValue(false);
    setEnvNotice(null);
  };

  const handleCancelEditEnv = () => {
    setEditingEnvOriginalKey(null);
    setEditingEnvKey('');
    setEditingEnvValue('');
    setShowEditEnvValue(false);
  };

  const handleSaveEditEnv = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedKey = editingEnvKey.trim();
    if (!trimmedKey) return;
    try {
      setSavingEnv(true);
      setEnvNotice(null);
      await api.upsertProjectEnvVar(projectId, {
        key: trimmedKey,
        value: editingEnvValue,
        original_key: editingEnvOriginalKey || undefined,
      });
      setEnvNotice({
        type: 'success',
        text: `Variable "${trimmedKey}" berhasil disimpan.`,
        needsRedeploy: true,
      });
      handleCancelEditEnv();
      await loadData();
    } catch (err: any) {
      setEnvNotice({ type: 'error', text: err.message || 'Gagal memperbarui variable.' });
    } finally {
      setSavingEnv(false);
    }
  };

  const handleDeleteEnvVar = async (key: string) => {
    if (!confirm(`Hapus environment variable "${key}"?`)) return;
    try {
      setSavingEnv(true);
      setEnvNotice(null);
      await api.deleteProjectEnvVar(projectId, key);
      if (editingEnvOriginalKey === key) {
        handleCancelEditEnv();
      }
      setEnvNotice({
        type: 'success',
        text: `Variable "${key}" telah dihapus.`,
        needsRedeploy: true,
      });
      await loadData();
    } catch (err: any) {
      setEnvNotice({ type: 'error', text: err.message || 'Gagal menghapus variable.' });
    } finally {
      setSavingEnv(false);
    }
  };

  const handleAddEnvVar = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedKey = newKey.trim();
    if (!trimmedKey) return;
    try {
      setSavingEnv(true);
      setEnvNotice(null);
      await api.upsertProjectEnvVar(projectId, {
        key: trimmedKey,
        value: newValue,
      });
      setNewKey('');
      setNewValue('');
      setShowNewEnvValue(false);
      setEnvNotice({
        type: 'success',
        text: `Variable "${trimmedKey}" berhasil ditambahkan/diperbarui tanpa mengubah variable lainnya.`,
        needsRedeploy: true,
      });
      await loadData();
    } catch (err: any) {
      setEnvNotice({ type: 'error', text: err.message || 'Gagal menyimpan environment variable' });
    } finally {
      setSavingEnv(false);
    }
  };

  const parseBulkEnvText = (text: string): Array<{ key: string; value: string }> => {
    const parsedMap = new Map<string, string>();
    const lines = text.split(/\r?\n/);
    for (const rawLine of lines) {
      const trimmed = rawLine.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const cleanLine = trimmed.startsWith('export ') ? trimmed.slice(7).trim() : trimmed;
      const eqIdx = cleanLine.indexOf('=');
      if (eqIdx <= 0) continue;
      const k = cleanLine.slice(0, eqIdx).trim();
      if (!k) continue;
      let v = cleanLine.slice(eqIdx + 1).trim();
      if (
        v.length >= 2 &&
        ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'")))
      ) {
        v = v.slice(1, -1);
      }
      parsedMap.set(k, v);
    }
    return Array.from(parsedMap.entries()).map(([key, value]) => ({ key, value }));
  };

  const handleSaveBulkEnv = async (andRedeploy: boolean = false) => {
    try {
      setSavingEnv(true);
      setEnvNotice(null);
      const parsedVars = parseBulkEnvText(bulkEnvText);
      if (parsedVars.length === 0 && bulkEnvText.trim() !== '') {
        setEnvNotice({
          type: 'error',
          text: 'Format .env tidak dikenali. Gunakan format KEY=VALUE per baris.',
        });
        return;
      }
      await api.updateProjectEnvironment(projectId, parsedVars);
      setEnvNotice({
        type: 'success',
        text: `${parsedVars.length} environment variable berhasil disimpan dari editor .env.`,
        needsRedeploy: !andRedeploy,
      });
      await loadData();
      if (andRedeploy) {
        await handleTriggerDeploy();
      }
    } catch (err: any) {
      setEnvNotice({ type: 'error', text: err.message || 'Gagal menyimpan editor .env.' });
    } finally {
      setSavingEnv(false);
    }
  };

  const handleOpenResetEnvPanel = () => {
    const fw = (project?.framework || '').toLowerCase();
    const isPhpOrLaravel = fw.includes('laravel') || fw.includes('php');
    setResetMode('repair');
    setResetAppEnv(isPhpOrLaravel ? 'local' : 'production');
    setResetAppUrl(access.tailscaleUrl || access.lanUrl || access.domainUrl || '');
    setResetIncludeDbVars(currentDbType !== 'none');
    setResetRegenerateAppKey(false);
    setShowResetEnvPanel((prev) => !prev);
  };

  const handleImportEnvFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      setBulkEnvText(text);
      setEnvEditorMode('bulk');
      setEnvNotice({
        type: 'success',
        text: `File "${file.name}" dimuat ke editor .env. Periksa isinya lalu klik "Simpan Semua (.env)" atau "Simpan & Deploy Ulang".`,
      });
    } catch {
      setEnvNotice({ type: 'error', text: 'Gagal membaca file .env yang dipilih.' });
    } finally {
      e.target.value = '';
    }
  };

  const handleDownloadEnvFile = () => {
    const lines = envVars.map((v: any) => `${v.key}=${v.value ?? ''}`).join('\n');
    const blob = new Blob([lines + '\n'], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const safeName = (project?.name || 'project').toLowerCase().replace(/[^a-z0-9_-]/g, '-');
    a.href = url;
    a.download = `${safeName}.env`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleResetEnvironment = async (andRedeploy: boolean = false) => {
    if (
      resetMode === 'clean' &&
      !confirm(
        'Mode Reset Bersih akan menghapus variabel custom yang tidak ada di .env.example dan menyusun ulang konfigurasi awal + database. Lanjutkan?'
      )
    ) {
      return;
    }
    try {
      setResettingEnv(true);
      setEnvNotice(null);
      const res = await api.resetProjectEnvironment(projectId, {
        mode: resetMode,
        preserve_app_key: !resetRegenerateAppKey,
        regenerate_app_key: resetRegenerateAppKey,
        include_db_vars: resetIncludeDbVars,
        app_env: resetAppEnv || undefined,
        app_url: resetAppUrl.trim() || undefined,
      });
      const clearedNote =
        res?.cleared_corrupted_keys && res.cleared_corrupted_keys.length > 0
          ? ` Membersihkan ${res.cleared_corrupted_keys.length} variabel rusak/masked.`
          : '';
      setEnvNotice({
        type: 'success',
        text: `${res?.message || 'Environment berhasil di-setup ulang.'}${clearedNote}`,
        needsRedeploy: !andRedeploy,
      });
      setShowResetEnvPanel(false);
      await loadData();
      if (andRedeploy) {
        await handleTriggerDeploy();
      }
    } catch (err: any) {
      setEnvNotice({ type: 'error', text: err.message || 'Gagal melakukan setup ulang environment.' });
    } finally {
      setResettingEnv(false);
    }
  };

  const buildMaintenancePreviewUrl = () => {
    const base = `${getApiBase()}/api/v1/projects/${projectId}/maintenance/preview`;
    const qs = new URLSearchParams();
    if (maintenanceTitle.trim()) qs.set('title', maintenanceTitle.trim());
    if (maintenanceMessage.trim()) qs.set('message', maintenanceMessage.trim());
    if (maintenanceEta.trim()) qs.set('eta', maintenanceEta.trim());
    const qStr = qs.toString();
    return qStr ? `${base}?${qStr}` : base;
  };

  const handleToggleMaintenance = async (enabled: boolean) => {
    try {
      setSavingMaintenance(true);
      setMaintenanceNotice(null);
      const res = await api.setProjectMaintenance(projectId, {
        enabled,
        title: maintenanceTitle.trim(),
        message: maintenanceMessage.trim(),
        eta: maintenanceEta.trim(),
        retry_after: 60,
      });
      const details: string[] = [];
      if (res?.router_synced) {
        details.push('Nginx Ingress (:80) tersinkron');
      }
      if (res?.container_sync?.applied) {
        details.push(`Container Laravel (${res.container_sync.driver})`);
      } else if (res?.container_sync?.reason) {
        details.push(res.container_sync.reason);
      }
      setMaintenanceNotice({
        type: 'success',
        text: `${res?.message || (enabled ? 'Mode Maintenance diaktifkan.' : 'Mode Maintenance dinonaktifkan.')}${
          details.length > 0 ? ` (${details.join(' • ')})` : ''
        }`,
      });
      await loadData();
    } catch (err: any) {
      setMaintenanceNotice({
        type: 'error',
        text: err.message || 'Gagal memperbarui Mode Maintenance project.',
      });
    } finally {
      setSavingMaintenance(false);
    }
  };

  if (!project && !loading) {
    return <div className="p-8 text-center text-gray-500">Project not found.</div>;
  }

  const handleSyncDomain = async () => {
    try {
      setSyncingDomain(true);
      setDomainSyncMsg(null);
      const res = await api.syncProjectDomain(projectId);
      setDomainSyncMsg(res?.message || `Route domain disinkronkan.`);
      await loadData();
    } catch (err: any) {
      setDomainSyncMsg(err.message || 'Gagal menyinkronkan domain route');
    } finally {
      setSyncingDomain(false);
    }
  };

  const handleCopyAccessUrl = (key: 'domain' | 'lan' | 'ts', url: string) => {
    navigator.clipboard.writeText(url);
    setCopiedAccessKey(key);
    setTimeout(() => setCopiedAccessKey(null), 1800);
  };

  const access = resolveProjectAccess(project, servers);
  const hostPortOnly = access.hostPort;
  const serverHost = access.lanIp;

  const currentDbType = (project?.db_type || dbInfo?.db_type || 'postgres').toLowerCase();
  const dbTypeLabel =
    currentDbType === 'mysql'
      ? 'MySQL'
      : currentDbType === 'mariadb'
      ? 'MariaDB'
      : currentDbType === 'sqlite'
      ? 'SQLite'
      : currentDbType === 'none'
      ? 'Stateless'
      : 'PostgreSQL';
  const dbTypeBadge =
    currentDbType === 'mysql'
      ? 'MySQL'
      : currentDbType === 'mariadb'
      ? 'MariaDB'
      : currentDbType === 'sqlite'
      ? 'SQLite'
      : currentDbType === 'none'
      ? 'No DB'
      : 'PG';

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-5">
        <div className="flex items-center gap-4">
          <button
            onClick={() => router.push('/projects')}
            className="p-2 rounded-lg border border-border hover:bg-card text-gray-400 hover:text-foreground transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <div className="flex items-center gap-2.5 flex-wrap">
              <h1 className="text-xl font-bold text-foreground">{project?.name || 'Project'}</h1>
              {(project?.maintenance_mode || project?.status === 'maintenance') ? (
                <button
                  type="button"
                  onClick={() => setActiveTab('settings')}
                  className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25 transition-colors"
                  title="Klik untuk membuka pengaturan Maintenance"
                >
                  <Wrench className="w-3 h-3" />
                  <span>MAINTENANCE</span>
                </button>
              ) : (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
                  {project?.status}
                </span>
              )}
              {containerInfo && (
                <button
                  onClick={() => setActiveTab('terminal')}
                  className={`flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold border transition-all ${
                    containerInfo.running
                      ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20 hover:bg-emerald-500/20'
                      : containerInfo.state === 'exited'
                      ? 'bg-rose-500/10 text-rose-500 border-rose-500/20 hover:bg-rose-500/20'
                      : 'bg-amber-500/10 text-amber-500 border-amber-500/20 hover:bg-amber-500/20'
                  }`}
                  title="Klik untuk membuka Terminal Logs container"
                >
                  <span className={`w-1.5 h-1.5 rounded-full ${containerInfo.running ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'}`} />
                  <span>Container: {containerInfo.running ? 'RUNNING' : (containerInfo.state === 'exited' ? `EXITED (${containerInfo.exit_code ?? 1})` : containerInfo.state || 'STOPPED')}</span>
                </button>
              )}
              <span className="flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-blue-500/10 text-blue-500 border border-blue-500/20">
                <Globe className="w-3 h-3" />
                <span>Port: {project?.port_mappings || '—'}</span>
              </span>
              {dbInfo && currentDbType !== 'none' && (
                <button
                  onClick={() => setActiveTab('database')}
                  className="flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-mono font-semibold bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 hover:bg-emerald-500/20 transition-colors"
                >
                  <Database className="w-3 h-3" />
                  <span>{dbTypeBadge}: {dbInfo.db_name || project?.name}</span>
                </button>
              )}
            </div>
            <p className="text-xs text-gray-400 font-mono mt-0.5">
              {project?.framework} • Hosted on {project?.server_name}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 flex-wrap">
          {/* Local DNS Ingress Link */}
          <a
            href={access.domainUrl}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-indigo-500/30 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 text-xs font-semibold transition-colors shadow-sm"
            title="Buka via Domain Pribadi & Ingress Router (Port 80)"
          >
            <Globe className="w-3.5 h-3.5" />
            <span>{access.domain}</span>
            <ExternalLink className="w-3 h-3" />
          </a>

          {/* Direct IP + Port Access Link */}
          {access.lanUrl && access.hostPort && (
            <a
              href={access.lanUrl}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-500 text-xs font-semibold font-mono transition-colors shadow-sm"
              title="Buka langsung via IP LAN Server & Port Docker"
            >
              <span>{access.lanIp}:{access.hostPort}</span>
              <ExternalLink className="w-3 h-3" />
            </a>
          )}

          <button
            onClick={() => setDeployModalOpen(true)}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl border border-blue-500/30 bg-blue-500/10 hover:bg-blue-500/20 text-blue-500 text-xs font-semibold shadow-sm transition-all"
            title="Ubah konfigurasi, port, database, atau source code lalu deploy ke server"
          >
            <FileCode className="w-4 h-4" />
            <span>Edit &amp; Deploy Ulang</span>
          </button>

          <button
            onClick={handleTriggerDeploy}
            disabled={deploying}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold shadow-md shadow-blue-600/20 transition-all disabled:opacity-50"
            title="Deploy commit/arsip terbaru langsung ke server"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${deploying ? 'animate-spin' : ''}`} />
            <span>{deploying ? 'Deploying...' : 'Deploy Ulang'}</span>
          </button>
        </div>
      </div>

      {/* Prominent Active Maintenance Banner */}
      {(project?.maintenance_mode || project?.status === 'maintenance') && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="p-2 rounded-lg bg-amber-500/20 text-amber-400 border border-amber-500/30 shrink-0 mt-0.5">
              <Wrench className="w-4 h-4" />
            </div>
            <div className="space-y-1 text-xs">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-bold text-amber-300">Mode Maintenance Sedang Aktif (HTTP 503)</span>
                {project?.maintenance_eta && (
                  <span className="px-2 py-0.5 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-200 font-mono text-[10px]">
                    ETA: {project.maintenance_eta}
                  </span>
                )}
              </div>
              <div className="text-foreground font-semibold">
                {project?.maintenance_title || 'Sedang Dalam Pemeliharaan'}
              </div>
              <p className="text-gray-300">
                {project?.maintenance_message ||
                  'Kami sedang melakukan pembaruan dan peningkatan sistem untuk memberikan layanan terbaik. Silakan coba kembali beberapa saat lagi.'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-wrap shrink-0">
            <a
              href={buildMaintenancePreviewUrl()}
              target="_blank"
              rel="noreferrer"
              className="px-3 py-1.5 rounded-lg border border-amber-500/40 bg-background/60 hover:bg-background text-amber-300 text-xs font-semibold flex items-center gap-1.5 transition-colors"
            >
              <Eye className="w-3.5 h-3.5" />
              <span>Preview Halaman</span>
              <ExternalLink className="w-3 h-3" />
            </a>
            <button
              type="button"
              disabled={savingMaintenance}
              onClick={() => handleToggleMaintenance(false)}
              className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50 transition-colors"
            >
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span>{savingMaintenance ? 'Memproses...' : 'Nonaktifkan Maintenance'}</span>
            </button>
          </div>
        </div>
      )}

      {/* Access Endpoints Card: Domain Pribadi & Direct IP:Port */}
      <div className="rounded-xl border border-border bg-card p-4 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-3">
          <div className="flex items-center gap-2">
            <Globe className="w-4 h-4 text-indigo-400" />
            <h2 className="text-xs font-bold uppercase tracking-wider text-foreground">
              Akses Aplikasi (Domain Pribadi &amp; IP:Port)
            </h2>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={handleSyncDomain}
              disabled={syncingDomain}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-indigo-500/30 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-400 text-[11px] font-semibold transition-colors disabled:opacity-50"
              title="Daftarkan ulang virtual host domain ke Nginx Ingress & CoreDNS"
            >
              <RefreshCw className={`w-3 h-3 ${syncingDomain ? 'animate-spin' : ''}`} />
              <span>{syncingDomain ? 'Menyinkronkan...' : 'Sync Domain Router'}</span>
            </button>
            <a
              href={access.hostsFileUrl}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border hover:bg-background text-gray-400 hover:text-foreground text-[11px] font-medium transition-colors"
              title="Unduh daftar IP & domain untuk file hosts Windows/Linux"
            >
              <Download className="w-3 h-3" />
              <span>Unduh hosts.txt</span>
            </a>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {/* Domain Pribadi */}
          <div className="flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg bg-background border border-border">
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
                Domain Pribadi (:80)
              </div>
              <a
                href={access.domainUrl}
                target="_blank"
                rel="noreferrer"
                className="text-xs font-mono font-semibold text-indigo-400 hover:underline flex items-center gap-1 truncate"
              >
                <span className="truncate">{access.domainUrl}</span>
                <ExternalLink className="w-3 h-3 shrink-0" />
              </a>
            </div>
            <button
              onClick={() => handleCopyAccessUrl('domain', access.domainUrl)}
              className="p-1.5 rounded hover:bg-card text-gray-400 hover:text-foreground shrink-0"
              title="Salin URL Domain"
            >
              {copiedAccessKey === 'domain' ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>

          {/* Direct IP LAN + Port */}
          <div className="flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg bg-background border border-border">
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
                IP LAN (Direct Port)
              </div>
              {access.lanUrl ? (
                <a
                  href={access.lanUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs font-mono font-semibold text-emerald-400 hover:underline flex items-center gap-1 truncate"
                >
                  <span className="truncate">{access.lanUrl}</span>
                  <ExternalLink className="w-3 h-3 shrink-0" />
                </a>
              ) : (
                <span className="text-xs font-mono text-gray-500">Port host belum di-map</span>
              )}
            </div>
            {access.lanUrl && (
              <button
                onClick={() => handleCopyAccessUrl('lan', access.lanUrl!)}
                className="p-1.5 rounded hover:bg-card text-gray-400 hover:text-foreground shrink-0"
                title="Salin URL IP LAN"
              >
                {copiedAccessKey === 'lan' ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            )}
          </div>

          {/* Tailscale IP + Port */}
          <div className="flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg bg-background border border-border">
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
                IP Tailscale (Remote)
              </div>
              {access.tailscaleUrl ? (
                <a
                  href={access.tailscaleUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs font-mono font-semibold text-sky-400 hover:underline flex items-center gap-1 truncate"
                >
                  <span className="truncate">{access.tailscaleUrl}</span>
                  <ExternalLink className="w-3 h-3 shrink-0" />
                </a>
              ) : (
                <span className="text-xs font-mono text-gray-500">Tailscale IP tidak dikonfigurasi</span>
              )}
            </div>
            {access.tailscaleUrl && (
              <button
                onClick={() => handleCopyAccessUrl('ts', access.tailscaleUrl!)}
                className="p-1.5 rounded hover:bg-card text-gray-400 hover:text-foreground shrink-0"
                title="Salin URL Tailscale"
              >
                {copiedAccessKey === 'ts' ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            )}
          </div>
        </div>

        {domainSyncMsg && (
          <p className="mt-2.5 text-[11px] font-mono text-indigo-400">
            {domainSyncMsg}
          </p>
        )}
      </div>

      {/* Tabs Header */}
      <div className="flex items-center gap-2 border-b border-border text-sm overflow-x-auto pb-px">
        {[
          {
            key: 'terminal',
            label: 'Terminal Logs',
            icon: Terminal,
            badge: containerInfo?.running ? 'Running' : (containerInfo?.state === 'exited' ? `Exited (${containerInfo?.exit_code ?? 1})` : null),
            badgeColor: containerInfo?.running ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-500 border border-rose-500/20',
          },
          { key: 'deployments', label: `Deployments (${deployments.length})`, icon: Rocket },
          {
            key: 'dockerfile',
            label: 'Dockerfile',
            icon: FileCode,
            badge: dockerfileData?.exists ? 'Workspace' : (dockerfileData?.is_generated ? 'Auto PaaS' : null),
            badgeColor: dockerfileData?.exists ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20' : 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/20',
          },
          { key: 'database', label: `Database (${dbTypeBadge})`, icon: Database },
          { key: 'environment', label: `Environment (${envVars.length})`, icon: Key },
          { key: 'overview', label: 'Source & Build', icon: FolderGit2 },
          { key: 'settings', label: 'Settings', icon: Settings },
        ].map((tab: any) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key as any)}
              className={`flex items-center gap-2 px-4 py-2.5 font-medium border-b-2 transition-colors whitespace-nowrap text-xs ${
                isActive
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-gray-500 hover:text-foreground'
              }`}
            >
              <Icon className="w-4 h-4" />
              <span>{tab.label}</span>
              {tab.badge && (
                <span className={`px-1.5 py-0.2 rounded text-[9px] font-mono font-bold ${tab.badgeColor || 'bg-gray-500/20 text-gray-400'}`}>
                  {tab.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Tab: Container Terminal Logs */}
      {activeTab === 'terminal' && (
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-card overflow-hidden shadow-sm">
            {/* Header Controls */}
            <div className="p-4 border-b border-border bg-background/50 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-blue-500/10 text-blue-500">
                  <Terminal className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-sm text-foreground">
                      Container Console &amp; Terminal Logs
                    </h3>
                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-bold uppercase ${
                        containerInfo?.running
                          ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                          : containerInfo?.state === 'exited'
                          ? 'bg-rose-500/10 text-rose-500 border border-rose-500/20'
                          : 'bg-amber-500/10 text-amber-500 border border-amber-500/20'
                      }`}
                    >
                      {containerInfo?.running ? '● RUNNING' : `● ${containerInfo?.state || 'STOPPED'} (Exit: ${containerInfo?.exit_code ?? 0})`}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 font-mono mt-0.5">
                    Container: {containerInfo?.container_name || project?.name} • Image: {containerInfo?.image || `${project?.name}:latest`}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2 flex-wrap">
                <label className="flex items-center gap-1.5 text-xs text-gray-400 mr-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={autoRefreshLogs}
                    onChange={(e) => setAutoRefreshLogs(e.target.checked)}
                    className="rounded border-border text-blue-600 focus:ring-blue-500"
                  />
                  <span>Live Stream (3s)</span>
                </label>

                <button
                  onClick={fetchContainerLogs}
                  disabled={loadingLogs}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border hover:bg-card text-foreground text-xs font-medium transition-colors disabled:opacity-50"
                  title="Muat ulang log"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingLogs ? 'animate-spin' : ''}`} />
                  <span>Refresh</span>
                </button>

                <button
                  onClick={handleRestartContainer}
                  disabled={restartingContainer}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 hover:bg-amber-500/20 text-amber-500 text-xs font-medium transition-colors disabled:opacity-50"
                  title="Restart container Docker"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${restartingContainer ? 'animate-spin' : ''}`} />
                  <span>{restartingContainer ? 'Restarting...' : 'Restart Container'}</span>
                </button>

                <button
                  onClick={() => {
                    if (containerLogs) {
                      navigator.clipboard.writeText(containerLogs);
                      setCopiedLogs(true);
                      setTimeout(() => setCopiedLogs(false), 2000);
                    }
                  }}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border hover:bg-card text-foreground text-xs font-medium transition-colors"
                >
                  {copiedLogs ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{copiedLogs ? 'Copied' : 'Copy'}</span>
                </button>
              </div>
            </div>

            {/* Error or Stopped Banner */}
            {containerInfo?.exit_code !== undefined && containerInfo.exit_code !== 0 && !containerInfo.running && (
              <div className="bg-rose-500/10 border-b border-rose-500/20 px-4 py-3 flex items-start gap-2.5 text-xs text-rose-400">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-rose-500" />
                <div>
                  <span className="font-bold">Container Berhenti atau Crash (Exit Code: {containerInfo.exit_code})</span>
                  <p className="text-gray-400 text-[11px] mt-0.5">
                    {containerInfo.error || 'Aplikasi di dalam container berhenti atau gagal saat booting. Silakan tinjau log terminal di bawah untuk melihat pesan error aplikasi.'}
                  </p>
                </div>
              </div>
            )}

            {/* Terminal Screen */}
            <div className="bg-[#0b0f19] p-4 text-xs font-mono text-gray-200 overflow-x-auto min-h-[380px] max-h-[560px] overflow-y-auto leading-relaxed border-t border-border/40 select-text">
              {containerLogs ? (
                containerLogs.split('\n').map((line, idx) => {
                  const isError = line.toLowerCase().includes('error') || line.toLowerCase().includes('fatal') || line.toLowerCase().includes('exception') || line.toLowerCase().includes('failed');
                  const isWarn = line.toLowerCase().includes('warn') || line.toLowerCase().includes('notice');
                  const isInfo = line.toLowerCase().includes('info') || line.toLowerCase().includes('ready') || line.toLowerCase().includes('running') || line.toLowerCase().includes('started');
                  return (
                    <div key={idx} className="flex gap-3 hover:bg-white/5 py-0.5 px-1 rounded">
                      <span className="text-gray-600 select-none text-[10px] w-8 text-right shrink-0">{idx + 1}</span>
                      <span
                        className={`break-all ${
                          isError
                            ? 'text-rose-400 font-semibold'
                            : isWarn
                            ? 'text-amber-300'
                            : isInfo
                            ? 'text-blue-300'
                            : 'text-gray-300'
                        }`}
                      >
                        {line}
                      </span>
                    </div>
                  );
                })
              ) : (
                <div className="flex flex-col items-center justify-center h-48 text-gray-500 space-y-2">
                  <Terminal className="w-8 h-8 opacity-40" />
                  <span>Tidak ada log output yang terekam atau container belum berjalan.</span>
                </div>
              )}
              <div ref={terminalEndRef} />
            </div>
          </div>
        </div>
      )}

      {/* Tab: Deployments */}
      {activeTab === 'deployments' && (
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-card overflow-hidden">
            <div className="p-4 border-b border-border flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <h3 className="font-bold text-sm text-foreground">Release History</h3>
                <p className="text-xs text-gray-500">
                  Daftar riwayat deployment &amp; build container Docker untuk project ini.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setDeployModalOpen(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-blue-500/30 bg-blue-500/10 hover:bg-blue-500/20 text-blue-500 text-xs font-semibold transition-all"
                  title="Sesuaikan konfigurasi project lalu deploy ulang"
                >
                  <FileCode className="w-3.5 h-3.5" />
                  <span>Edit &amp; Deploy Ulang</span>
                </button>
                <button
                  onClick={handleTriggerDeploy}
                  disabled={deploying}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold transition-all shadow-sm shadow-blue-600/20 disabled:opacity-50"
                  title="Deploy ulang rilis terbaru ke server"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${deploying ? 'animate-spin' : ''}`} />
                  <span>{deploying ? 'Deploying...' : 'Deploy Ulang'}</span>
                </button>
                <button
                  onClick={loadData}
                  className="px-2.5 py-1.5 rounded-lg border border-border text-xs text-gray-400 hover:text-foreground flex items-center gap-1 transition-colors"
                >
                  <RefreshCw className="w-3 h-3" />
                  <span>Refresh</span>
                </button>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-background/60 text-gray-400 uppercase tracking-wider border-b border-border">
                  <tr>
                    <th className="p-3.5">Release</th>
                    <th className="p-3.5">Status</th>
                    <th className="p-3.5">Commit / Image</th>
                    <th className="p-3.5">Triggered At</th>
                    <th className="p-3.5 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {deployments.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="p-8 text-center text-gray-500">
                        Belum ada riwayat deployment. Klik &quot;Deploy Ulang&quot; atau &quot;Edit &amp; Deploy Ulang&quot; untuk meluncurkan rilis container.
                      </td>
                    </tr>
                  ) : (
                    deployments.map((d) => (
                      <tr key={d.id} className="hover:bg-gray-500/5 transition-colors">
                        <td className="p-3.5 font-mono font-bold text-foreground">
                          v{d.version}
                        </td>
                        <td className="p-3.5">
                          <span
                            className={`px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase ${
                              d.status === 'success'
                                ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                                : d.status === 'failed'
                                ? 'bg-rose-500/10 text-rose-500'
                                : 'bg-amber-500/10 text-amber-500 animate-pulse'
                            }`}
                          >
                            {d.status}
                          </span>
                        </td>
                        <td className="p-3.5 font-mono text-gray-400">
                          {d.image_tag || d.commit_hash || 'HEAD'}
                        </td>
                        <td className="p-3.5 text-gray-400">
                          {new Date(d.created_at).toLocaleString()}
                        </td>
                        <td className="p-3.5 text-right space-x-2">
                          <Link
                            href={`/deployments/${d.id}`}
                            className="px-2.5 py-1 rounded border border-border hover:bg-gray-100 dark:hover:bg-gray-800 text-blue-500 font-medium"
                          >
                            Live Logs
                          </Link>
                          <button
                            onClick={handleTriggerDeploy}
                            disabled={deploying}
                            className="px-2.5 py-1 rounded border border-blue-500/30 bg-blue-500/10 hover:bg-blue-500/20 text-blue-500 font-semibold disabled:opacity-50"
                            title="Deploy ulang rilis project ke server"
                          >
                            Deploy Ulang
                          </button>
                          {d.status === 'success' && (
                            <button
                              onClick={() => handleRollback(d.id)}
                              className="px-2.5 py-1 rounded border border-border hover:bg-amber-500/10 text-amber-500 font-medium"
                              title="Rollback to this version"
                            >
                              Rollback
                            </button>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Tab: Dockerfile & PaaS Build Configuration */}
      {activeTab === 'dockerfile' && (
        <div className="space-y-6">
          {/* Header Card */}
          <div className="rounded-xl border border-indigo-500/20 bg-card p-5 space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                  <FileCode className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-sm text-foreground">Dockerfile & Build Configuration</h3>
                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border ${
                        dockerfileData?.exists
                          ? 'bg-blue-500/10 text-blue-400 border-blue-500/20'
                          : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                      }`}
                    >
                      {dockerfileData?.exists
                        ? `✓ Workspace: ${dockerfileData?.dockerfile_path || 'Dockerfile'}`
                        : '⚡ Auto-Generated (PaaS Standard)'}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                    Personal Server Manager mendukung deployment profesional dengan Dockerfile multi-stage production dan proteksi context .dockerignore.
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={loadDockerfile}
                  disabled={loadingDockerfile}
                  className="px-3 py-1.5 rounded-lg border border-border hover:bg-background text-gray-400 hover:text-foreground text-xs font-medium transition-colors flex items-center gap-1.5"
                  title="Muat ulang dari disk"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingDockerfile ? 'animate-spin' : ''}`} />
                  <span>Reload</span>
                </button>
                <button
                  type="button"
                  onClick={handleSaveDockerfile}
                  disabled={savingDockerfile}
                  className="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium transition-colors flex items-center gap-1.5 shadow-sm"
                >
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>{savingDockerfile ? 'Menyimpan...' : 'Simpan ke Workspace'}</span>
                </button>
              </div>
            </div>

            {/* Save Success Alert */}
            {saveSuccessMsg && (
              <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs flex items-center gap-2 animate-in fade-in duration-200">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>{saveSuccessMsg}</span>
              </div>
            )}

            {/* Template Presets Bar */}
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 p-3 rounded-lg bg-background border border-border text-xs">
              <div className="flex items-center gap-2 flex-1">
                <Sparkles className="w-4 h-4 text-amber-400 shrink-0" />
                <span className="text-gray-400 font-medium whitespace-nowrap">Template Stack Preset:</span>
                <select
                  value={selectedTemplateKey}
                  onChange={(e) => handleApplyTemplate(e.target.value)}
                  className="flex-1 px-3 py-1.5 rounded-lg bg-card border border-border text-foreground text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500"
                >
                  <option value="">Pilih Preset Template Profesional...</option>
                  {Object.keys(dockerfileTemplates).map((k) => (
                    <option key={k} value={k}>
                      {dockerfileTemplates[k].name} (Port: {dockerfileTemplates[k].default_port})
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                <button
                  type="button"
                  onClick={handleRegenerateDockerfile}
                  disabled={loadingDockerfile}
                  className="px-3 py-1.5 rounded-lg border border-indigo-500/30 hover:bg-indigo-500/10 text-indigo-400 text-xs font-medium transition-colors flex items-center gap-1"
                >
                  <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
                  <span>Generate Ulang Otomatis</span>
                </button>
              </div>
            </div>

            {/* File Switcher & Code Editor */}
            <div className="space-y-2">
              <div className="flex items-center justify-between border-b border-border pb-2">
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setDockerfileSubTab('dockerfile')}
                    className={`px-3 py-1.5 rounded-lg text-xs font-mono font-medium transition-colors flex items-center gap-1.5 ${
                      dockerfileSubTab === 'dockerfile'
                        ? 'bg-indigo-600/15 text-indigo-400 border border-indigo-500/30'
                        : 'text-gray-400 hover:text-foreground'
                    }`}
                  >
                    <FileCode className="w-3.5 h-3.5" />
                    <span>Dockerfile</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setDockerfileSubTab('dockerignore')}
                    className={`px-3 py-1.5 rounded-lg text-xs font-mono font-medium transition-colors flex items-center gap-1.5 ${
                      dockerfileSubTab === 'dockerignore'
                        ? 'bg-indigo-600/15 text-indigo-400 border border-indigo-500/30'
                        : 'text-gray-400 hover:text-foreground'
                    }`}
                  >
                    <span>.dockerignore</span>
                    <span className="px-1.5 py-0.2 rounded text-[9px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                      Context Guard
                    </span>
                  </button>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      const text = dockerfileSubTab === 'dockerfile' ? dockerfileContent : dockerignoreContent;
                      navigator.clipboard.writeText(text);
                      setCopiedDockerfile(true);
                      setTimeout(() => setCopiedDockerfile(false), 2000);
                    }}
                    className="px-2.5 py-1 rounded-md border border-border hover:bg-background text-gray-400 hover:text-foreground text-xs flex items-center gap-1 transition-colors"
                  >
                    {copiedDockerfile ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                    <span>{copiedDockerfile ? 'Copied' : 'Salin'}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const text = dockerfileSubTab === 'dockerfile' ? dockerfileContent : dockerignoreContent;
                      const fname = dockerfileSubTab === 'dockerfile' ? 'Dockerfile' : '.dockerignore';
                      const blob = new Blob([text], { type: 'text/plain' });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = fname;
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                    className="px-2.5 py-1 rounded-md border border-border hover:bg-background text-gray-400 hover:text-foreground text-xs flex items-center gap-1 transition-colors"
                  >
                    <Download className="w-3 h-3" />
                    <span>Unduh</span>
                  </button>
                </div>
              </div>

              {/* Textarea Code Editor */}
              <div className="relative rounded-lg border border-border bg-[#0d1117] overflow-hidden">
                <textarea
                  value={dockerfileSubTab === 'dockerfile' ? dockerfileContent : dockerignoreContent}
                  onChange={(e) => {
                    if (dockerfileSubTab === 'dockerfile') {
                      setDockerfileContent(e.target.value);
                    } else {
                      setDockerignoreContent(e.target.value);
                    }
                  }}
                  rows={20}
                  spellCheck={false}
                  className="w-full p-4 bg-transparent text-gray-200 font-mono text-xs focus:outline-none resize-y leading-relaxed"
                  placeholder={dockerfileSubTab === 'dockerfile' ? '# Masukkan konten Dockerfile...' : '# Masukkan pola .dockerignore...'}
                />
              </div>
            </div>

            {/* Architecture Highlights / Explanations */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 text-xs">
              <div className="p-3 rounded-lg border border-border bg-background/50 space-y-1">
                <div className="font-semibold text-foreground flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-blue-400" />
                  <span>Multi-Stage Standalone</span>
                </div>
                <p className="text-[11px] text-gray-400 leading-normal">
                  Memisahkan layer instalasi dependencies, build, dan runner runtime sehingga ukuran final image berkurang drastis (&lt;120MB) dan proses build cepat.
                </p>
              </div>

              <div className="p-3 rounded-lg border border-border bg-background/50 space-y-1">
                <div className="font-semibold text-foreground flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Non-Root Security User</span>
                </div>
                <p className="text-[11px] text-gray-400 leading-normal">
                  Container dijalankan oleh user aplikasi non-root terisolasi (seperti <code>nextjs</code>, <code>node</code>, <code>appuser</code>) untuk melindungi sistem host homelab.
                </p>
              </div>

              <div className="p-3 rounded-lg border border-border bg-background/50 space-y-1">
                <div className="font-semibold text-foreground flex items-center gap-1.5">
                  <FileCode className="w-3.5 h-3.5 text-purple-400" />
                  <span>Proteksi Context Guard</span>
                </div>
                <p className="text-[11px] text-gray-400 leading-normal">
                  File <code>.dockerignore</code> otomatis mencegah direktori berat host (<code>node_modules</code>, <code>.git</code>, <code>.venv</code>) dikirim ke daemon Docker.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'database' && (
        <div className="space-y-6">
          <div className="rounded-xl border border-border bg-card p-5 space-y-5">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
                  <Database className="w-5 h-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-sm text-foreground">{dbTypeLabel} Auto-Connect & Auto-Provision</h3>
                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${
                        dbInfo?.connected
                          ? 'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                          : 'bg-blue-500/10 text-blue-500 border border-blue-500/20'
                      }`}
                    >
                      {dbInfo?.connected ? 'CONNECTED & ACTIVE' : 'AUTO-PROVISION ON DEPLOY'}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 mt-0.5">
                    {dbInfo?.message || `Setiap kali project di-deploy, PSM otomatis memeriksa database di ${dbTypeLabel}, membuatnya jika belum ada, dan menghubungkannya ke container aplikasi.`}
                  </p>
                </div>
              </div>

              <form onSubmit={handleProvisionDatabase} className="flex flex-wrap items-center gap-2 text-xs">
                <select
                  value={selectedDbType}
                  onChange={(e) => setSelectedDbType(e.target.value)}
                  className="px-2.5 py-1.5 rounded-lg bg-background border border-border text-foreground font-mono text-xs focus:outline-none focus:ring-1 focus:ring-emerald-500"
                >
                  <option value="mysql">MySQL (:3306)</option>
                  <option value="postgres">PostgreSQL (:5432)</option>
                  <option value="sqlite">SQLite (File)</option>
                  <option value="none">Stateless (No DB)</option>
                </select>
                <input
                  type="text"
                  value={customDbName}
                  onChange={(e) => setCustomDbName(e.target.value)}
                  placeholder="database_name"
                  className="px-3 py-1.5 rounded-lg bg-background border border-border text-foreground font-mono text-xs w-36 focus:outline-none focus:ring-1 focus:ring-emerald-500"
                />
                <button
                  type="submit"
                  disabled={provisioningDb}
                  className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium flex items-center gap-1.5 disabled:opacity-50 whitespace-nowrap shadow-sm transition-colors"
                >
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>{provisioningDb ? 'Syncing...' : 'Save & Sync DB'}</span>
                </button>
              </form>
            </div>

            {/* Status Koneksi Database <-> Project Ter-deploy */}
            {(() => {
              const depConn = dbInfo?.deployment_connection;
              const connStatus = depConn?.status || (dbInfo?.connected ? 'configured_standby' : 'disconnected');
              const statusMeta: Record<string, { label: string; box: string; badge: string; icon: 'linked' | 'warn' | 'unlinked' }> = {
                connected: {
                  label: 'TERHUBUNG KE PROJECT TER-DEPLOY',
                  box: 'border-emerald-500/30 bg-emerald-500/5',
                  badge: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
                  icon: 'linked',
                },
                mismatch: {
                  label: 'KONFIGURASI ENV TIDAK COCOK',
                  box: 'border-amber-500/30 bg-amber-500/5',
                  badge: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
                  icon: 'warn',
                },
                degraded: {
                  label: 'CONTAINER AKTIF • DB BELUM TERVERIFIKASI',
                  box: 'border-amber-500/30 bg-amber-500/5',
                  badge: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
                  icon: 'warn',
                },
                configured_standby: {
                  label: 'SIAP • BELUM DI-DEPLOY',
                  box: 'border-blue-500/30 bg-blue-500/5',
                  badge: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
                  icon: 'linked',
                },
                stateless: {
                  label: 'STATELESS (TANPA DATABASE)',
                  box: 'border-border bg-background/60',
                  badge: 'bg-gray-500/15 text-gray-400 border-border',
                  icon: 'unlinked',
                },
                disconnected: {
                  label: 'TIDAK TERHUBUNG',
                  box: 'border-rose-500/30 bg-rose-500/5',
                  badge: 'bg-rose-500/15 text-rose-400 border-rose-500/30',
                  icon: 'unlinked',
                },
              };
              const meta = statusMeta[connStatus] || statusMeta.disconnected;

              return (
                <div className={`rounded-xl border p-4 space-y-4 ${meta.box}`}>
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div className="flex items-center gap-2.5 flex-wrap">
                      {meta.icon === 'linked' ? (
                        <Link2 className="w-4 h-4 text-emerald-400 shrink-0" />
                      ) : meta.icon === 'warn' ? (
                        <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
                      ) : (
                        <Unlink className="w-4 h-4 text-rose-400 shrink-0" />
                      )}
                      <h4 className="font-bold text-xs text-foreground">Status Koneksi Database &harr; Project Ter-deploy</h4>
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase border ${meta.badge}`}>
                        {meta.label}
                      </span>
                    </div>
                    {depConn?.latest_deployment_version != null && (
                      <span className="text-[11px] font-mono text-gray-400">
                        Deploy Terbaru: <strong className="text-foreground">v{depConn.latest_deployment_version}</strong> &bull; {depConn.latest_deployment_status || 'unknown'}
                      </span>
                    )}
                  </div>

                  <p className="text-xs text-gray-300 leading-relaxed">
                    {depConn?.summary || `Memeriksa keterhubungan container '${project?.name}' dengan database '${dbInfo?.db_name || currentDbType}'...`}
                  </p>

                  {dbInfo?.error && (
                    <div className="p-2.5 rounded-lg border border-rose-500/30 bg-rose-500/10 text-rose-300 text-[11px] font-mono">
                      Diagnostik DB Server: {dbInfo.error}
                    </div>
                  )}

                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
                    {/* 1. Container Ter-deploy */}
                    <div className="p-3 rounded-lg bg-background/80 border border-border space-y-1">
                      <span className="text-[11px] text-gray-400 block">Container Ter-deploy</span>
                      <div className="font-mono font-bold text-foreground truncate" title={depConn?.container_name || project?.name}>
                        {depConn?.container_name || project?.name}
                      </div>
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border ${
                        depConn?.container_running
                          ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                          : depConn?.container_exists
                          ? 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                          : 'bg-gray-500/10 text-gray-400 border-border'
                      }`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${depConn?.container_running ? 'bg-emerald-400 animate-pulse' : 'bg-gray-400'}`} />
                        {depConn?.container_running
                          ? 'RUNNING'
                          : depConn?.container_exists
                          ? (depConn?.container_state || 'STOPPED').toUpperCase()
                          : 'NOT CREATED'}
                      </span>
                    </div>

                    {/* 2. Docker Network */}
                    <div className="p-3 rounded-lg bg-background/80 border border-border space-y-1.5">
                      <span className="text-[11px] text-gray-400 block">Docker Network</span>
                      {depConn?.container_networks && depConn.container_networks.length > 0 ? (
                        <div className="flex flex-wrap gap-1.5">
                          {depConn.container_networks.map((net: string) => (
                            <span key={net} className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20 font-mono text-[11px] font-semibold">
                              {net}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-[11px] text-gray-500 font-mono">Belum terpasang ke network</span>
                      )}
                    </div>

                    {/* 3. Konfigurasi Env Aktif */}
                    <div className="p-3 rounded-lg bg-background/80 border border-border space-y-1">
                      <div className="flex items-center justify-between gap-1">
                        <span className="text-[11px] text-gray-400">Konfigurasi Env Aktif</span>
                        <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${
                          depConn?.env_matches_database
                            ? 'bg-emerald-500/10 text-emerald-400'
                            : 'bg-amber-500/10 text-amber-400'
                        }`}>
                          {depConn?.env_matches_database ? 'Cocok' : 'Tidak Cocok'}
                        </span>
                      </div>
                      <div className="font-mono text-[11px] text-foreground truncate">
                        {depConn?.env_db_connection || '?'}://{depConn?.env_db_host || '?'}/{depConn?.env_db_name || '?'}
                      </div>
                      <span className="text-[10px] text-gray-500 block">
                        Sumber: {depConn?.env_source === 'container' ? 'Runtime Container' : depConn?.env_source === 'project_env' ? 'Project Env' : 'Belum dikonfigurasi'}
                      </span>
                    </div>

                    {/* 4. Sesi DB Aktif */}
                    <div className="p-3 rounded-lg bg-background/80 border border-border space-y-1">
                      <span className="text-[11px] text-gray-400 block">Sesi DB Aktif</span>
                      <div className="flex items-center gap-1.5 font-mono font-bold text-sm text-foreground">
                        <Activity className="w-3.5 h-3.5 text-blue-400" />
                        <span>{depConn?.active_db_sessions ?? dbInfo?.active_connections ?? 0} koneksi</span>
                      </div>
                      <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold border ${
                        dbInfo?.connected
                          ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                          : 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                      }`}>
                        DB Server: {dbInfo?.connected ? 'Online' : 'Offline'}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* Connection Details Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-xs font-mono">
              <div className="p-3.5 rounded-xl bg-background border border-border">
                <span className="text-gray-400 font-sans block mb-1">Database Engine</span>
                <span className="text-sm font-bold text-foreground">
                  {currentDbType === 'mysql' && 'MySQL (Port 3306)'}
                  {currentDbType === 'mariadb' && 'MariaDB (Port 3306)'}
                  {currentDbType === 'postgres' && 'PostgreSQL (Port 5432)'}
                  {currentDbType === 'sqlite' && 'SQLite (Embedded File)'}
                  {currentDbType === 'none' && 'None / Stateless'}
                </span>
              </div>
              <div className="p-3.5 rounded-xl bg-background border border-border">
                <span className="text-gray-400 font-sans block mb-1">Database Name</span>
                <span className="text-sm font-bold text-emerald-500">{dbInfo?.db_name || (currentDbType === 'none' ? 'None' : 'N/A')}</span>
              </div>
              <div className="p-3.5 rounded-xl bg-background border border-border">
                <span className="text-gray-400 font-sans block mb-1">Docker Network Host</span>
                <span className="text-sm font-bold text-foreground">
                  {currentDbType === 'sqlite'
                    ? (dbInfo?.file_path || '/data/*.db')
                    : currentDbType === 'none'
                    ? 'N/A'
                    : `${dbInfo?.host || project?.server_ip || '192.168.25.105'}:${dbInfo?.port || (currentDbType === 'mysql' ? 3306 : 5432)}`}
                </span>
              </div>
              <div className="p-3.5 rounded-xl bg-background border border-border">
                <span className="text-gray-400 font-sans block mb-1">Storage Size & Tables</span>
                <span className="text-sm font-bold text-blue-500">
                  {formatBytes(dbInfo?.size_bytes || 0)} • {dbInfo?.tables_count ?? (dbInfo?.tables?.length || 0)} Tables
                </span>
              </div>
            </div>

            {/* Connection URL Copy Box */}
            {currentDbType !== 'none' && (
              <div className="space-y-2 text-xs">
                <label className="block text-gray-400 font-medium">
                  Auto-Injected {dbTypeLabel} Connection URI (<code>DATABASE_URL</code>):
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    readOnly
                    value={dbInfo?.masked_connection_url_internal || dbInfo?.connection_url_internal || ''}
                    className="flex-1 px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono text-xs"
                  />
                  <button
                    onClick={() => {
                      const urlToCopy = dbInfo?.connection_url_internal || dbInfo?.masked_connection_url_internal;
                      if (urlToCopy) {
                        navigator.clipboard.writeText(urlToCopy);
                        setCopiedDbUrl(true);
                        setTimeout(() => setCopiedDbUrl(false), 2000);
                      }
                    }}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border hover:bg-background text-foreground transition-all"
                  >
                    {copiedDbUrl ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copiedDbUrl ? 'Copied Full URL' : 'Copy Full URL'}</span>
                  </button>
                </div>
              </div>
            )}

            {/* Schema Tables List */}
            {currentDbType !== 'none' && (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-semibold text-foreground flex items-center gap-1.5">
                    <Table className="w-3.5 h-3.5 text-blue-500" />
                    <span>Tables in Database &apos;{dbInfo?.db_name}&apos; ({dbInfo?.tables?.length || 0})</span>
                  </span>
                </div>
                {dbInfo?.tables && dbInfo.tables.length > 0 ? (
                  <div className="flex flex-wrap gap-2 pt-1 max-h-48 overflow-y-auto">
                    {dbInfo.tables.map((tbl: string) => (
                      <span
                        key={tbl}
                        className="px-2.5 py-1 rounded-lg bg-background border border-border font-mono text-xs text-foreground"
                      >
                        {tbl}
                      </span>
                    ))}
                  </div>
                ) : (
                  <div className="p-4 rounded-lg bg-background border border-border text-xs text-gray-400">
                    Database <code>{dbInfo?.db_name}</code> sudah siap digunakan di {dbTypeLabel}. Tabel akan otomatis dibuat saat aplikasi menjalankan migrasi saat deploy (misalnya <code>php artisan migrate --force</code>, Prisma, atau SQLAlchemy).
                  </div>
                )}
              </div>
            )}

            {/* Project Ter-deploy yang Terhubung ke Database Ini */}
            {currentDbType !== 'none' && (
              <div className="space-y-3 pt-2 border-t border-border">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-semibold text-foreground flex items-center gap-1.5">
                    <Server className="w-3.5 h-3.5 text-emerald-500" />
                    <span>Project Ter-deploy yang Terhubung ke Database Ini ({dbInfo?.connected_projects?.length || 0})</span>
                  </span>
                </div>
                {dbInfo?.connected_projects && dbInfo.connected_projects.length > 0 ? (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {dbInfo.connected_projects.map((cp: any) => (
                      <Link
                        key={cp.id}
                        href={`/projects/${cp.id}`}
                        className="flex items-center justify-between gap-3 p-3 rounded-xl bg-background border border-border hover:border-blue-500/40 transition-colors text-xs"
                      >
                        <div className="min-w-0 space-y-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-bold text-foreground truncate">{cp.name}</span>
                            {cp.is_current_project && (
                              <span className="px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400 border border-blue-500/30 text-[10px] font-semibold">
                                Project Ini
                              </span>
                            )}
                            <span className="text-[11px] font-mono text-gray-400">{cp.framework}</span>
                          </div>
                          <div className="text-[11px] font-mono text-gray-400 truncate">
                            {cp.domain} &bull; Port {cp.port_mappings}
                            {cp.latest_deployment_version != null ? ` • v${cp.latest_deployment_version}` : ''}
                          </div>
                        </div>
                        <span
                          className={`shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold border ${
                            cp.container_running
                              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                              : cp.container_exists
                              ? 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                              : 'bg-gray-500/10 text-gray-400 border-border'
                          }`}
                        >
                          <span className={`w-1.5 h-1.5 rounded-full ${cp.container_running ? 'bg-emerald-400 animate-pulse' : 'bg-gray-400'}`} />
                          {cp.container_running ? 'Running' : cp.container_exists ? (cp.container_state || 'Stopped') : 'Not Deployed'}
                        </span>
                      </Link>
                    ))}
                  </div>
                ) : (
                  <div className="p-3.5 rounded-lg bg-background border border-border text-xs text-gray-400">
                    Belum ada project PSM lain yang terhubung ke database <code>{dbInfo?.db_name}</code>.
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab: Environment Variables */}
      {activeTab === 'environment' && (() => {
        const q = envSearch.trim().toLowerCase();
        const filteredEnvVars = q
          ? envVars.filter(
              (item) =>
                item.key.toLowerCase().includes(q) ||
                String(item.value ?? '').toLowerCase().includes(q)
            )
          : envVars;

        const envMap: Record<string, string> = {};
        for (const item of envVars) {
          if (item.value !== null && item.value !== undefined) {
            envMap[item.key] = String(item.value);
          }
        }
        const corruptedKeys = envVars
          .filter((item) => String(item.value ?? '').includes('****'))
          .map((item) => item.key);
        const isLaravelProject = /laravel|php/i.test(project?.framework || '');
        const diagnosticWarnings: string[] = [];
        if (corruptedKeys.length > 0) {
          diagnosticWarnings.push(
            `${corruptedKeys.length} variabel terdeteksi berisi nilai masked rusak ("****"): ${corruptedKeys.slice(0, 4).join(', ')}${corruptedKeys.length > 4 ? '...' : ''}.`
          );
        }
        if (isLaravelProject) {
          if (envMap.APP_ENV === 'production') {
            diagnosticWarnings.push(
              'APP_ENV bernilai "production" — pada akses HTTP IP:Port, Laravel dapat menghasilkan URL aset HTTPS sehingga CSS gagal dimuat.'
            );
          }
          if (!envMap.APP_KEY || String(envMap.APP_KEY).includes('****')) {
            diagnosticWarnings.push('APP_KEY belum terisi atau tidak valid untuk Laravel.');
          }
        }
        if (
          (currentDbType === 'mysql' || currentDbType === 'postgres') &&
          ['192.168.25.101', '127.0.0.1', 'localhost'].includes((envMap.DB_HOST || '').trim())
        ) {
          diagnosticWarnings.push(
            `DB_HOST masih mengarah ke "${envMap.DB_HOST}" — gunakan Setup Ulang / Sync DB untuk mengarahkan ke container Docker jaringan homelab.`
          );
        }

        return (
          <div className="space-y-6">
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
              {/* Header */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-4">
                <div>
                  <h3 className="font-bold text-sm text-foreground">Environment Variables (.env Editor &amp; Secrets)</h3>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Edit variabel secara satuan, mode teks <code>.env</code> lengkap, atau jalankan Setup Ulang jika terjadi kesalahan konfigurasi.
                  </p>
                </div>
                <div className="flex items-center gap-2 flex-wrap self-start">
                  <button
                    type="button"
                    onClick={handleOpenResetEnvPanel}
                    className={`px-3 py-1.5 rounded-lg border text-xs font-medium flex items-center gap-1.5 whitespace-nowrap transition-colors ${
                      showResetEnvPanel
                        ? 'border-amber-500 bg-amber-500/20 text-amber-300'
                        : 'border-amber-500/30 bg-amber-500/10 hover:bg-amber-500/20 text-amber-400'
                    }`}
                  >
                    <RotateCcw className="w-3.5 h-3.5" />
                    <span>Setup Ulang Env</span>
                  </button>
                  <button
                    onClick={() => handleProvisionDatabase()}
                    disabled={provisioningDb}
                    className="px-3 py-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-500 text-xs font-medium flex items-center gap-1.5 whitespace-nowrap"
                  >
                    <Database className="w-3.5 h-3.5" />
                    <span>Sync {dbTypeLabel} Env Vars</span>
                  </button>
                </div>
              </div>

              {/* Auto-Diagnostics Banner */}
              {diagnosticWarnings.length > 0 && (
                <div className="p-3.5 rounded-xl border border-amber-500/30 bg-amber-500/10 text-xs space-y-2">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div className="flex items-center gap-2 font-bold text-amber-300">
                      <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
                      <span>Terdeteksi Potensi Kesalahan Konfigurasi Environment ({diagnosticWarnings.length})</span>
                    </div>
                    {!showResetEnvPanel && (
                      <button
                        type="button"
                        onClick={handleOpenResetEnvPanel}
                        className="px-3 py-1 rounded-lg bg-amber-500 hover:bg-amber-400 text-slate-950 font-semibold text-[11px] flex items-center gap-1.5 self-start sm:self-auto"
                      >
                        <RotateCcw className="w-3 h-3" />
                        <span>Buka Setup Ulang Env</span>
                      </button>
                    )}
                  </div>
                  <ul className="list-disc list-inside space-y-1 text-amber-200/90 text-[11px]">
                    {diagnosticWarnings.map((w, idx) => (
                      <li key={idx}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Wizard Setup Ulang Environment */}
              {showResetEnvPanel && (
                <div className="p-4 rounded-xl border border-amber-500/30 bg-background/80 space-y-4 text-xs">
                  <div className="flex items-center justify-between border-b border-border pb-2.5">
                    <div className="flex items-center gap-2 font-bold text-foreground">
                      <RotateCcw className="w-4 h-4 text-amber-400" />
                      <span>Wizard Setup Ulang Environment ({project?.framework})</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setShowResetEnvPanel(false)}
                      className="text-gray-400 hover:text-foreground"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>

                  {/* Mode Selector */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <button
                      type="button"
                      onClick={() => setResetMode('repair')}
                      className={`p-3 rounded-xl border text-left space-y-1 transition-all ${
                        resetMode === 'repair'
                          ? 'border-blue-500 bg-blue-500/10 ring-1 ring-blue-500/40'
                          : 'border-border bg-card hover:border-gray-600'
                      }`}
                    >
                      <div className="font-bold text-foreground flex items-center justify-between">
                        <span>Perbaiki &amp; Lengkapi (Aman)</span>
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-500/15 text-blue-400 font-semibold">Disarankan</span>
                      </div>
                      <p className="text-[11px] text-gray-400 leading-relaxed">
                        Menghapus nilai rusak (<code>****</code>), melengkapi default framework &amp; <code>.env.example</code> yang hilang, tetap menjaga variabel custom Anda yang masih valid.
                      </p>
                    </button>

                    <button
                      type="button"
                      onClick={() => setResetMode('clean')}
                      className={`p-3 rounded-xl border text-left space-y-1 transition-all ${
                        resetMode === 'clean'
                          ? 'border-rose-500 bg-rose-500/10 ring-1 ring-rose-500/40'
                          : 'border-border bg-card hover:border-gray-600'
                      }`}
                    >
                      <div className="font-bold text-foreground flex items-center justify-between">
                        <span>Reset Bersih &amp; Setup Ulang Total</span>
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-rose-500/15 text-rose-400 font-semibold">Fresh Reset</span>
                      </div>
                      <p className="text-[11px] text-gray-400 leading-relaxed">
                        Mengosongkan environment lama lalu membangun ulang murni dari <code>.env.example</code>, default framework, dan kredensial database terbaru.
                      </p>
                    </button>
                  </div>

                  {/* Parameters */}
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div className="space-y-1">
                      <label className="block text-[11px] text-gray-400 font-medium">
                        {isLaravelProject ? 'APP_ENV (Mode Aplikasi)' : 'Environment Mode'}
                      </label>
                      <select
                        value={resetAppEnv}
                        onChange={(e) => setResetAppEnv(e.target.value)}
                        className="w-full px-3 py-1.5 rounded-lg bg-card border border-border text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
                      >
                        <option value="local">local (HTTP + Debug On)</option>
                        <option value="production">production</option>
                      </select>
                    </div>

                    <div className="md:col-span-2 space-y-1">
                      <div className="flex items-center justify-between gap-2 flex-wrap">
                        <label className="block text-[11px] text-gray-400 font-medium">APP_URL / Base URL</label>
                        <div className="flex items-center gap-1.5 text-[10px] font-mono">
                          {access.tailscaleUrl && (
                            <button
                              type="button"
                              onClick={() => setResetAppUrl(access.tailscaleUrl!)}
                              className="px-2 py-0.5 rounded border border-cyan-500/30 bg-cyan-500/10 text-cyan-300 hover:bg-cyan-500/20"
                            >
                              Tailscale
                            </button>
                          )}
                          {access.lanUrl && (
                            <button
                              type="button"
                              onClick={() => setResetAppUrl(access.lanUrl!)}
                              className="px-2 py-0.5 rounded border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20"
                            >
                              LAN IP
                            </button>
                          )}
                          {access.domainUrl && (
                            <button
                              type="button"
                              onClick={() => setResetAppUrl(access.domainUrl)}
                              className="px-2 py-0.5 rounded border border-indigo-500/30 bg-indigo-500/10 text-indigo-300 hover:bg-indigo-500/20"
                            >
                              Domain
                            </button>
                          )}
                        </div>
                      </div>
                      <input
                        type="text"
                        value={resetAppUrl}
                        onChange={(e) => setResetAppUrl(e.target.value)}
                        placeholder="http://100.78.113.53:3101"
                        className="w-full px-3 py-1.5 rounded-lg bg-card border border-border text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-5 pt-1">
                    {currentDbType !== 'none' && (
                      <label className="inline-flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={resetIncludeDbVars}
                          onChange={(e) => setResetIncludeDbVars(e.target.checked)}
                          className="rounded border-border bg-card text-emerald-500"
                        />
                        <span>Sinkronkan ulang kredensial Database ({dbTypeLabel})</span>
                      </label>
                    )}

                    {isLaravelProject && (
                      <label className="inline-flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={resetRegenerateAppKey}
                          onChange={(e) => setResetRegenerateAppKey(e.target.checked)}
                          className="rounded border-border bg-card text-amber-500"
                        />
                        <span>Generate ulang <code>APP_KEY</code> baru</span>
                      </label>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center justify-end gap-2 pt-2 border-t border-border">
                    <button
                      type="button"
                      onClick={() => setShowResetEnvPanel(false)}
                      className="px-3 py-1.5 rounded-lg border border-border hover:bg-card text-gray-300"
                    >
                      Batal
                    </button>
                    <button
                      type="button"
                      disabled={resettingEnv}
                      onClick={() => handleResetEnvironment(false)}
                      className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <RotateCcw className={`w-3.5 h-3.5 ${resettingEnv ? 'animate-spin' : ''}`} />
                      <span>{resettingEnv ? 'Memproses...' : 'Terapkan Setup Ulang'}</span>
                    </button>
                    <button
                      type="button"
                      disabled={resettingEnv || deploying}
                      onClick={() => handleResetEnvironment(true)}
                      className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <Rocket className="w-3.5 h-3.5" />
                      <span>{deploying ? 'Deploying...' : 'Setup Ulang & Deploy Ulang'}</span>
                    </button>
                  </div>
                </div>
              )}

              {/* Notice & Redeploy Prompt */}
              {envNotice && (
                <div
                  className={`p-3 rounded-xl border text-xs flex flex-col sm:flex-row sm:items-center justify-between gap-3 ${
                    envNotice.type === 'success'
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                      : 'bg-rose-500/10 border-rose-500/30 text-rose-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    {envNotice.type === 'success' ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                    ) : (
                      <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
                    )}
                    <span>{envNotice.text}</span>
                  </div>
                  {envNotice.needsRedeploy && (
                    <button
                      onClick={handleTriggerDeploy}
                      disabled={deploying}
                      className="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold flex items-center gap-1.5 whitespace-nowrap self-start sm:self-auto"
                    >
                      <Rocket className="w-3.5 h-3.5" />
                      <span>{deploying ? 'Deploying...' : 'Deploy Ulang Sekarang'}</span>
                    </button>
                  )}
                </div>
              )}

              {/* Toolbar */}
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
                <div className="flex items-center gap-2 flex-1">
                  {envEditorMode === 'list' && (
                    <div className="relative flex-1 max-w-sm">
                      <Search className="w-3.5 h-3.5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                      <input
                        type="text"
                        placeholder="Cari key atau nilai variable..."
                        value={envSearch}
                        onChange={(e) => setEnvSearch(e.target.value)}
                        className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-background border border-border text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-2 flex-wrap text-xs">
                  <input
                    ref={envFileInputRef}
                    type="file"
                    accept=".env,text/plain,*"
                    className="hidden"
                    onChange={handleImportEnvFile}
                  />
                  <button
                    type="button"
                    onClick={() => envFileInputRef.current?.click()}
                    className="px-3 py-1.5 rounded-lg border border-border hover:bg-background text-foreground flex items-center gap-1.5 transition-colors"
                    title="Muat isi file .env dari komputer ke editor"
                  >
                    <Upload className="w-3.5 h-3.5 text-blue-400" />
                    <span>Import .env</span>
                  </button>
                  <button
                    type="button"
                    onClick={handleDownloadEnvFile}
                    disabled={envVars.length === 0}
                    className="px-3 py-1.5 rounded-lg border border-border hover:bg-background text-foreground flex items-center gap-1.5 transition-colors disabled:opacity-50"
                    title="Unduh semua variabel sebagai file .env"
                  >
                    <Download className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Unduh .env</span>
                  </button>

                  {envEditorMode === 'list' && (
                    <button
                      type="button"
                      onClick={() => setRevealAllEnv((v) => !v)}
                      className="px-3 py-1.5 rounded-lg border border-border hover:bg-background text-foreground flex items-center gap-1.5 transition-colors"
                    >
                      {revealAllEnv ? <EyeOff className="w-3.5 h-3.5 text-amber-400" /> : <Eye className="w-3.5 h-3.5 text-blue-400" />}
                      <span>{revealAllEnv ? 'Sembunyikan Semua' : 'Tampilkan Semua Nilai'}</span>
                    </button>
                  )}

                  <div className="inline-flex rounded-lg border border-border bg-background p-0.5">
                    <button
                      type="button"
                      onClick={() => setEnvEditorMode('list')}
                      className={`px-3 py-1 rounded-md flex items-center gap-1.5 font-medium transition-colors ${
                        envEditorMode === 'list' ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-foreground'
                      }`}
                    >
                      <List className="w-3.5 h-3.5" />
                      <span>Mode Daftar ({envVars.length})</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setBulkEnvText(envVars.map((v: any) => `${v.key}=${v.value ?? ''}`).join('\n'));
                        setEnvEditorMode('bulk');
                      }}
                      className={`px-3 py-1 rounded-md flex items-center gap-1.5 font-medium transition-colors ${
                        envEditorMode === 'bulk' ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-foreground'
                      }`}
                    >
                      <FileText className="w-3.5 h-3.5" />
                      <span>Editor .env (Bulk)</span>
                    </button>
                  </div>
                </div>
              </div>

              {envEditorMode === 'list' ? (
                <>
                  {/* Key-Value List */}
                  <div className="space-y-2">
                    {filteredEnvVars.length === 0 ? (
                      <div className="p-6 text-center rounded-lg border border-dashed border-border text-xs text-gray-500">
                        {envVars.length === 0
                          ? 'Belum ada environment variable. Tambahkan di bawah atau gunakan Sync Database.'
                          : 'Tidak ada variable yang cocok dengan pencarian Anda.'}
                      </div>
                    ) : (
                      filteredEnvVars.map((item) => {
                        const isEditing = editingEnvOriginalKey === item.key;
                        const visible = isEnvValueVisible(item.key);
                        const hasPlainValue = item.value !== null && item.value !== undefined;
                        const displayVal = visible
                          ? hasPlainValue
                            ? item.value === ''
                              ? '(kosong)'
                              : item.value
                            : item.masked_value
                          : item.masked_value || '••••••••';

                        if (isEditing) {
                          return (
                            <form
                              key={item.key}
                              onSubmit={handleSaveEditEnv}
                              className="p-3 rounded-lg border border-blue-500/40 bg-blue-500/5 flex flex-col sm:flex-row items-stretch sm:items-center gap-2 text-xs font-mono"
                            >
                              <input
                                type="text"
                                value={editingEnvKey}
                                onChange={(e) => setEditingEnvKey(e.target.value)}
                                placeholder="KEY"
                                className="sm:w-1/3 px-3 py-1.5 rounded-lg bg-background border border-border text-foreground font-bold focus:outline-none focus:ring-2 focus:ring-blue-500"
                              />
                              <div className="relative flex-1 flex items-center">
                                <input
                                  type={showEditEnvValue ? 'text' : 'password'}
                                  value={editingEnvValue}
                                  onChange={(e) => setEditingEnvValue(e.target.value)}
                                  placeholder="VALUE"
                                  className="w-full pl-3 pr-9 py-1.5 rounded-lg bg-background border border-border text-foreground focus:outline-none focus:ring-2 focus:ring-blue-500"
                                />
                                <button
                                  type="button"
                                  onClick={() => setShowEditEnvValue((s) => !s)}
                                  className="absolute right-2.5 text-gray-400 hover:text-foreground"
                                  title={showEditEnvValue ? 'Sembunyikan' : 'Tampilkan'}
                                >
                                  {showEditEnvValue ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                                </button>
                              </div>
                              <div className="flex items-center gap-1.5 font-sans">
                                <button
                                  type="submit"
                                  disabled={savingEnv}
                                  className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold flex items-center gap-1 disabled:opacity-50"
                                >
                                  <Save className="w-3.5 h-3.5" />
                                  <span>Simpan</span>
                                </button>
                                <button
                                  type="button"
                                  onClick={handleCancelEditEnv}
                                  className="px-2.5 py-1.5 rounded-lg border border-border hover:bg-background text-gray-300 flex items-center gap-1"
                                >
                                  <X className="w-3.5 h-3.5" />
                                  <span>Batal</span>
                                </button>
                              </div>
                            </form>
                          );
                        }

                        return (
                          <div
                            key={item.key}
                            className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 p-3 rounded-lg border border-border bg-background text-xs font-mono hover:border-gray-600 transition-colors"
                          >
                            <div className="flex flex-col sm:flex-row sm:items-center gap-2 min-w-0 flex-1">
                              <span className="font-bold text-foreground sm:w-56 shrink-0 truncate" title={item.key}>
                                {item.key}
                              </span>
                              <span
                                className={`truncate flex-1 ${
                                  visible && item.value === '' ? 'italic text-gray-500' : 'text-gray-300'
                                }`}
                                title={visible && hasPlainValue ? String(item.value) : undefined}
                              >
                                {displayVal}
                              </span>
                            </div>

                            <div className="flex items-center gap-1 shrink-0 font-sans">
                              <button
                                type="button"
                                onClick={() => toggleRevealEnvKey(item.key)}
                                className="p-1.5 rounded-md border border-border hover:bg-card text-gray-400 hover:text-foreground transition-colors"
                                title={visible ? 'Sembunyikan nilai' : 'Tampilkan nilai'}
                              >
                                {visible ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                              </button>
                              <button
                                type="button"
                                onClick={() => handleCopyEnvValue(item.key, String(item.value ?? item.masked_value ?? ''))}
                                className="p-1.5 rounded-md border border-border hover:bg-card text-gray-400 hover:text-foreground transition-colors"
                                title="Salin nilai"
                              >
                                {copiedEnvKey === item.key ? (
                                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                                ) : (
                                  <Copy className="w-3.5 h-3.5" />
                                )}
                              </button>
                              <button
                                type="button"
                                onClick={() => handleStartEditEnv(item)}
                                className="px-2 py-1.5 rounded-md border border-border hover:border-blue-500/40 hover:bg-blue-500/10 text-blue-400 flex items-center gap-1 transition-colors"
                                title="Edit key / value"
                              >
                                <Pencil className="w-3 h-3" />
                                <span>Edit</span>
                              </button>
                              <button
                                type="button"
                                onClick={() => handleDeleteEnvVar(item.key)}
                                disabled={savingEnv}
                                className="p-1.5 rounded-md border border-border hover:border-rose-500/40 hover:bg-rose-500/10 text-rose-400 transition-colors disabled:opacity-50"
                                title="Hapus variable"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>

                  {/* Add new variable */}
                  <form onSubmit={handleAddEnvVar} className="pt-4 border-t border-border flex flex-col sm:flex-row gap-3 text-xs">
                    <input
                      type="text"
                      placeholder="VARIABLE_KEY (contoh: APP_ENV)"
                      value={newKey}
                      onChange={(e) => setNewKey(e.target.value)}
                      className="sm:w-1/3 px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <div className="relative flex-1 flex items-center">
                      <input
                        type={showNewEnvValue ? 'text' : 'password'}
                        placeholder="VALUE (contoh: local)"
                        value={newValue}
                        onChange={(e) => setNewValue(e.target.value)}
                        className="w-full pl-3 pr-9 py-2 rounded-lg bg-background border border-border text-foreground font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                      <button
                        type="button"
                        onClick={() => setShowNewEnvValue((s) => !s)}
                        className="absolute right-2.5 text-gray-400 hover:text-foreground"
                        title={showNewEnvValue ? 'Sembunyikan' : 'Tampilkan'}
                      >
                        {showNewEnvValue ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                    <button
                      type="submit"
                      disabled={savingEnv || !newKey.trim()}
                      className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium flex items-center justify-center gap-1.5 disabled:opacity-50 whitespace-nowrap"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      <span>Tambah Variable</span>
                    </button>
                  </form>
                </>
              ) : (
                /* Bulk .env Text Editor */
                <div className="space-y-3">
                  <div className="rounded-lg border border-border bg-[#0d1117] overflow-hidden">
                    <textarea
                      value={bulkEnvText}
                      onChange={(e) => setBulkEnvText(e.target.value)}
                      rows={16}
                      spellCheck={false}
                      placeholder="# Paste atau edit file .env di sini (KEY=VALUE)&#10;APP_ENV=local&#10;APP_URL=http://100.78.113.53:3101"
                      className="w-full p-4 bg-transparent text-gray-200 font-mono text-xs focus:outline-none resize-y leading-relaxed"
                    />
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
                    <span className="text-gray-400">
                      Mendukung baris komentar <code>#</code>, baris kosong, dan tanda petik di nilai.
                    </span>
                    <div className="flex items-center gap-2 flex-wrap">
                      <button
                        type="button"
                        onClick={() => setBulkEnvText(envVars.map((v: any) => `${v.key}=${v.value ?? ''}`).join('\n'))}
                        className="px-3 py-2 rounded-lg border border-border hover:bg-background text-gray-300 font-medium"
                      >
                        Reset
                      </button>
                      <button
                        type="button"
                        disabled={savingEnv}
                        onClick={() => handleSaveBulkEnv(false)}
                        className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold flex items-center gap-1.5 disabled:opacity-50"
                      >
                        <Save className="w-3.5 h-3.5" />
                        <span>{savingEnv ? 'Menyimpan...' : 'Simpan Semua (.env)'}</span>
                      </button>
                      <button
                        type="button"
                        disabled={savingEnv || deploying}
                        onClick={() => handleSaveBulkEnv(true)}
                        className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold flex items-center gap-1.5 disabled:opacity-50"
                      >
                        <Rocket className="w-3.5 h-3.5" />
                        <span>{deploying ? 'Deploying...' : 'Simpan & Deploy Ulang'}</span>
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })()}

      {/* Tab: Overview / Source */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          {/* Docker Network & Port Mappings Card */}
          <div className="rounded-xl border border-blue-500/20 bg-card p-5 space-y-4 text-xs font-mono">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-sans font-bold text-sm text-foreground">
                <Globe className="w-4 h-4 text-blue-500" />
                <span>Docker Port Mapping & Network</span>
              </div>
              {hostPortOnly && (
                <a
                  href={`http://${serverHost}:${hostPortOnly}`}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-sans font-medium transition-colors"
                >
                  <span>Buka di Browser</span>
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="p-3 rounded-lg bg-background border border-border">
                <span className="text-gray-400 block mb-1">Mapping Konfigurasi</span>
                <span className="text-blue-500 font-bold text-sm">{project?.port_mappings || '—'}</span>
              </div>
              <div className="p-3 rounded-lg bg-background border border-border">
                <span className="text-gray-400 block mb-1">Server Host URL</span>
                <span className="text-foreground">
                  http://{serverHost}:{hostPortOnly}
                </span>
              </div>
              <div className="p-3 rounded-lg bg-background border border-border">
                <span className="text-gray-400 block mb-1">Network Docker</span>
                <span className="text-emerald-500">psm-network (bridge)</span>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-border bg-card p-5 space-y-4 text-xs font-mono">
            <h3 className="font-bold text-sm font-sans text-foreground">Source & Build Configuration</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="p-3 rounded-lg bg-background border border-border">
                <span className="text-gray-400 block mb-1">Source Type</span>
                <span className="text-foreground capitalize">{project?.source_type}</span>
              </div>
              <div className="p-3 rounded-lg bg-background border border-border">
                <span className="text-gray-400 block mb-1">Repository URL</span>
                <span className="text-foreground">{project?.repository_url || 'N/A (ZIP Upload)'}</span>
              </div>
              <div className="p-3 rounded-lg bg-background border border-border">
                <span className="text-gray-400 block mb-1">Branch</span>
                <span className="text-foreground">{project?.branch || 'main'}</span>
              </div>
              <div className="p-3 rounded-lg bg-background border border-border">
                <span className="text-gray-400 block mb-1">Dockerfile Path</span>
                <span className="text-foreground">{project?.dockerfile_path || 'Dockerfile'}</span>
              </div>
            </div>
          </div>

          {/* GitHub Auto-Deploy Webhook Card */}
          <div className="rounded-xl border border-border bg-card p-5 space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <div className="p-2 rounded-lg bg-purple-500/10 text-purple-500">
                  <GitBranch className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="font-bold text-sm text-foreground">GitHub Webhook (Continuous Deployment to Docker)</h3>
                  <p className="text-xs text-gray-500">
                    Automatically build and deploy directly to Docker every time you push code to GitHub.
                  </p>
                </div>
              </div>

              {project?.source?.repository_url && project.source.repository_url.includes('github.com') && (
                <button
                  onClick={handleAutoInstallWebhook}
                  disabled={autoInstallingWebhook}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-xs font-medium transition-colors shadow-sm disabled:opacity-50 self-start"
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  <span>{autoInstallingWebhook ? 'Memasang...' : '⚡ Pasang Webhook Otomatis ke GitHub'}</span>
                </button>
              )}
            </div>

            {webhookInstallMsg && (
              <div
                className={`p-3 rounded-lg text-xs flex items-center gap-2 animate-in fade-in duration-200 ${
                  webhookInstallMsg.type === 'success'
                    ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                    : 'bg-rose-500/10 border border-rose-500/20 text-rose-400'
                }`}
              >
                {webhookInstallMsg.type === 'success' ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                ) : (
                  <AlertCircle className="w-4 h-4 text-rose-500 shrink-0" />
                )}
                <span>{webhookInstallMsg.text}</span>
              </div>
            )}

            <div className="space-y-2 text-xs">
              <label className="block text-gray-400 font-medium">Payload URL to configure in GitHub:</label>
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  readOnly
                  value={typeof window !== 'undefined' ? `${window.location.origin}/api/v1/deployments/webhook/${projectId}` : `/api/v1/deployments/webhook/${projectId}`}
                  className="flex-1 px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono select-all text-xs"
                />
                <button
                  onClick={() => {
                    if (typeof window !== 'undefined') {
                      navigator.clipboard.writeText(`${window.location.origin}/api/v1/deployments/webhook/${projectId}`);
                      setCopiedWebhook(true);
                      setTimeout(() => setCopiedWebhook(false), 2000);
                    }
                  }}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border hover:bg-card text-foreground transition-all"
                >
                  {copiedWebhook ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                  <span>{copiedWebhook ? 'Copied' : 'Copy'}</span>
                </button>
              </div>
            </div>

            <div className="p-3.5 rounded-lg bg-background border border-border text-[11px] text-gray-500 space-y-1.5">
              <div className="font-semibold text-foreground">Cara memasang Webhook di GitHub:</div>
              <ol className="list-decimal list-inside space-y-1 text-gray-400">
                <li>Buka repositori GitHub Anda &gt; <strong>Settings</strong> &gt; <strong>Webhooks</strong> &gt; <strong>Add webhook</strong>.</li>
                <li>Tempel (Paste) URL di atas ke dalam kolom <strong>Payload URL</strong>.</li>
                <li>Pilih Content type: <code>application/json</code>.</li>
                <li>Pilih <em>&quot;Just the push event&quot;</em> lalu klik <strong>Add webhook</strong>.</li>
                <li>Selesai! Setiap Anda <code>git push</code>, PSM otomatis meng-clone, mem-build, membuat/menghubungkan database {dbTypeLabel}, dan me-restart container Docker aplikasi Anda.</li>
              </ol>
            </div>
          </div>
        </div>
      )}

      {/* Tab: Settings */}
      {activeTab === 'settings' && (
        <div className="space-y-6">
          {projectUpdateSuccessMsg && (
            <div className="p-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-medium flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
              <span>{projectUpdateSuccessMsg}</span>
            </div>
          )}

          {/* Card 0: Mode Maintenance & Halaman Pemeliharaan */}
          <div className={`rounded-xl border bg-card p-5 space-y-4 text-xs ${maintenanceMode ? 'border-amber-500/40' : 'border-border'}`}>
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-4">
              <div className="flex items-start gap-3">
                <div className={`p-2 rounded-lg border ${maintenanceMode ? 'bg-amber-500/15 text-amber-400 border-amber-500/30' : 'bg-blue-500/10 text-blue-400 border-blue-500/20'}`}>
                  <Wrench className="w-4 h-4" />
                </div>
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="font-bold text-sm text-foreground">Mode Maintenance &amp; Halaman Pemeliharaan</h3>
                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase border ${
                        maintenanceMode
                          ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                          : 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
                      }`}
                    >
                      {maintenanceMode ? 'AKTIF (503)' : 'NONAKTIF (ONLINE)'}
                    </span>
                  </div>
                  <p className="text-gray-400 mt-0.5">
                    Tampilkan halaman informasi pemeliharaan (HTTP 503) kepada pengunjung tanpa mematikan container project.
                  </p>
                </div>
              </div>

              <button
                type="button"
                disabled={savingMaintenance}
                onClick={() => handleToggleMaintenance(!maintenanceMode)}
                className={`relative inline-flex items-center gap-2 px-3.5 py-2 rounded-xl font-semibold text-xs border transition-all self-start disabled:opacity-50 ${
                  maintenanceMode
                    ? 'bg-amber-500/15 border-amber-500/40 text-amber-300 hover:bg-amber-500/25'
                    : 'bg-background border-border text-gray-300 hover:border-amber-500/40 hover:text-amber-300'
                }`}
              >
                <span className={`w-2.5 h-2.5 rounded-full ${maintenanceMode ? 'bg-amber-400 animate-pulse' : 'bg-emerald-500'}`} />
                <span>{maintenanceMode ? 'Status: ON (Klik untuk Matikan)' : 'Status: OFF (Klik untuk Aktifkan)'}</span>
              </button>
            </div>

            {maintenanceNotice && (
              <div
                className={`p-3 rounded-lg border text-xs flex items-center gap-2 ${
                  maintenanceNotice.type === 'success'
                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                    : 'bg-rose-500/10 border-rose-500/30 text-rose-300'
                }`}
              >
                {maintenanceNotice.type === 'success' ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                ) : (
                  <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
                )}
                <span>{maintenanceNotice.text}</span>
              </div>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-gray-400 font-medium mb-1">Judul Halaman Maintenance:</label>
                <input
                  type="text"
                  value={maintenanceTitle}
                  onChange={(e) => setMaintenanceTitle(e.target.value)}
                  placeholder="Sedang Dalam Pemeliharaan"
                  className="w-full px-3 py-2 rounded-lg bg-background border border-border text-foreground text-xs focus:outline-none focus:ring-2 focus:ring-amber-500"
                />
              </div>
              <div>
                <label className="block text-gray-400 font-medium mb-1">Estimasi Selesai (Opsional):</label>
                <input
                  type="text"
                  value={maintenanceEta}
                  onChange={(e) => setMaintenanceEta(e.target.value)}
                  placeholder="contoh: Hari ini, 14:00 WIB atau ± 30 menit"
                  className="w-full px-3 py-2 rounded-lg bg-background border border-border text-foreground text-xs focus:outline-none focus:ring-2 focus:ring-amber-500"
                />
              </div>
              <div className="sm:col-span-2">
                <label className="block text-gray-400 font-medium mb-1">Pesan Informasi Maintenance:</label>
                <textarea
                  rows={3}
                  value={maintenanceMessage}
                  onChange={(e) => setMaintenanceMessage(e.target.value)}
                  placeholder="Kami sedang melakukan pembaruan dan peningkatan sistem untuk memberikan layanan terbaik. Silakan coba kembali beberapa saat lagi."
                  className="w-full px-3 py-2 rounded-lg bg-background border border-border text-foreground text-xs focus:outline-none focus:ring-2 focus:ring-amber-500 resize-y"
                />
              </div>
            </div>

            <div className="p-3 rounded-lg bg-background border border-border text-[11px] text-gray-400 space-y-1">
              <div>
                <strong className="text-foreground">Cakupan Maintenance Otomatis:</strong>
              </div>
              <div>
                &bull; <strong>Domain Ingress (<code>http://{access.domain}</code>)</strong>: Aktif otomatis untuk semua framework lewat Nginx Router (:80) tanpa me-restart container.
              </div>
              <div>
                &bull; <strong>IP:Port Langsung (misal <code>{access.tailscaleIp || access.lanIp}:{access.hostPort || '3101'}</code>)</strong>: Aktif otomatis untuk container <strong>Laravel</strong> lewat <code>storage/framework/down</code>.
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <div className="flex items-center gap-2 flex-wrap">
                <a
                  href={buildMaintenancePreviewUrl()}
                  target="_blank"
                  rel="noreferrer"
                  className="px-3 py-2 rounded-lg border border-border hover:bg-background text-foreground font-medium flex items-center gap-1.5"
                >
                  <Eye className="w-3.5 h-3.5 text-blue-400" />
                  <span>Preview Halaman</span>
                  <ExternalLink className="w-3 h-3 opacity-70" />
                </a>
                <button
                  type="button"
                  onClick={() => setShowMaintenancePreview((v) => !v)}
                  className="px-3 py-2 rounded-lg border border-border hover:bg-background text-gray-300 font-medium"
                >
                  {showMaintenancePreview ? 'Tutup Live Preview' : 'Tampilkan Live Preview'}
                </button>
              </div>

              <div className="flex items-center gap-2 flex-wrap">
                {maintenanceMode ? (
                  <>
                    <button
                      type="button"
                      disabled={savingMaintenance}
                      onClick={() => handleToggleMaintenance(true)}
                      className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-semibold flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <Save className="w-3.5 h-3.5" />
                      <span>{savingMaintenance ? 'Menyimpan...' : 'Simpan Pengaturan Maintenance'}</span>
                    </button>
                    <button
                      type="button"
                      disabled={savingMaintenance}
                      onClick={() => handleToggleMaintenance(false)}
                      className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      <span>Nonaktifkan &amp; Online-kan</span>
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    disabled={savingMaintenance}
                    onClick={() => handleToggleMaintenance(true)}
                    className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-semibold flex items-center gap-1.5 disabled:opacity-50"
                  >
                    <Wrench className="w-3.5 h-3.5" />
                    <span>{savingMaintenance ? 'Mengaktifkan...' : 'Aktifkan Mode Maintenance'}</span>
                  </button>
                )}
              </div>
            </div>

            {showMaintenancePreview && (
              <div className="rounded-xl border border-border overflow-hidden bg-[#090d16]">
                <div className="px-3 py-2 border-b border-border flex items-center justify-between text-[11px] font-mono text-gray-400">
                  <span>Live Preview Halaman Maintenance ({access.domain})</span>
                  <span>HTTP 503 Preview</span>
                </div>
                <iframe
                  title="Preview Halaman Maintenance"
                  src={buildMaintenancePreviewUrl()}
                  className="w-full h-[420px] border-0"
                />
              </div>
            )}
          </div>

          {/* Card 1: Identitas Project & Local Domain */}
          <div className="rounded-xl border border-border bg-card p-5 space-y-4 text-xs">
            <div>
              <h3 className="font-bold text-sm text-foreground flex items-center gap-2">
                <Settings className="w-4 h-4 text-blue-500" />
                <span>Identitas Project & Domain Ingress</span>
              </h3>
              <p className="text-gray-500 mt-0.5">
                Konfigurasi nama, deskripsi, framework aplikasi, dan routing domain lokal virtual host.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-gray-400 font-medium mb-1">Nama Project:</label>
                <input
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder="Nama Project"
                  className="w-full px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-gray-400 font-medium mb-1">Framework Preset:</label>
                <select
                  value={projectFramework}
                  onChange={(e) => setProjectFramework(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-background border border-border text-foreground text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="Next.js">Next.js (React / SSR / App Router)</option>
                  <option value="Vite / React">Vite / React SPA</option>
                  <option value="Vue.js">Vue.js / Nuxt</option>
                  <option value="Node.js">Node.js (Express / Fastify / Nest)</option>
                  <option value="FastAPI">Python / FastAPI</option>
                  <option value="Django">Python / Django</option>
                  <option value="Flask">Python / Flask</option>
                  <option value="Laravel">Laravel / PHP (Nginx + PHP-FPM)</option>
                  <option value="Go">Go (Golang Alpine)</option>
                  <option value="Static HTML">Static HTML / Web</option>
                  <option value="Dockerfile">Custom / Multi-stage Dockerfile</option>
                </select>
              </div>

              <div>
                <label className="block text-gray-400 font-medium mb-1">Deskripsi:</label>
                <input
                  type="text"
                  value={projectDescription}
                  onChange={(e) => setProjectDescription(e.target.value)}
                  placeholder="Deskripsi singkat project"
                  className="w-full px-3 py-2 rounded-lg bg-background border border-border text-foreground text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-gray-400 font-medium mb-1">
                  Local Domain Virtual Host (Ingress Port 80):
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={projectDomain}
                    onChange={(e) => setProjectDomain(e.target.value)}
                    placeholder="app.homelab"
                    className="flex-1 px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  {projectDomain && (
                    <a
                      href={`http://${projectDomain}`}
                      target="_blank"
                      rel="noreferrer"
                      className="px-3 py-2 rounded-lg border border-border hover:bg-card text-gray-400 hover:text-foreground flex items-center gap-1"
                      title="Buka Domain"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                    </a>
                  )}
                </div>
                <p className="text-[10px] text-gray-400 mt-1">
                  Otomatis terdaftar di Nginx Ingress Router &amp; CoreDNS (:53) homelab server.
                </p>
              </div>
            </div>
          </div>

          {/* Card 2: Sumber Kode & Git Configuration */}
          <div className="rounded-xl border border-border bg-card p-5 space-y-4 text-xs">
            <div>
              <h3 className="font-bold text-sm text-foreground flex items-center gap-2">
                <FolderGit2 className="w-4 h-4 text-purple-500" />
                <span>Sumber Kode &amp; Build Target</span>
              </h3>
              <p className="text-gray-500 mt-0.5">
                Konfigurasi repositori Git atau upload arsip kode untuk deployment ke server.
              </p>
            </div>

            {project?.source_type === 'git' ? (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="sm:col-span-2">
                  <label className="block text-gray-400 font-medium mb-1">Git Repository URL:</label>
                  <input
                    type="text"
                    value={projectRepoUrl}
                    onChange={(e) => {
                      let val = e.target.value;
                      if (val.includes('/tree/')) {
                        const parts = val.split('/tree/');
                        val = parts[0].replace(/\/+$/, '');
                        const ext = parts[1]?.replace(/^\/+|\/+$/g, '').split('/')[0];
                        if (ext) setProjectBranch(ext);
                      }
                      setProjectRepoUrl(val);
                    }}
                    placeholder="https://github.com/user/repository.git"
                    className="w-full px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-purple-500"
                  />
                </div>

                <div>
                  <label className="block text-gray-400 font-medium mb-1">Target Branch:</label>
                  <input
                    type="text"
                    value={projectBranch}
                    onChange={(e) => setProjectBranch(e.target.value)}
                    placeholder="main"
                    className="w-full px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-purple-500"
                  />
                </div>

                <div className="sm:col-span-3">
                  <label className="block text-gray-400 font-medium mb-1">Dockerfile Path:</label>
                  <input
                    type="text"
                    value={projectDockerfilePath}
                    onChange={(e) => setProjectDockerfilePath(e.target.value)}
                    placeholder="Dockerfile"
                    className="w-full sm:w-1/2 px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-purple-500"
                  />
                </div>
              </div>
            ) : (
              <div className="p-4 rounded-lg bg-background border border-border space-y-3">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <div>
                    <span className="font-semibold text-foreground block">Tipe Sumber: ZIP Archive Upload</span>
                    <span className="text-[11px] text-gray-500">
                      Project ini dideploy dari file arsip ZIP. Anda dapat mengunggah arsip kode baru kapan saja untuk memperbarui container di server.
                    </span>
                  </div>
                  <div>
                    <input
                      type="file"
                      ref={fileInputRef}
                      onChange={handleZipUpload}
                      accept=".zip"
                      className="hidden"
                    />
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      disabled={deploying}
                      className="px-3.5 py-2 rounded-lg bg-purple-600 hover:bg-purple-500 text-white font-medium flex items-center gap-1.5 transition-colors"
                    >
                      <Upload className="w-3.5 h-3.5" />
                      <span>{deploying ? 'Mengunggah...' : 'Upload File ZIP Baru'}</span>
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Card 3: Database Engine & Auto-Wire */}
          <div className="rounded-xl border border-border bg-card p-5 space-y-4 text-xs">
            <div>
              <h3 className="font-bold text-sm text-foreground flex items-center gap-2">
                <Database className="w-4 h-4 text-emerald-500" />
                <span>Konfigurasi Engine &amp; Nama Database</span>
              </h3>
              <p className="text-gray-500 mt-0.5">
                Ubah jenis database (PostgreSQL, MySQL, SQLite, atau Stateless) dan nama database yang dihubungkan ke container aplikasi.
              </p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-gray-400 font-medium mb-1">Database Engine:</label>
                <select
                  value={selectedDbType}
                  onChange={(e) => setSelectedDbType(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-emerald-500"
                >
                  <option value="postgres">PostgreSQL (:5432) — Direkomendasikan untuk Next.js, Node, Django, FastAPI</option>
                  <option value="mysql">MySQL (:3306) — Cocok untuk Laravel, PHP, WordPress</option>
                  <option value="sqlite">SQLite (File /data/*.db) — Embedded file database</option>
                  <option value="none">Stateless / No Database</option>
                </select>
              </div>
              <div>
                <label className="block text-gray-400 font-medium mb-1">Nama Database:</label>
                <input
                  type="text"
                  value={customDbName}
                  onChange={(e) => setCustomDbName(e.target.value)}
                  placeholder="database_name"
                  className="w-full px-3 py-2 rounded-lg bg-background border border-border text-foreground font-mono text-xs focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>
            </div>

            <div className="p-3 rounded-lg bg-background border border-border text-[11px] text-gray-400">
              Kredensial database (<code>DATABASE_URL</code>, <code>DB_CONNECTION</code>, <code>DB_HOST</code>, <code>DB_PORT</code>, dll.) otomatis di-injeksi ke container saat deployment. Engine database pilihan Anda diprioritaskan mutlak dan sisa variabel lama otomatis dibersihkan.
            </div>
          </div>

          {/* Card 4: Port Mappings */}
          <div className="rounded-xl border border-border bg-card p-5 space-y-4 text-xs">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <h3 className="font-bold text-sm text-foreground flex items-center gap-2">
                  <Globe className="w-4 h-4 text-blue-500" />
                  <span>Konfigurasi Port Mapping Docker</span>
                </h3>
                <p className="text-gray-500 mt-0.5">
                  Ubah port host dan port internal container yang diekspos oleh Docker saat deployment ke server.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setPortMappingsInput(`${recommendedPort}:${effectiveContainerPort}`)}
                className="px-2.5 py-1.5 rounded-lg bg-blue-600/15 hover:bg-blue-600/25 text-blue-400 border border-blue-500/30 font-semibold text-xs shrink-0 flex items-center gap-1 self-start sm:self-auto"
                title="Pilih host port bebas konflik otomatis (>= 3100)"
              >
                <Sparkles className="w-3.5 h-3.5" /> Auto-Bebas :{recommendedPort}
              </button>
            </div>

            <div>
              <label className="block text-gray-400 font-medium mb-1">
                Port Mapping (Format: <code>HOST_PORT:CONTAINER_PORT</code>):
              </label>
              <input
                type="text"
                value={portMappingsInput}
                onChange={(e) => setPortMappingsInput(e.target.value)}
                placeholder={`${recommendedPort}:3000`}
                className={`w-full sm:w-1/2 px-3 py-2 rounded-lg bg-background border text-foreground font-mono text-xs focus:outline-none focus:ring-2 ${
                  settingsPortConflictReason
                    ? 'border-rose-500/80 focus:ring-rose-500'
                    : 'border-border focus:ring-blue-500'
                }`}
              />

              {settingsPortConflictReason && (
                <div className="mt-2.5 p-3 rounded-xl border border-rose-500/40 bg-rose-500/10 text-rose-300 text-xs flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
                    <span>
                      Port Host <b>{parsedHostPortStr}</b> sudah dipakai oleh <b>{settingsPortConflictReason}</b>!
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setPortMappingsInput(`${recommendedPort}:${effectiveContainerPort}`)}
                    className="px-2.5 py-1 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs shrink-0 flex items-center gap-1 shadow-sm self-start sm:self-auto"
                  >
                    <Sparkles className="w-3.5 h-3.5" /> Gunakan Port Aman :{recommendedPort}
                  </button>
                </div>
              )}

              {/* Quick Safe Preset Buttons */}
              <div className="flex flex-wrap gap-2 pt-2 text-[11px]">
                <span className="text-gray-500 self-center">Preset Bebas Konflik:</span>
                {[
                  `${recommendedPort}:3000`,
                  `${recommendedPort}:8000`,
                  `${recommendedPort}:8080`,
                  `${recommendedPort}:5000`,
                  `${recommendedPort}:80`,
                ].map((preset) => (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => setPortMappingsInput(preset)}
                    className={`px-2.5 py-1 rounded-lg border font-mono transition-colors ${
                      portMappingsInput.trim() === preset
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'border-border hover:bg-background text-gray-400 hover:text-foreground'
                    }`}
                  >
                    {preset}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Card 5: Action Toolbar (Simpan & Terapkan ke Server) */}
          <div className="rounded-xl border border-border bg-card p-5 flex flex-col sm:flex-row items-center justify-between gap-4">
            <div>
              <div className="font-bold text-sm text-foreground">Terapkan Perubahan ke Server</div>
              <div className="text-xs text-gray-500">
                Simpan konfigurasi dan luncurkan release baru ke container Docker di server homelab.
              </div>
            </div>

            <div className="flex items-center gap-3 w-full sm:w-auto">
              <button
                type="button"
                onClick={() => handleUpdateProjectSettings(false)}
                disabled={savingProjectSettings || deployingToServer}
                className="flex-1 sm:flex-none px-4 py-2.5 rounded-xl border border-border hover:bg-card text-foreground text-xs font-semibold flex items-center justify-center gap-2 transition-all disabled:opacity-50"
              >
                <Save className="w-4 h-4 text-gray-400" />
                <span>{savingProjectSettings ? 'Menyimpan...' : 'Simpan Perubahan'}</span>
              </button>

              <button
                type="button"
                onClick={() => handleUpdateProjectSettings(true)}
                disabled={savingProjectSettings || deployingToServer}
                className="flex-1 sm:flex-none px-5 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold flex items-center justify-center gap-2 shadow-lg shadow-blue-600/25 transition-all disabled:opacity-50"
              >
                <Rocket className="w-4 h-4" />
                <span>{deployingToServer ? 'Menerapkan ke Server...' : 'Simpan & Terapkan ke Server'}</span>
              </button>
            </div>
          </div>

          {/* Danger Zone */}
          <div className="rounded-xl border border-rose-500/20 bg-card p-5 space-y-4 text-xs">
            <h3 className="font-bold text-sm text-rose-500 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />
              <span>Project Danger Zone</span>
            </h3>
            <p className="text-gray-500">Menghapus project ini akan mematikan container dan menghapus seluruh rilis terkait.</p>
            <button
              onClick={() => {
                if (confirm('Yakin ingin menghapus project ini secara permanen?')) {
                  api.deleteProject(projectId).then(() => router.push('/projects'));
                }
              }}
              className="px-4 py-2 rounded-lg bg-rose-600/10 hover:bg-rose-600/20 text-rose-600 font-medium transition-colors"
            >
              Hapus Project Permanen
            </button>
          </div>
        </div>
      )}

      {/* Deploy Release Modal */}
      <DeployProjectModal
        isOpen={deployModalOpen}
        onClose={() => setDeployModalOpen(false)}
        servers={servers}
        existingProject={project}
        onSuccess={(result) => {
          setDeployModalOpen(false);
          if (result && result.id) {
            router.push(`/deployments/${result.id}`);
          } else {
            loadData();
          }
        }}
      />
    </div>
  );
}
