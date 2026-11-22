import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.app.database.session import Base
from apps.api.app.models.entities import Deployment, DeploymentLog, Job, Project, Server, User
from apps.api.app.services import job_service, port_service
from apps.api.app.services.docker_service import DockerEngineService
from apps.api.app.services.port_service import (
    CORE_RESERVED_PORTS,
    _extract_host_ports_from_cli_string,
    _extract_host_ports_from_port_entries,
    get_reserved_ports_map,
    validate_or_allocate_port_mappings,
)


def _make_memory_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    user = User(name="Admin", email="admin@homelab.local", password_hash="x", role="owner")
    db.add(user)
    db.flush()

    server = Server(owner_id=user.id, name="homelab-primary", hostname="192.168.25.105")
    db.add(server)
    db.commit()
    return db, user, server


def test_core_reserved_ports_has_grafana_3000():
    assert CORE_RESERVED_PORTS[3000] == "Grafana (:3000)"


def test_validate_rejects_3000_when_auto_fix_disabled():
    db, _, _ = _make_memory_db()
    with pytest.raises(HTTPException) as exc_info:
        validate_or_allocate_port_mappings(
            db,
            "3000:3000",
            framework="Next.js",
            auto_fix_reserved=False,
            include_docker=False,
        )
    assert exc_info.value.status_code == 400
    assert "Grafana (:3000)" in exc_info.value.detail
    db.close()


def test_validate_auto_allocates_3100_when_none():
    db, _, _ = _make_memory_db()
    allocated, notice = validate_or_allocate_port_mappings(
        db,
        None,
        framework="Next.js",
        auto_fix_reserved=False,
        include_docker=False,
    )
    assert allocated == "3100:3000"
    assert notice is None
    db.close()


def test_validate_auto_heals_legacy_3000_on_redeploy():
    db, user, server = _make_memory_db()
    legacy = Project(
        server_id=server.id,
        owner_id=user.id,
        name="legacy-nextjs",
        framework="Next.js",
        source_type="git",
        port_mappings="3000:3000",
        domain="legacy-nextjs.homelab",
    )
    db.add(legacy)
    db.commit()

    # Core port 3000 must remain Grafana even with a legacy DB row storing 3000:3000
    reserved = get_reserved_ports_map(db, include_docker=False)
    assert reserved[3000] == "Grafana (:3000)"

    healed, notice = validate_or_allocate_port_mappings(
        db,
        legacy.port_mappings,
        framework=legacy.framework,
        exclude_project_id=legacy.id,
        exclude_container_name=legacy.name,
        auto_fix_reserved=True,
        include_docker=False,
    )
    assert healed == "3100:3000"
    assert notice is not None and "Grafana (:3000)" in notice
    db.close()


def test_extract_host_ports_from_docker_uds_and_cli():
    assert _extract_host_ports_from_cli_string("0.0.0.0:3100->80/tcp, [::]:3100->80/tcp, 8080/tcp") == [3100]
    assert _extract_host_ports_from_cli_string("192.168.25.105:3102->3000/tcp, 0.0.0.0:3100->80/tcp") == [3102, 3100]
    assert _extract_host_ports_from_port_entries([
        {"IP": "0.0.0.0", "PrivatePort": 80, "PublicPort": 3100, "Type": "tcp"},
        {"PrivatePort": 80, "Type": "tcp"},
    ]) == [3100]


def test_reserved_ports_detects_live_docker_container_via_uds_and_overrides_stale_project(monkeypatch):
    db, user, server = _make_memory_db()
    fin = Project(
        server_id=server.id,
        owner_id=user.id,
        name="financial-app",
        framework="Next.js",
        source_type="git",
        port_mappings="3100:3000",
        domain="financial-app.homelab",
    )
    db.add(fin)
    db.commit()

    monkeypatch.setattr(
        DockerEngineService,
        "list_containers",
        classmethod(
            lambda cls: [
                {
                    "name": "stock-umkm",
                    "names": ["stock-umkm"],
                    "state": "running",
                    "ports": [{"IP": "0.0.0.0", "PrivatePort": 80, "PublicPort": 3100, "Type": "tcp"}],
                }
            ]
        ),
    )
    monkeypatch.setattr(port_service, "_is_host_port_open", lambda port, probe_hosts=None: False)

    reserved = get_reserved_ports_map(db, include_docker=True)
    assert "stock-umkm" in reserved[3100]

    healed, notice = validate_or_allocate_port_mappings(
        db,
        fin.port_mappings,
        framework=fin.framework,
        exclude_project_id=fin.id,
        exclude_container_name=fin.name,
        auto_fix_reserved=True,
        include_docker=True,
    )
    assert healed == "3101:3000"
    assert notice is not None and "stock-umkm" in notice
    db.close()


def test_complete_job_syncs_effective_port_mappings_and_nginx(monkeypatch):
    db, user, server = _make_memory_db()
    proj = Project(
        server_id=server.id,
        owner_id=user.id,
        name="financial-app",
        framework="Next.js",
        source_type="git",
        port_mappings="3100:3000",
        domain="financial-app.homelab",
        status="active",
    )
    db.add(proj)
    db.flush()

    dep = Deployment(
        project_id=proj.id,
        version=4,
        image_tag="financial-app:v4",
        status="building",
        triggered_by=user.id,
    )
    db.add(dep)
    db.flush()

    job = Job(
        server_id=server.id,
        deployment_id=dep.id,
        job_type="deployment.git",
        payload={"image_tag": "financial-app:v4"},
        status="running",
    )
    db.add(job)
    db.commit()

    routed_calls = []
    monkeypatch.setattr(job_service, "sync_domain_routing", lambda **kwargs: routed_calls.append(kwargs) or True)
    monkeypatch.setattr(job_service, "sync_psm_to_pap", lambda db_session: {"synced": True})

    completed = job_service.complete_job(
        db=db,
        job_id=job.id,
        status="success",
        output={"effective_port_mappings": "3101:3000", "port_reassigned": True},
        duration_seconds=42,
    )

    assert completed is not None and completed.status == "success"
    db.refresh(proj)
    assert proj.port_mappings == "3101:3000"
    assert len(routed_calls) == 1
    assert routed_calls[0]["domain"] == "financial-app.homelab"
    assert routed_calls[0]["target_port"] == 3101

    logs = db.query(DeploymentLog).filter(DeploymentLog.deployment_id == dep.id).all()
    assert any("[Auto-Port]" in l.message and "3101:3000" in l.message for l in logs)
    db.close()
