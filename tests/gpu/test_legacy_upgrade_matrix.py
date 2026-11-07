"""真实旧库升级回归（只读应用代码）。

Fixture 的 SQL DDL 来自官方 dev8 peeled commit ``af9d684...`` 的
``api/app/gpu/repository.py`` / ``CommonMixin``，不是用当前 ORM
``Base.metadata.create_all`` 伪造旧库。旧库没有 0003 新增的 typed identity
columns，也没有 accounts/instances 等表；测试分别验证真实 Alembic 0003
迁移和应用启动的 ``Repository.ensure_schema`` 路径。

不同历史版本的实际矩阵由主代理 gen2 运行；这里保留一套确定性的旧库
shape 回归，避免用重复 label 的同一 fixture 冒充版本覆盖。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.config import settings
from app.db import Store
from app.gpu.accounts import AccountService
from app.gpu.legacy_migration import upgrade_legacy_identity
from app.gpu.repository import Repository
from app.security import decrypt_secret, encrypt_secret
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).parents[2] / "migrations" / "versions" / "0003_accounts_instances.py"

# This is the pre-0003 table family from the peeled dev8 repository.  Keep this
# DDL explicit: importing the current repository before creating a fixture would
# silently add account_id/instance_id and make the regression meaningless.
_COMMON_COLUMNS = """
    id VARCHAR(64) PRIMARY KEY,
    owner_id VARCHAR(128),
    project_id VARCHAR(128),
    state VARCHAR(64),
    version INTEGER NOT NULL,
    data JSON NOT NULL,
    created_at DATETIME,
    updated_at DATETIME
