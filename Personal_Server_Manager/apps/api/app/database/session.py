import os
import re
import socket
from urllib.parse import urlparse
from typing import Generator, Dict, Any
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from apps.api.app.core.config import settings

db_url = settings.DATABASE_URL
if db_url.startswith("postgresql+psycopg://"):
    try:
        import psycopg  # type: ignore # noqa: F401
    except ImportError:
        db_url = db_url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
elif db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)

ACTIVE_PSM_DB_INFO: Dict[str, Any] = {
    "engine": "sqlite" if db_url.startswith("sqlite") else "postgresql",
    "host": os.getenv("POSTGRES_HOST", settings.POSTGRES_HOST),
    "port": int(os.getenv("POSTGRES_PORT", settings.POSTGRES_PORT)),
    "user": os.getenv("POSTGRES_USER", settings.POSTGRES_USER),
    "db_name": os.getenv("POSTGRES_DB", settings.POSTGRES_DB),
    "auto_created": False,
    "connected": False,
}


def _auto_ensure_psm_postgres_db(url: str) -> str:
    """
    Connect to the configured PostgreSQL server and ensure the 'psm' database exists.
    Uses fast, targeted connection attempts when explicit credentials are provided
    to avoid slow multi-host/multi-password timeouts during cold container boot.
    """
    if not url.startswith("postgres"):
        return url

    try:
        import psycopg2
        norm = url.replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
        u = urlparse(norm)

        explicit_env_creds = bool(os.getenv("POSTGRES_USER") and os.getenv("POSTGRES_PASSWORD"))
        target_user = os.getenv("POSTGRES_USER") or u.username or "appuser"
        target_pass = os.getenv("POSTGRES_PASSWORD") or u.password or "admin12345"
        target_host = os.getenv("POSTGRES_HOST") or u.hostname or "postgres"
        target_port = int(os.getenv("POSTGRES_PORT") or u.port or 5432)
        target_db = os.getenv("POSTGRES_DB") or (u.path.lstrip("/") if u.path else "psm") or "psm"
        clean_db = re.sub(r"[^a-zA-Z0-9_]", "_", target_db)

        if explicit_env_creds:
            candidate_hosts = [target_host]
            if "postgres" not in candidate_hosts:
                candidate_hosts.append("postgres")
            candidate_creds = [(target_user, target_pass)]
            if target_user != "postgres":
                candidate_creds.append(("postgres", target_pass))
            maint_dbs = ("postgres", "template1")
        else:
            candidate_hosts = []
            for h in [target_host, "postgres", "host.docker.internal", "127.0.0.1"]:
                if h and h not in candidate_hosts:
                    candidate_hosts.append(h)
            candidate_creds = []
            for cred in [
                (target_user, target_pass),
                ("fadb", "wokeh123"),
                ("appuser", "admin12345"),
                ("postgres", target_pass),
                ("psm", "psm_super_secret_password_change_in_production"),
            ]:
                if cred not in candidate_creds:
                    candidate_creds.append(cred)
            maint_dbs = ("postgres", "template1", "psm")

        for host in candidate_hosts:
            try:
                with socket.create_connection((host, target_port), timeout=1.5):
                    pass
            except Exception:
                continue

            for user, pw in candidate_creds:
                # 1. Try direct connection to target_db (e.g. 'psm')
                try:
                    c = psycopg2.connect(
                        host=host, port=target_port, user=user, password=pw, dbname=clean_db, connect_timeout=3
                    )
                    c.autocommit = True
                    c.close()
                    ACTIVE_PSM_DB_INFO.update({
                        "engine": "postgresql",
                        "host": host,
                        "port": target_port,
                        "user": user,
                        "db_name": clean_db,
                        "auto_created": False,
                        "connected": True,
                    })
                    print(
                        f"[PSM Database] Connected to PostgreSQL database '{clean_db}' at {host}:{target_port} (user: {user})",
                        flush=True,
                    )
                    return f"postgresql+psycopg2://{user}:{pw}@{host}:{target_port}/{clean_db}"
                except Exception as direct_err:
                    # If authentication failed on explicit user, don't waste time probing maintenance DBs with same user
                    if "password authentication failed" in str(direct_err).lower():
                        continue

                # 2. Database 'psm' may not exist yet -> connect to maintenance DB and auto-create 'psm'
                for maint_db in maint_dbs:
                    try:
                        c = psycopg2.connect(
                            host=host, port=target_port, user=user, password=pw, dbname=maint_db, connect_timeout=3
                        )
                        c.autocommit = True
                        cur = c.cursor()

                        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (clean_db,))
                        created_now = False
                        if not cur.fetchone():
                            print(
                                f"[PSM Database] Database '{clean_db}' not found on {host}:{target_port} — automatically creating...",
                                flush=True,
                            )
                            cur.execute(f'CREATE DATABASE "{clean_db}" OWNER "{user}" ENCODING \'UTF8\'')
                            created_now = True

                        cur.close()
                        c.close()

                        ACTIVE_PSM_DB_INFO.update({
                            "engine": "postgresql",
                            "host": host,
                            "port": target_port,
                            "user": user,
                            "db_name": clean_db,
                            "auto_created": created_now,
                            "connected": True,
                        })
                        return f"postgresql+psycopg2://{user}:{pw}@{host}:{target_port}/{clean_db}"
                    except Exception:
                        continue
    except Exception as e:
        print(f"[PSM Database] Auto-ensure note: {e}", flush=True)

    return url


# Skip eager network connections at import time during unit tests
if os.getenv("TESTING") != "true" and not db_url.startswith("sqlite"):
    db_url = _auto_ensure_psm_postgres_db(db_url)

# Configure engine. For SQLite, need check_same_thread=False
connect_args = {}
if db_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    db_url,
    connect_args=connect_args,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
