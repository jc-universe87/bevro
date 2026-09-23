"""Test fixtures.

Tests run against a separate `bevro_test` database on the same PostgreSQL
server, created on demand. Each test gets a session inside a transaction
that is rolled back afterwards, so tests never see each other's rows.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

TEST_URL = os.environ.get("BEVRO_TEST_DATABASE_URL", "postgresql+psycopg://bevro:bevro@db:5432/bevro_test")
if "test" not in make_url(TEST_URL).database:
    raise RuntimeError("BEVRO_TEST_DATABASE_URL must point at a database whose name contains 'test'")

_artifact_dir = tempfile.mkdtemp(prefix="bevro-artifacts-")
os.environ["BEVRO_DATABASE_URL"] = TEST_URL
os.environ["BEVRO_ARTIFACT_DIR"] = _artifact_dir
os.environ["BEVRO_SECRET_KEY"] = Fernet.generate_key().decode()
os.environ["BEVRO_WORKSPACES_FILE"] = os.path.join(_artifact_dir, "no-workspaces.json")
os.environ["BEVRO_LOG_DIR"] = os.path.join(_artifact_dir, "logs")
# Delivery is off in tests whatever this machine happens to be set up with:
# a test that needs email hands its own Settings in.
os.environ["BEVRO_SMTP_HOST"] = ""
os.environ["BEVRO_NOTIFY_EMAIL"] = ""
os.environ["BEVRO_NOTIFY_WEBHOOK_URL"] = ""
os.environ["BEVRO_APP_URL"] = "http://localhost:6140"

from app.config import get_settings  # noqa: E402
from app.db import Base, get_db  # noqa: E402
import app.models  # noqa: E402,F401

get_settings.cache_clear()


def _ensure_database() -> None:
    url = make_url(TEST_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine():
    _ensure_database()
    eng = create_engine(TEST_URL, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine) -> Iterator[Session]:
    connection = engine.connect()
    trans = connection.begin()
    session = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint")()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def seeded(db: Session):
    from app.services.providers import seed_examples

    seed_examples(db)
    return db


@pytest.fixture
def client(db: Session):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import tasks as task_service

    app.dependency_overrides[get_db] = lambda: db
    # Run provider work inline and inside the test transaction instead of in a
    # background thread with its own session.
    original = task_service.execute_run_in_background

    def inline(run_id):
        task_service.execute_run(db, run_id)

    task_service.execute_run_in_background = inline
    # Startup seeding would commit outside the test transaction; the `seeded`
    # fixture does it inside instead.
    import app.main as main_module

    original_seed = main_module.seed_examples
    original_ws_seed = main_module.seed_from_config
    original_rt = main_module.ensure_runtimes
    main_module.seed_examples = lambda _db: 0
    main_module.seed_from_config = lambda _db: 0
    main_module.ensure_runtimes = lambda _db: 0
    try:
        with TestClient(app) as c:
            yield c
    finally:
        main_module.seed_examples = original_seed
        main_module.seed_from_config = original_ws_seed
        main_module.ensure_runtimes = original_rt
        task_service.execute_run_in_background = original
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def fast_providers(monkeypatch):
    """Example providers pause for realism; tests do not need that."""
    import providers.event_demo as event_demo
    import providers.research as research

    monkeypatch.setattr(research, "WORK_SECONDS", 0)
    monkeypatch.setattr(event_demo, "WORK_SECONDS", 0)