"""
_OLD_TABLE_EXTRAS = {
    "gpu_models": "",
    "gpu_workflows": ", workflow_key VARCHAR(255) NOT NULL, workflow_version VARCHAR(64) NOT NULL",
    "gpu_jobs": ", idempotency_key VARCHAR(255) NOT NULL, body_hash VARCHAR(64) NOT NULL",
    "gpu_batches": ", idempotency_key VARCHAR(255) NOT NULL, body_hash VARCHAR(64) NOT NULL",
    "gpu_executions": "",
    "gpu_leases": "",
    "gpu_events": "",
    "gpu_presets": "",
    "gpu_usage": "",
    "gpu_files": "",
    "gpu_credentials": "",
}


def _create_official_dev8_schema(path: Path) -> None:
    """Create the old schema directly, without current SQLAlchemy metadata."""
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for table, extras in _OLD_TABLE_EXTRAS.items():
            connection.execute(text(f"CREATE TABLE {table} ({_COMMON_COLUMNS}{extras})"))
            connection.execute(text(f"CREATE INDEX ix_{table}_owner_id ON {table} (owner_id)"))
            connection.execute(text(f"CREATE INDEX ix_{table}_project_id ON {table} (project_id)"))
            connection.execute(text(f"CREATE INDEX ix_{table}_state ON {table} (state)"))
        connection.execute(
            text(
                "CREATE UNIQUE INDEX uq_gpu_workflows_owner_key_version "
                "ON gpu_workflows (owner_id, workflow_key, workflow_version)"
            )
        )
        connection.execute(
            text("CREATE UNIQUE INDEX uq_gpu_jobs_owner_idempotency ON gpu_jobs (owner_id, idempotency_key)")
        )
        connection.execute(
            text("CREATE UNIQUE INDEX uq_gpu_batches_owner_key ON gpu_batches (owner_id, idempotency_key)")
        )
    engine.dispose()


def _insert_legacy_row(connection, table: str, row_id: str, owner_id: str, state: str | None, data: dict, **extra) -> None:
    columns = ["id", "owner_id", "project_id", "state", "version", "data", "created_at", "updated_at"]
    values = {
        "id": row_id,
        "owner_id": owner_id,
        "project_id": None,
        "state": state,
        "version": 0,
        "data": json.dumps(data, ensure_ascii=False, separators=(",", ":")),
    }
    if table == "gpu_jobs":
        columns.extend(("idempotency_key", "body_hash"))
        values.update(idempotency_key=extra["idempotency_key"], body_hash=extra["body_hash"])
    placeholders = ", ".join(f":{column}" for column in columns if column not in {"created_at", "updated_at"})
    insert_columns = ", ".join(column for column in columns if column not in {"created_at", "updated_at"})
    connection.execute(
        text(
            f"INSERT INTO {table} ({insert_columns}, created_at, updated_at) "
            f"VALUES ({placeholders}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        values,
    )


def _legacy_fixture(tmp_path: Path) -> tuple[Path, dict]:
    path = tmp_path / "official-dev8-pre0003.db"
    _create_official_dev8_schema(path)
    tokens = {
        "owner-a": {"colab": "virtual-owner-a-colab-token", "drive": "virtual-owner-a-drive-token"},
        "owner-b": {"colab": "virtual-owner-b-colab-token"},
    }
    expected = {"tokens": {}, "accounts": {}, "instances": {}}
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for owner, purposes in tokens.items():
            expected["tokens"][owner] = {}
            for purpose, plaintext in purposes.items():
                ciphertext = encrypt_secret(plaintext)
                expected["tokens"][owner][purpose] = ciphertext
                _insert_legacy_row(
                    connection,
                    "gpu_credentials",
                    f"{owner}-cred-{purpose}",
                    owner,
                    None,
                    {
                        "provider": "google",
                        "purpose": purpose,
                        "encrypted_access_token": encrypt_secret(f"access-{plaintext}"),
                        "encrypted_refresh_token": ciphertext,
                        "expires_at": 4102444800,
                    },
                )

        for owner in ("owner-a", "owner-b"):
            job_id = f"{owner}-job"
            lease_id = f"{owner}-lease"
            execution_id = f"{owner}-execution"
            _insert_legacy_row(
                connection,
                "gpu_leases",
                lease_id,
                owner,
                "active",
                {"owner_job_id": job_id, "runtime_id": f"runtime-{owner}"},
            )
            _insert_legacy_row(
                connection,
                "gpu_jobs",
                job_id,
                owner,
                "completed",
                {"lease_id": lease_id, "provider": "colab", "request_body": {"kind": "virtual"}},
                idempotency_key=f"legacy-{owner}-job",
                body_hash=f"legacy-{owner}-hash",
            )
            _insert_legacy_row(
                connection,
                "gpu_executions",
                execution_id,
                owner,
                "completed",
                {"job_id": job_id, "lease_id": lease_id},
            )
            expected["instances"][owner] = {
                "lease_id": lease_id,
                "job_id": job_id,
                "execution_id": execution_id,
            }
    engine.dispose()
    return path, expected


def _open_legacy_store(tmp_path: Path) -> tuple[Store, dict]:
    path, expected = _legacy_fixture(tmp_path)
    return Store(f"sqlite:///{path}"), expected


def _load_migration(connection):
    spec = importlib.util.spec_from_file_location("legacy_upgrade_matrix_0003", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def _run_0003(store: Store) -> None:
    with store.engine.begin() as connection:
        _load_migration(connection).upgrade()


def _prepare_virtual_encryption(monkeypatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "site_url", "http://offline.test")


def test_fixture_is_the_real_pre0003_shape_not_current_model(tmp_path, monkeypatch):
    _prepare_virtual_encryption(monkeypatch)
    path, _ = _legacy_fixture(tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    inspector = inspect(engine)
    assert "gpu_accounts" not in inspector.get_table_names()
    assert "gpu_instances" not in inspector.get_table_names()
    assert "account_id" not in {item["name"] for item in inspector.get_columns("gpu_credentials")}
    assert "account_id" not in {item["name"] for item in inspector.get_columns("gpu_jobs")}
    assert "instance_id" not in {item["name"] for item in inspector.get_columns("gpu_leases")}
    engine.dispose()


def test_startup_migrates_official_dev8_database(tmp_path, monkeypatch):
    """Repository startup repairs the old schema and legacy identity graph."""
    _prepare_virtual_encryption(monkeypatch)
    store, _ = _open_legacy_store(tmp_path)
    Repository(store)
    Repository(store)  # simulate two process starts against the same upgraded file

    with store.engine.connect() as connection:
        account_count = connection.execute(text("SELECT COUNT(*) FROM gpu_accounts")).scalar_one()
        instance_count = connection.execute(text("SELECT COUNT(*) FROM gpu_instances")).scalar_one()
    assert account_count == 2, "startup did not materialize legacy accounts"
    assert instance_count == 2, "startup did not materialize legacy instances"


def test_actual_0003_migration_preserves_ciphertext_owner_scope_and_bindings(tmp_path, monkeypatch):
    _prepare_virtual_encryption(monkeypatch)
    store, expected = _open_legacy_store(tmp_path)
    _run_0003(store)
    _run_0003(store)

    with store.engine.connect() as connection:
        accounts = connection.execute(text("SELECT id, owner_id, data FROM gpu_accounts ORDER BY owner_id")).mappings().all()
        assert [row["owner_id"] for row in accounts] == ["owner-a", "owner-b"]
        for row in accounts:
            account_data = json.loads(row["data"])
            assert row["id"] == AccountService.default_account_id(row["owner_id"])
            assert not any(ciphertext in json.dumps(account_data) for ciphertext in expected["tokens"][row["owner_id"]].values())

        for owner, purposes in expected["tokens"].items():
            rows = connection.execute(
                text(
                    "SELECT id, account_id, data FROM gpu_credentials "
                    "WHERE owner_id=:owner ORDER BY id"
                ),
                {"owner": owner},
            ).mappings().all()
            by_purpose = {json.loads(row["data"])["purpose"]: (row, json.loads(row["data"])) for row in rows}
            assert set(by_purpose) == set(purposes)
            account_id = AccountService.default_account_id(owner)
            assert {row["account_id"] for row, _payload in by_purpose.values()} == {account_id}
            for purpose, expected_ciphertext in purposes.items():
                row, payload = by_purpose[purpose]
                assert payload["encrypted_refresh_token"] == expected_ciphertext
                assert decrypt_secret(payload["encrypted_refresh_token"]) == (
                    f"virtual-{owner}-{purpose}-token"
                )

        for owner, links in expected["instances"].items():
            account_id = AccountService.default_account_id(owner)
            instance_id = connection.execute(
                text("SELECT instance_id FROM gpu_leases WHERE id=:lease"), {"lease": links["lease_id"]}
            ).scalar_one()
            assert connection.execute(
                text("SELECT account_id FROM gpu_leases WHERE id=:lease"), {"lease": links["lease_id"]}
            ).scalar_one() == account_id
            assert connection.execute(
                text("SELECT account_id, instance_id FROM gpu_jobs WHERE id=:job"), {"job": links["job_id"]}
            ).mappings().one() == {"account_id": account_id, "instance_id": instance_id}
            assert connection.execute(
                text("SELECT account_id, instance_id FROM gpu_executions WHERE id=:execution"),
                {"execution": links["execution_id"]},
            ).mappings().one() == {"account_id": account_id, "instance_id": instance_id}


def test_owner_scoped_account_list_reports_colab_drive_connection_state(tmp_path, monkeypatch):
    _prepare_virtual_encryption(monkeypatch)
    store, _ = _open_legacy_store(tmp_path)
    _run_0003(store)
    repo = Repository(store)

    owner_a = AccountService(repo, "owner-a").list()
    owner_b = AccountService(repo, "owner-b").list()
    assert [row["account_id"] for row in owner_a] == [AccountService.default_account_id("owner-a")]
    assert [row["account_id"] for row in owner_b] == [AccountService.default_account_id("owner-b")]
    assert owner_a[0]["authorization"]["colab"]["connected"] is True
    assert owner_a[0]["authorization"]["drive"]["connected"] is True
    assert owner_b[0]["authorization"]["colab"]["connected"] is True
    assert owner_b[0]["authorization"]["drive"]["connected"] is False
    assert "virtual-owner-a-colab-token" not in json.dumps(owner_a)
    assert repo.get("jobs", "owner-b-job", "owner-a") is None
    assert {row["owner_id"] for row in repo.list("credentials", "owner-a")} == {"owner-a"}


def test_already_upgraded_0003_startup_is_idempotent_and_does_not_duplicate_defaults(tmp_path, monkeypatch):
    _prepare_virtual_encryption(monkeypatch)
    store, _ = _open_legacy_store(tmp_path)
    _run_0003(store)
    before = {}
    with store.engine.connect() as connection:
        for table in ("gpu_accounts", "gpu_instances", "gpu_credentials", "gpu_jobs", "gpu_leases"):
            before[table] = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()

    Repository(store)
    Repository(store)
    AccountService(Repository(store), "owner-a").list()

    with store.engine.connect() as connection:
        after = {
            table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in before
        }
    assert after == before


def test_repository_projection_exposes_typed_0003_identity_fields_after_restart(tmp_path, monkeypatch):
    """RED: SQL columns are populated, but Repository._row drops them from API rows."""
    _prepare_virtual_encryption(monkeypatch)
    store, expected = _open_legacy_store(tmp_path)
    _run_0003(store)
    repo = Repository(store)
    account_id = AccountService.default_account_id("owner-a")

    with store.engine.connect() as connection:
        instance_id = connection.execute(
            text("SELECT instance_id FROM gpu_leases WHERE id='owner-a-lease'")
        ).scalar_one()
    job = repo.get("jobs", "owner-a-job", "owner-a")
    lease = repo.get("leases", "owner-a-lease", "owner-a")
    execution = repo.get("executions", "owner-a-execution", "owner-a")
    instance = repo.get("instances", instance_id, "owner-a")

    assert job["account_id"] == account_id
    assert job["instance_id"] == instance_id
    assert lease["account_id"] == account_id
    assert lease["instance_id"] == instance_id
    assert execution["account_id"] == account_id
    assert execution["instance_id"] == instance_id
    assert instance["account_id"] == account_id
    assert instance["current_job_id"] == expected["instances"]["owner-a"]["job_id"]


def test_repository_restart_does_not_materialize_legacy_identity_for_new_unbound_job(tmp_path, monkeypatch):
    """Modern rows without account bindings are not legacy migration input."""
    _prepare_virtual_encryption(monkeypatch)
    store = Store(f"sqlite:///{tmp_path / 'modern.db'}")
    repo = Repository(store)
    repo.create_job("modern-owner", {"provider": "mock", "request_body": {"kind": "modern"}}, "modern-key")

    Repository(store)

    with store.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM gpu_accounts")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM gpu_instances")).scalar_one() == 0


def test_restart_does_not_reactivate_an_instance_that_was_unbound(tmp_path, monkeypatch):
    _prepare_virtual_encryption(monkeypatch)
    store, _ = _open_legacy_store(tmp_path)
    _run_0003(store)

    with store.engine.begin() as connection:
        instance_id = connection.execute(
            text("SELECT instance_id FROM gpu_leases WHERE id='owner-a-lease'")
        ).scalar_one()
        connection.execute(
            text("UPDATE gpu_instances SET current_job_id=NULL, data=:data WHERE id=:id"),
            {"id": instance_id, "data": json.dumps({"legacy_lease_id": "owner-a-lease"})},
        )

    Repository(store)

    with store.engine.connect() as connection:
        row = connection.execute(
            text("SELECT current_job_id, data FROM gpu_instances WHERE id=:id"), {"id": instance_id}
        ).mappings().one()
        assert row["current_job_id"] is None
        assert "current_job_id" not in json.loads(row["data"])


def test_duplicate_legacy_job_leases_share_one_instance_without_unique_violation(tmp_path, monkeypatch):
    _prepare_virtual_encryption(monkeypatch)
    store, _ = _open_legacy_store(tmp_path)
    _run_0003(store)

    with store.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO gpu_leases "
                "(id, owner_id, project_id, state, version, data, created_at, updated_at, account_id, instance_id) "
                "VALUES ('owner-a-lease-duplicate', 'owner-a', NULL, 'active', 0, :data, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL)"
            ),
            {"data": json.dumps({"owner_job_id": "owner-a-job"})},
        )

    Repository(store)

    with store.engine.connect() as connection:
        original_instance = connection.execute(
            text("SELECT instance_id FROM gpu_leases WHERE id='owner-a-lease'")
        ).scalar_one()
        duplicate_instance = connection.execute(
            text("SELECT instance_id FROM gpu_leases WHERE id='owner-a-lease-duplicate'")
        ).scalar_one()
        assert duplicate_instance == original_instance
        assert connection.execute(text("SELECT COUNT(*) FROM gpu_instances")).scalar_one() == 2
        assert connection.execute(
            text("SELECT COUNT(*) FROM gpu_instances WHERE owner_id='owner-a' AND current_job_id='owner-a-job'")
        ).scalar_one() == 1


@pytest.mark.parametrize(
    ("table", "row_id", "field", "wrong_value"),
    [
        ("gpu_credentials", "owner-a-cred-colab", "account_id", AccountService.default_account_id("owner-b")),
        ("gpu_jobs", "owner-a-job", "instance_id", None),
        ("gpu_instances", None, "current_job_id", "owner-b-job"),
    ],
)
def test_typed_json_drift_rejects_cross_owner_identity_and_rolls_back(
    tmp_path, monkeypatch, table, row_id, field, wrong_value
):
    """Typed identity columns must not synchronize a cross-owner JSON binding."""
    _prepare_virtual_encryption(monkeypatch)
    store, _ = _open_legacy_store(tmp_path)
    _run_0003(store)

    with store.engine.begin() as connection:
        if field == "instance_id":
            wrong_value = connection.execute(
                text("SELECT instance_id FROM gpu_leases WHERE id='owner-b-lease'")
            ).scalar_one()
        if field == "current_job_id":
            row_id = connection.execute(
                text("SELECT id FROM gpu_instances WHERE owner_id='owner-a'")
            ).scalar_one()
        row = connection.execute(
            text(f"SELECT {field}, data FROM {table} WHERE id=:id"), {"id": row_id}
        ).mappings().one()
        typed_before = row[field]
        payload = json.loads(row["data"])
        payload[field] = wrong_value
        connection.execute(
            text(f"UPDATE {table} SET data=:data WHERE id=:id"),
            {"id": row_id, "data": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        )

    with pytest.raises(ValueError, match="owner mismatch"):
        with store.engine.begin() as connection:
            upgrade_legacy_identity(connection)

    with store.engine.connect() as connection:
        row = connection.execute(
            text(f"SELECT {field}, data FROM {table} WHERE id=:id"), {"id": row_id}
        ).mappings().one()
        assert row[field] == typed_before
        assert json.loads(row["data"])[field] == wrong_value
