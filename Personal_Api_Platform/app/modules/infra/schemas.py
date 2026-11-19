"""Pydantic schemas for unified infrastructure management."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ServerMetrics(BaseModel):
    hostname: str = Field(default="homelab", description="Server hostname")
    status: str = Field(default="online", description="Overall server operational status")
    cpu_usage: float = Field(default=0.0, description="CPU utilization percentage")
    ram_usage: float = Field(default=0.0, description="Memory utilization percentage")
    disk_usage: float = Field(default=0.0, description="Disk utilization percentage")
    ip_lan: Optional[str] = Field(default="192.168.25.105", description="Local network IP")
    ip_tailscale: Optional[str] = Field(default="100.78.113.53", description="Tailscale VPN IP")
    os: Optional[str] = Field(default="Ubuntu Server 24.04 LTS", description="Operating system")
    uptime_seconds: Optional[int] = Field(default=0, description="Uptime in seconds")


class DockerOverview(BaseModel):
    running: int = Field(default=0, description="Number of running containers")
    stopped: int = Field(default=0, description="Number of stopped/exited containers")
    unhealthy: int = Field(default=0, description="Number of unhealthy containers")
    total: int = Field(default=0, description="Total managed containers")


class DatabaseOverview(BaseModel):
    postgresql: str = Field(default="healthy", description="PostgreSQL status")
    mysql: str = Field(default="healthy", description="MySQL status")
    details: Optional[Dict[str, str]] = Field(default_factory=dict, description="Per-database status")


class InfraOverviewResponse(BaseModel):
    server: ServerMetrics
    docker: DockerOverview
    databases: Dict[str, str]
    services: Dict[str, str]


class ServiceItem(BaseModel):
    name: str
    category: str
    port: int
    url: str
    status: str
    reachable: bool
    version: Optional[str] = None


class ServerNodeItem(BaseModel):
    id: str
    name: str
    hostname: str
    ip_lan: str
    ip_tailscale: str
    status: str
    cpu_usage: float
    ram_usage: float
    disk_usage: float
    containers_count: int
