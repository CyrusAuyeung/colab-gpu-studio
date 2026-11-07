import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db import Store
from app.gpu.repository import ensure_schema
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

MIGRATION = Path(__file__).parents[2] / "migrations" / "versions" / "0003_accounts_instances.py"


def _load_migration(connection):
    spec = importlib.util.spec_from_file_location("v020_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def _legacy_store(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'legacy.db'}")
    # Store currently knows the v0.2.0 metadata, so reduce this database to
    # the pre-migration shape before exercising the forward migration.
    with store.engine.begin() as connection:
        for table in (
            "gpu_operations",
            "gpu_allocation_cursors",
            "gpu_instance_usage_segments",
            "gpu_instances",
            "gpu_accounts",
        ):
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
        for table, columns in {
            "gpu_jobs": ("account_id", "instance_id"),
            "gpu_executions": ("account_id", "instance_id"),
            "gpu_leases": ("account_id", "instance_id"),
            "gpu_credentials": ("account_id",),
        }.items():
            for index in inspect(store.engine).get_indexes(table):
                if any(column in columns for column in index["column_names"]):
                    connection.execute(text(f"DROP INDEX IF EXISTS {index['name']}"))
            for column in columns:
                connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    payloads = [
        (
            "gpu_credentials",
            "cred-colab",
            "owner-1",
            None,
            None,
            {"provider": "google", "purpose": "colab", "encrypted_refresh_token": "DO_NOT_COPY"},
        ),
        (
            "gpu_credentials",
            "cred-drive",
            "owner-1",
            None,
            None,
            {"provider": "google", "purpose": "drive", "encrypted_refresh_token": "DO_NOT_COPY"},
        ),
        (
            "gpu_leases",
            "lease-active",
            "owner-1",
            "active",
            None,
            {"owner_job_id": "job-1", "runtime_id": "runtime-1"},
        ),
        (
            "gpu_leases",
            "lease-deleted",
            "owner-1",
            "deleted",
            None,
            {"owner_job_id": "job-old", "runtime_id": "runtime-old"},
        ),
        (
            "gpu_jobs",
            "job-1",
            "owner-1",
            "completed",
            None,
            {"lease_id": "lease-active", "provider": "colab"},
        ),
        (
            "gpu_executions",
            "execution-1",
            "owner-1",
            "completed",
            None,
            {"job_id": "job-1", "lease_id": "lease-active"},
        ),
    ]
    with store.engine.begin() as connection:
        for table, row_id, owner_id, state, project_id, data in payloads:
            columns = "id, owner_id, project_id, state, version, data, created_at, updated_at"
            values = (
                "(:id, :owner_id, :project_id, :state, 0, :data, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            if table == "gpu_jobs":
                columns += ", idempotency_key, body_hash"
                values = values[:-1] + ", :idempotency_key, :body_hash)"
            connection.execute(
                text(f"INSERT INTO {table} ({columns}) VALUES {values}"),
                {
                    "id": row_id,
                    "owner_id": owner_id,
                    "project_id": project_id,
                    "state": state,
                    "data": json.dumps(data),
                    "idempotency_key": "legacy-job-1" if table == "gpu_jobs" else None,
                    "body_hash": "legacy-hash" if table == "gpu_jobs" else None,
                },
            )
    return store


def test_v020_entities_are_registered_with_instance_occupancy_constraint(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'fresh.db'}")
    ensure_schema(store)
    tables = set(inspect(store.engine).get_table_names())
    assert {
        "gpu_accounts",
        "gpu_instances",
        "gpu_instance_usage_segments",
        "gpu_allocation_cursors",
        "gpu_operations",
    } <= tables
    assert {"account_id", "instance_id"} <= {
        c["name"] for c in inspect(store.engine).get_columns("gpu_jobs")
    }
    assert {"account_id", "instance_id"} <= {
        c["name"] for c in inspect(store.engine).get_columns("gpu_executions")
    }
    assert {"account_id", "instance_id"} <= {
        c["name"] for c in inspect(store.engine).get_columns("gpu_leases")
    }
    assert "account_id" in {c["name"] for c in inspect(store.engine).get_columns("gpu_credentials")}

    with store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO gpu_instances "
                "(id, owner_id, current_job_id, version, data, created_at, updated_at) "
                "VALUES ('instance-1', 'owner-1', 'job-1', 0, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO gpu_instances "
                    "(id, owner_id, current_job_id, version, data, created_at, updated_at) "
                    "VALUES ('instance-2', 'owner-1', 'job-1', 0, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )


def test_forward_migration_is_idempotent_and_never_copies_credential_tokens(tmp_path):
    store = _legacy_store(tmp_path)
    with store.engine.begin() as connection:
        migration = _load_migration(connection)
        migration.upgrade()
        migration.upgrade()

        account = connection.execute(
            text("SELECT id, owner_id, data FROM gpu_accounts WHERE owner_id='owner-1'")
        ).mappings().one()
        account_data = json.loads(account["data"])
        assert account["id"]
        assert account["owner_id"] == "owner-1"
        assert "DO_NOT_COPY" not in json.dumps(account_data)
        assert connection.execute(text("SELECT COUNT(*) FROM gpu_accounts")).scalar_one() == 1

        credential_ids = connection.execute(
            text(
                "SELECT account_id FROM gpu_credentials "
                "WHERE id IN ('cred-colab', 'cred-drive') ORDER BY id"
            )
        ).scalars().all()
        assert credential_ids == [account["id"], account["id"]]

        active_instance = connection.execute(
            text(
                "SELECT id, state FROM gpu_instances "
                "WHERE owner_id='owner-1' AND data LIKE '%lease-active%'"
            )
        ).mappings().one()
        deleted_instance = connection.execute(
            text(
                "SELECT state FROM gpu_instances "
                "WHERE owner_id='owner-1' AND data LIKE '%lease-deleted%'"
            )
        ).scalar_one()
        assert active_instance["state"] == "unknown"
        assert deleted_instance == "deleted"

        linked = connection.execute(
            text("SELECT instance_id, owner_id FROM gpu_jobs WHERE id='job-1'")
        ).mappings().one()
        execution = connection.execute(
            text("SELECT instance_id FROM gpu_executions WHERE id='execution-1'")
        ).scalar_one()
        assert linked["instance_id"] == active_instance["id"]
        assert linked["owner_id"] == "owner-1"
        assert execution == active_instance["id"]


def test_downgrade_refuses_live_instances_but_preserves_legacy_rows(tmp_path):
    store = _legacy_store(tmp_path)
    with store.engine.begin() as connection:
        migration = _load_migration(connection)
        migration.upgrade()
        with pytest.raises(RuntimeError, match="活跃|active|停止"):
            migration.downgrade()

        connection.execute(text("UPDATE gpu_instances SET state='deleted'"))
        migration.downgrade()
        assert connection.execute(text("SELECT COUNT(*) FROM gpu_jobs")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM gpu_leases")).scalar_one() == 2
        assert connection.execute(text("SELECT COUNT(*) FROM gpu_credentials")).scalar_one() == 2
