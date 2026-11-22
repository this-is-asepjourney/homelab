import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.app.database.session import Base
from apps.api.app.models.entities import Project, Server, User
from apps.api.app.services.port_service import (
    CORE_RESERVED_PORTS,
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
