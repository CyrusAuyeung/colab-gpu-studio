"""Register v0.2.0 account/instance entities and migrate legacy GPU rows.

The migration only reads legacy JSON to identify ownership links.  It never
 decrypts credentials and deliberately copies no token or handle into the new
 account/instance records.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict

from alembic import op
from sqlalchemy import Column, String, inspect, text

from app.gpu.repository import (
    GpuAccountRow,
    GpuAllocationCursorRow,
    GpuInstanceRow,
    GpuInstanceUsageSegmentRow,
    GpuOperationRow,
)

revision = "0003_accounts_instances"
down_revision = "0002_batch_execution"
branch_labels = None
depends_on = None

_LEGACY_NAMESPACE = uuid.NAMESPACE_URL


def legacy_account_id(owner_id: str) -> str:
    """Return the stable default account identity for one historical owner."""
    return str(uuid.uuid5(_LEGACY_NAMESPACE, f"colab-gpu-studio:v0.2.0:legacy-account:{owner_id}"))


def legacy_instance_id(owner_id: str, lease_id: str) -> str:
    """Return the stable instance shell identity for one historical lease."""
    return str(uuid.uuid5(_LEGACY_NAMESPACE, f"colab-gpu-studio:v0.2.0:legacy-instance:{owner_id}:{lease_id}"))


def _json(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tables(bind):
    return set(inspect(bind).get_table_names())


def _add_columns(bind) -> None:
    existing_tables = _tables(bind)
    specs = {
        "gpu_jobs": ("account_id", "instance_id"),
        "gpu_executions": ("account_id", "instance_id"),
        "gpu_leases": ("account_id", "instance_id"),
        "gpu_credentials": ("account_id",),
    }
    for table, columns in specs.items():
        if table not in existing_tables:
            continue
        existing = {column["name"] for column in inspect(bind).get_columns(table)}
        for column in columns:
            if column not in existing:
                op.add_column(table, Column(column, String(64), nullable=True))


def _ensure_indexes(bind) -> None:
    for table, columns in {
        "gpu_jobs": ("account_id", "instance_id"),
        "gpu_executions": ("account_id", "instance_id"),
        "gpu_leases": ("account_id", "instance_id"),
        "gpu_credentials": ("account_id",),
    }.items():
        if table not in _tables(bind):
            continue
        indexes = {item["name"] for item in inspect(bind).get_indexes(table)}
        for column in columns:
            name = f"ix_{table}_{column}_v020"
            if name not in indexes:
                op.create_index(name, table, [column])


def _insert_if_missing(bind, table: str, values: dict) -> None:
    row = bind.execute(
        text(f"SELECT id FROM {table} WHERE id=:id"), {"id": values["id"]}
    ).first()
    if row is None:
        bind.execute(
            text(
                f"INSERT INTO {table} "
                "(id, owner_id, project_id, state, version, data, created_at, updated_at) "
                "VALUES (:id, :owner_id, :project_id, :state, 0, :data, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            values,
        )


def _migrate_legacy_credentials(bind) -> dict[str, str]:
    if "gpu_credentials" not in _tables(bind):
        return {}
    by_owner: set[str] = set()
    rows = bind.execute(text("SELECT id, owner_id, data FROM gpu_credentials")).mappings()
    credential_rows = list(rows)
    for row in credential_rows:
        payload = _json(row["data"])
        if payload.get("provider", "google") != "google":
            continue
        if payload.get("purpose") not in {"colab", "drive"}:
            continue
        if row["owner_id"] is not None:
            by_owner.add(row["owner_id"])
    account_by_owner = {owner: legacy_account_id(owner) for owner in by_owner}
    for owner, account_id in account_by_owner.items():
        _insert_if_missing(
            bind,
            "gpu_accounts",
            {
                "id": account_id,
                "owner_id": owner,
                "project_id": None,
                "state": "connected",
                "data": json.dumps(
                    {
                        "alias": "Google账号",
                        "status": "connected",
                        "legacy": True,
                    },
                    ensure_ascii=False,
                ),
            },
        )
    for row in credential_rows:
        payload = _json(row["data"])
        account_id = account_by_owner.get(row["owner_id"])
        if account_id and payload.get("provider", "google") == "google" and payload.get("purpose") in {
            "colab",
            "drive",
        }:
            bind.execute(
                text("UPDATE gpu_credentials SET account_id=:account_id WHERE id=:id"),
                {"account_id": account_id, "id": row["id"]},
            )
    return account_by_owner


def _migrate_legacy_leases(bind, account_by_owner: dict[str, str]):
    if "gpu_leases" not in _tables(bind):
        return {}, {}
    jobs = []
    if "gpu_jobs" in _tables(bind):
        jobs = list(bind.execute(text("SELECT id, owner_id, data FROM gpu_jobs")).mappings())
    job_ids_by_owner = defaultdict(set)
    for job in jobs:
        job_ids_by_owner[job["owner_id"]].add(job["id"])

    lease_rows = list(bind.execute(text("SELECT id, owner_id, state, data FROM gpu_leases")).mappings())
    lease_by_id = {}
    lease_by_job = {}
    instance_by_lease = {}
    for lease in lease_rows:
        payload = _json(lease["data"])
        owner = lease["owner_id"]
        instance_id = legacy_instance_id(owner, lease["id"])
        account_id = account_by_owner.get(owner)
        state = "deleted" if lease["state"] == "deleted" else "unknown"
        legacy_job_id = payload.get("owner_job_id") or payload.get("current_job_id")
        current_job_id = legacy_job_id if state != "deleted" and legacy_job_id in job_ids_by_owner[owner] else None
        safe_data = {
            "legacy_lease_id": lease["id"],
            "legacy_owner_job_id": legacy_job_id,
        }
        _insert_if_missing(
            bind,
            "gpu_instances",
            {
                "id": instance_id,
                "owner_id": owner,
                "project_id": None,
                "state": state,
                "data": json.dumps(safe_data),
            },
        )
        if current_job_id is not None:
            occupied = bind.execute(
                text(
                    "SELECT id FROM gpu_instances "
                    "WHERE owner_id=:owner_id AND current_job_id=:job_id AND id<>:instance_id"
                ),
                {"owner_id": owner, "job_id": current_job_id, "instance_id": instance_id},
            ).first()
            if occupied is None:
                bind.execute(
                    text(
                        "UPDATE gpu_instances SET current_job_id=:job_id, account_id=:account_id "
                        "WHERE id=:instance_id"
                    ),
                    {
                        "job_id": current_job_id,
                        "account_id": account_id,
                        "instance_id": instance_id,
                    },
                )
            else:
                # A malformed legacy database may associate one job with more
                # than one lease.  Keep both historical instances, but do not
                # violate the new one-job/one-instance invariant.
                bind.execute(
                    text("UPDATE gpu_instances SET account_id=:account_id WHERE id=:instance_id"),
                    {"account_id": account_id, "instance_id": instance_id},
                )
        else:
            bind.execute(
                text("UPDATE gpu_instances SET account_id=:account_id WHERE id=:instance_id"),
                {"account_id": account_id, "instance_id": instance_id},
            )
        bind.execute(
            text(
                "UPDATE gpu_leases SET account_id=:account_id, instance_id=:instance_id WHERE id=:id"
            ),
            {"account_id": account_id, "instance_id": instance_id, "id": lease["id"]},
        )
        lease_by_id[lease["id"]] = instance_id
        instance_by_lease[lease["id"]] = instance_id
        if legacy_job_id:
            lease_by_job[(owner, legacy_job_id)] = instance_id
    return lease_by_id, lease_by_job


def _migrate_legacy_jobs(bind, lease_by_id, lease_by_job):
    if "gpu_jobs" not in _tables(bind):
        return {}
    job_to_instance = {}
    for job in bind.execute(text("SELECT id, owner_id, data FROM gpu_jobs")).mappings():
        payload = _json(job["data"])
        instance_id = lease_by_id.get(payload.get("lease_id")) or lease_by_job.get((job["owner_id"], job["id"]))
        if instance_id is None:
            continue
        job_to_instance[job["id"]] = instance_id
        account_id = bind.execute(
            text("SELECT account_id FROM gpu_leases WHERE instance_id=:instance_id LIMIT 1"),
            {"instance_id": instance_id},
        ).scalar()
        bind.execute(
            text(
                "UPDATE gpu_jobs SET account_id=:account_id, instance_id=:instance_id WHERE id=:id"
            ),
            {"account_id": account_id, "instance_id": instance_id, "id": job["id"]},
        )
    if "gpu_executions" in _tables(bind):
        for execution in bind.execute(text("SELECT id, data FROM gpu_executions")).mappings():
            payload = _json(execution["data"])
            instance_id = lease_by_id.get(payload.get("lease_id")) or job_to_instance.get(payload.get("job_id"))
            if instance_id is None:
                continue
            account_id = bind.execute(
                text("SELECT account_id FROM gpu_leases WHERE instance_id=:instance_id LIMIT 1"),
                {"instance_id": instance_id},
            ).scalar()
            bind.execute(
                text(
                    "UPDATE gpu_executions SET account_id=:account_id, instance_id=:instance_id WHERE id=:id"
                ),
                {"account_id": account_id, "instance_id": instance_id, "id": execution["id"]},
            )
    return job_to_instance


def upgrade():
    bind = op.get_bind()
    _add_columns(bind)
    for model in (
        GpuAccountRow,
        GpuInstanceRow,
        GpuInstanceUsageSegmentRow,
        GpuAllocationCursorRow,
        GpuOperationRow,
    ):
        model.__table__.create(bind, checkfirst=True)
    _ensure_indexes(bind)
    account_by_owner = _migrate_legacy_credentials(bind)
    lease_by_id, lease_by_job = _migrate_legacy_leases(bind, account_by_owner)
    _migrate_legacy_jobs(bind, lease_by_id, lease_by_job)


def downgrade():
    bind = op.get_bind()
    if "gpu_instances" not in _tables(bind):
        return
    active = bind.execute(
        text("SELECT COUNT(*) FROM gpu_instances WHERE state IS NULL OR state <> 'deleted'")
    ).scalar_one()
    if active:
        raise RuntimeError("存在活跃GPU实例，必须先安全停止并确认收尾后才能降级")
    # Legacy jobs, leases and credentials are intentionally retained.  The
    # additive columns are also retained so a rollback cannot erase history.
    for table in (
        "gpu_operations",
        "gpu_allocation_cursors",
        "gpu_instance_usage_segments",
        "gpu_instances",
        "gpu_accounts",
    ):
        if table in _tables(bind):
            op.drop_table(table)
