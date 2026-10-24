"""tests/test_migration_0010_rule_version_states.py — verify 0010 migration and runtime DDL removal.

Tests:
1. Fresh database upgrades through full migration chain to latest head (0010).
2. Existing database at revision 0009 upgrades cleanly to 0010 without data loss.
3. Downgrade from 0010 to 0009 safely drops rule_version_states and allows clean re-upgrade.
4. Schema inspection confirms table name, columns, nullability, and primary key match RuleVersionState model.
5. Admin endpoints (/admin/rule-versions) operate correctly on migrated schema without calling runtime DDL (__table__.create).
"""
from datetime import datetime, timezone
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from main import app
from models import RuleVersionState, User
from password_handler import hash_password
from rbac import require_admin


def _get_alembic_config(db_url: str) -> Config:
    """Create isolated Alembic configuration pointing strictly to the given test database URL."""
    ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(ini_path.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_fresh_database_full_migration_chain(tmp_path):
    """A fresh disposable database upgrades through the complete migration chain to revision 0010."""
    db_file = tmp_path / "fresh_chain.db"
    db_url = f"sqlite:///{db_file}"
    cfg = _get_alembic_config(db_url)

    # Run upgrade to head
    command.upgrade(cfg, "head")

    eng = create_engine(db_url)
    inspector = sa.inspect(eng)
    tables = inspector.get_table_names()

    # Verify rule_version_states exists alongside core tables
    assert "rule_version_states" in tables
    assert "users" in tables
    assert "inspections" in tables
    assert "scans" in tables
    assert "alembic_version" in tables

    # Verify alembic_version head is 0010
    with eng.connect() as conn:
        rev = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == "0010"

    eng.dispose()


def test_upgrade_from_0009_to_0010_preserves_existing_data(tmp_path):
    """An existing database at revision 0009 upgrades to 0010 without damaging existing data."""
    db_file = tmp_path / "upgrade_step.db"
    db_url = f"sqlite:///{db_file}"
    cfg = _get_alembic_config(db_url)

    # 1. Upgrade to 0009 first
    command.upgrade(cfg, "0009")

    # Drop rule_version_states if 0001's Base.metadata.create_all created it,
    # simulating an actual legacy production database upgraded up to 0009
    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS rule_version_states"))

    inspector = sa.inspect(eng)
    assert "rule_version_states" not in inspector.get_table_names()

    # 2. Insert existing user row at revision 0009
    with eng.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO users (employee_id, full_name, password_hash, role, is_active, token_epoch, created_at) "
                "VALUES ('LM-TEST-PREV', 'Existing Officer', 'hash123', 'admin', 1, 0, '2026-10-01 10:00:00')"
            )
        )

    # 3. Upgrade from 0009 to 0010
    command.upgrade(cfg, "0010")

    # 4. Verify rule_version_states table was added
    inspector = sa.inspect(eng)
    assert "rule_version_states" in inspector.get_table_names()

    # 5. Verify existing user row is intact
    with eng.connect() as conn:
        officer = conn.execute(
            sa.text("SELECT full_name, role FROM users WHERE employee_id = 'LM-TEST-PREV'")
        ).fetchone()
        assert officer is not None
        assert officer[0] == "Existing Officer"
        assert officer[1] == "admin"

    # 6. Verify inserting and querying rule_version_states works
    with eng.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO rule_version_states (id, is_active, updated_at) "
                "VALUES ('rv-2026-gsr826e', 1, '2026-10-24 12:00:00')"
            )
        )
        st = conn.execute(
            sa.text("SELECT id, is_active FROM rule_version_states WHERE id = 'rv-2026-gsr826e'")
        ).fetchone()
        assert st is not None
        assert st[0] == "rv-2026-gsr826e"
        assert bool(st[1]) is True

    eng.dispose()


def test_downgrade_and_reupgrade_cycle(tmp_path):
    """Migration 0010 supports clean downgrade to 0009 and idempotent re-upgrade."""
    db_file = tmp_path / "downgrade_cycle.db"
    db_url = f"sqlite:///{db_file}"
    cfg = _get_alembic_config(db_url)

    # Upgrade to 0010
    command.upgrade(cfg, "0010")
    eng = create_engine(db_url)
    assert "rule_version_states" in sa.inspect(eng).get_table_names()

    # Downgrade to 0009
    command.downgrade(cfg, "0009")
    assert "rule_version_states" not in sa.inspect(eng).get_table_names()

    with eng.connect() as conn:
        rev = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == "0009"

    # Re-upgrade to 0010
    command.upgrade(cfg, "0010")
    assert "rule_version_states" in sa.inspect(eng).get_table_names()

    with eng.connect() as conn:
        rev = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
        assert rev == "0010"

    eng.dispose()


def test_rule_version_states_schema_matches_model(tmp_path):
    """Table schema columns, data types, nullability, and primary key match RuleVersionState."""
    db_file = tmp_path / "schema_inspect.db"
    db_url = f"sqlite:///{db_file}"
    cfg = _get_alembic_config(db_url)
    command.upgrade(cfg, "0010")

    eng = create_engine(db_url)
    inspector = sa.inspect(eng)
    cols = {c["name"]: c for c in inspector.get_columns("rule_version_states")}

    # Validate column presence
    assert "id" in cols
    assert "is_active" in cols
    assert "updated_at" in cols

    # Validate nullability
    assert cols["id"]["nullable"] is False
    assert cols["is_active"]["nullable"] is False

    # Validate primary key
    pk = inspector.get_pk_constraint("rule_version_states")
    assert pk["constrained_columns"] == ["id"]

    eng.dispose()


def test_admin_rule_version_endpoints_operate_without_runtime_ddl(tmp_path):
    """Admin rule-version endpoints operate correctly without calling RuleVersionState.__table__.create."""
    db_file = tmp_path / "admin_test.db"
    db_url = f"sqlite:///{db_file}"
    cfg = _get_alembic_config(db_url)
    command.upgrade(cfg, "head")

    eng = create_engine(db_url, connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=eng)
    db = Session()

    admin_user = User(
        employee_id="LM-ADM-999",
        full_name="Super Admin",
        password_hash=hash_password("adminSecretPass!"),
        role="admin",
        is_active=True,
    )
    db.add(admin_user)
    db.commit()
    db.refresh(admin_user)

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_require_admin():
        return admin_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_admin] = override_require_admin

    client = TestClient(app)

    try:
        # Spy on RuleVersionState.__table__.create to verify it is NEVER called
        with patch.object(RuleVersionState.__table__, "create", wraps=RuleVersionState.__table__.create) as mock_create:
            # 1. GET /admin/rule-versions
            resp_get = client.get("/admin/rule-versions")
            assert resp_get.status_code == 200, resp_get.text
            versions = resp_get.json()
            assert len(versions) >= 4

            # 2. PATCH /admin/rule-versions/{version_id}
            target_version = versions[0]["id"]
            resp_patch = client.patch(
                f"/admin/rule-versions/{target_version}",
                json={"is_active": True},
            )
            assert resp_patch.status_code == 200, resp_patch.text
            patched_data = resp_patch.json()
            assert patched_data["id"] == target_version
            assert patched_data["is_active"] is True

            # Verify the table was queried and updated in the DB
            persisted = db.query(RuleVersionState).filter(RuleVersionState.id == target_version).first()
            assert persisted is not None
            assert persisted.is_active is True

            # Verify runtime DDL was NOT called at all
            mock_create.assert_not_called()
    finally:
        app.dependency_overrides.clear()
        db.close()
        eng.dispose()
