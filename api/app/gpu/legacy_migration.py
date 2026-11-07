"""Idempotent, local repair for pre-account GPU databases.

The repair is deliberately transport-free.  It only repairs missing identity
bindings and typed/JSON drift; it never exchanges credentials or copies token
contents into account/instance rows.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import inspect, text

_LEGACY_NAMESPACE = uuid.NAMESPACE_URL
_LEGACY_ACCOUNT_PREFIX = "colab-gpu-studio:v0.2.0:legacy-account:"
_LEGACY_INSTANCE_PREFIX = "colab-gpu-studio:v0.2.0:legacy-instance:"
_GOOGLE_PURPOSES = {"colab", "drive"}
_IDENTITY_FIELDS = {
    "gpu_credentials": ("account_id",),
    "gpu_jobs": ("account_id", "instance_id"),
    "gpu_executions": ("account_id", "instance_id"),
    "gpu_leases": ("account_id", "instance_id"),
    "gpu_instances": ("account_id", "current_job_id"),
}
_UNSET = object()


def legacy_account_id(owner_id: str) -> str:
    return str(uuid.uuid5(_LEGACY_NAMESPACE, _LEGACY_ACCOUNT_PREFIX + owner_id))


def legacy_instance_id(owner_id: str, lease_id: str) -> str:
    return str(uuid.uuid5(_LEGACY_NAMESPACE, f"{_LEGACY_INSTANCE_PREFIX}{owner_id}:{lease_id}"))


def _json(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _rows(bind, table: str) -> list[dict]:
    if table not in _tables(bind):
        return []
    return [dict(row) for row in bind.execute(text(f"SELECT * FROM {table}")).mappings()]


def _identity_from_row(row: dict, field: str) -> str | None:
    typed = row.get(field, _UNSET)
    if typed not in (_UNSET, None, ""):
        return str(typed)
    value = _json(row.get("data")).get(field)
    return str(value) if value not in (None, "") else None


def _owner_account(bind, account_id: str | None, owner_id: str | None) -> str | None:
    if not account_id or not owner_id or "gpu_accounts" not in _tables(bind):
        return None
    row = bind.execute(
        text("SELECT id, owner_id FROM gpu_accounts WHERE id=:id"), {"id": account_id}
    ).mappings().first()
    if row is None:
        return None
    if row["owner_id"] != owner_id:
        raise ValueError("legacy account owner mismatch")
    return str(row["id"])


def _owner_instance(bind, instance_id: str | None, owner_id: str | None) -> str | None:
    if not instance_id or not owner_id or "gpu_instances" not in _tables(bind):
        return None
    row = bind.execute(
        text("SELECT id, owner_id FROM gpu_instances WHERE id=:id"), {"id": instance_id}
    ).mappings().first()
    if row is None:
        return None
    if row["owner_id"] != owner_id:
        raise ValueError("legacy instance owner mismatch")
    return str(row["id"])


def _legacy_lease(row: dict) -> bool:
    """Recognize the old lease shape, not a freshly-created modern lease."""
    payload = _json(row.get("data"))
    return bool(
        (payload.get("owner_job_id") or payload.get("current_job_id"))
        and not payload.get("request_id")
        and payload.get("created_epoch") is None
    )


def _legacy_owner_needs_default(bind) -> dict[str, bool]:
    needs: dict[str, bool] = defaultdict(bool)
    for row in _rows(bind, "gpu_credentials"):
        payload = _json(row.get("data"))
        if (
            row.get("owner_id") not in (None, "")
            and payload.get("provider", "google") == "google"
            and payload.get("purpose") in _GOOGLE_PURPOSES
            and _identity_from_row(row, "account_id") is None
        ):
            needs[row["owner_id"]] = True
    for row in _rows(bind, "gpu_leases"):
        if (
            row.get("owner_id") not in (None, "")
            and _legacy_lease(row)
            and _identity_from_row(row, "account_id") is None
            and _identity_from_row(row, "instance_id") is None
        ):
            needs[row["owner_id"]] = True
    return needs


def _default_accounts(bind, legacy_rows: dict[str, bool]) -> dict[str, str]:
    if "gpu_accounts" not in _tables(bind):
        return {}
    accounts: dict[str, str] = {}
    for owner_id, needed in sorted(legacy_rows.items()):
        if not needed or owner_id in (None, ""):
            continue
        account_id = legacy_account_id(owner_id)
        payload = json.dumps(
            {
                "account_id": account_id,
                "alias": "Google账号",
                "enabled": True,
                "allow_allocation": False,
                "projects": [],
                "workflows": [],
                "budget": {},
                "strategy": "manual",
                "legacy_default": True,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        # This is valid on both supported server dialects.  Do not catch an
        # IntegrityError: on PostgreSQL that would leave the outer transaction
        # aborted and make subsequent repair statements unsafe.
        bind.execute(
            text(
                "INSERT INTO gpu_accounts "
                "(id, owner_id, project_id, state, version, data, created_at, updated_at) "
                "VALUES (:id, :owner_id, NULL, 'connected', 0, :data, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": account_id, "owner_id": owner_id, "data": payload},
        )
        actual = bind.execute(
            text("SELECT id, owner_id FROM gpu_accounts WHERE id=:id"), {"id": account_id}
        ).mappings().one()
        if actual["owner_id"] != owner_id:
            raise ValueError("legacy default account owner mismatch")
        accounts[owner_id] = account_id
    return accounts


def _sync_row(bind, table: str, row: dict, **values: object) -> dict:
    """Synchronize only changed identity fields; never rewrite modern rows."""
    columns = _columns(bind, table)
    original_payload = _json(row.get("data"))
    payload = dict(original_payload)
    updates: dict[str, object] = {}
    for field, value in values.items():
        if field not in columns or value is _UNSET:
            continue
        _validate_identity_owner(bind, field, value, row.get("owner_id"))
        if value is None:
            if row.get(field, _UNSET) not in (_UNSET, None):
                updates[field] = None
            if field in payload:
                payload.pop(field)
        else:
            text_value = str(value)
            if row.get(field, _UNSET) != text_value:
                updates[field] = text_value
            if payload.get(field) != text_value:
                payload[field] = text_value
    if "data" in columns and payload != original_payload:
        updates["data"] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if not updates:
        return row
    assignments = ", ".join(f"{field}=:{field}" for field in updates)
    bind.execute(
        text(f"UPDATE {table} SET {assignments} WHERE id=:__id"),
        {**updates, "__id": row["id"]},
    )
    row.update(updates)
    row["data"] = updates.get("data", row.get("data"))
    return row


def _same_owner_job(bind, owner_id: str, job_id: str | None) -> bool:
    if not job_id or "gpu_jobs" not in _tables(bind):
        return False
    return (
        bind.execute(
            text("SELECT id FROM gpu_jobs WHERE id=:id AND owner_id=:owner"),
            {"id": job_id, "owner": owner_id},
        ).first()
        is not None
    )


def _instance_for_job(bind, owner_id: str, job_id: str) -> str | None:
    row = bind.execute(
        text(
            "SELECT id, owner_id FROM gpu_instances "
            "WHERE owner_id=:owner AND current_job_id=:job LIMIT 1"
        ),
        {"owner": owner_id, "job": job_id},
    ).mappings().first()
    if row is None:
        return None
    if row["owner_id"] != owner_id:
        raise ValueError("legacy instance owner mismatch")
    return str(row["id"])


def _owner_job(bind, job_id: str | None, owner_id: str | None) -> str | None:
    if not job_id or not owner_id or "gpu_jobs" not in _tables(bind):
        return None
    row = bind.execute(
        text("SELECT id, owner_id FROM gpu_jobs WHERE id=:id"), {"id": job_id}
    ).mappings().first()
    if row is None:
        return None
    if row["owner_id"] != owner_id:
        raise ValueError("legacy job owner mismatch")
    return str(row["id"])


def _validate_identity_owner(bind, field: str, value: object, owner_id: str | None) -> None:
    if value in (None, "") or owner_id in (None, ""):
        return
    validators = {
        "account_id": _owner_account,
        "instance_id": _owner_instance,
        "current_job_id": _owner_job,
    }
    validators[field](bind, str(value), owner_id)


def _repair_typed_json_drift(bind, table: str) -> None:
    for row in _rows(bind, table):
        values: dict[str, object] = {}
        payload = _json(row.get("data"))
        for field in _IDENTITY_FIELDS[table]:
            if field not in row:
                continue
            typed = row.get(field)
            json_value = payload.get(field)
            _validate_identity_owner(bind, field, typed, row.get("owner_id"))
            _validate_identity_owner(bind, field, json_value, row.get("owner_id"))
            if typed not in (None, ""):
                if str(json_value) != str(typed):
                    values[field] = typed
            elif json_value not in (None, ""):
                # A NULL current_job_id is an explicit unbind.  For the other
                # identity columns the old JSON value is the only binding.
                values[field] = None if field == "current_job_id" else json_value
        if values:
            _sync_row(bind, table, row, **values)


def repair_legacy_identity(bind) -> None:
    """Backfill only legacy/missing bindings and keep ownership isolated."""
    if "gpu_accounts" not in _tables(bind):
        return

    accounts = _default_accounts(bind, _legacy_owner_needs_default(bind))
    # Validate both representations before any legacy pass can overwrite JSON
    # with a typed value from the same row.
    for table in ("gpu_credentials", "gpu_jobs", "gpu_executions", "gpu_leases", "gpu_instances"):
        _repair_typed_json_drift(bind, table)
    credential_rows = _rows(bind, "gpu_credentials")
    job_rows = _rows(bind, "gpu_jobs")
    lease_rows = _rows(bind, "gpu_leases")
    execution_rows = _rows(bind, "gpu_executions")
    jobs_by_owner: dict[str, set[str]] = defaultdict(set)
    for row in job_rows:
        if row.get("owner_id") not in (None, ""):
            jobs_by_owner[row["owner_id"]].add(row["id"])

    for row in credential_rows:
        payload = _json(row.get("data"))
        if payload.get("provider", "google") != "google" or payload.get("purpose") not in _GOOGLE_PURPOSES:
            continue
        owner = row.get("owner_id")
        raw_account = _identity_from_row(row, "account_id")
        account_id = _owner_account(bind, raw_account, owner) or accounts.get(owner)
        if account_id:
            _sync_row(bind, "gpu_credentials", row, account_id=account_id)

    lease_to_instance: dict[tuple[str, str], str] = {}
    lease_account: dict[tuple[str, str], str | None] = {}
    job_to_instance: dict[tuple[str, str], str] = {}

    for row in lease_rows:
        owner = row.get("owner_id")
        lease_id = row["id"]
        if owner in (None, ""):
            continue
        payload = _json(row.get("data"))
        legacy = _legacy_lease(row)
        raw_account = _identity_from_row(row, "account_id")
        raw_instance = _identity_from_row(row, "instance_id")
        account_id = _owner_account(bind, raw_account, owner) if raw_account else accounts.get(owner)
        legacy_job_id = payload.get("owner_job_id") or payload.get("current_job_id")

        # A new lease is intentionally left alone until InstanceService binds it.
        if not legacy and raw_account is None and raw_instance is None:
            continue
        if account_id is None and not legacy:
            continue

        instance_id = _owner_instance(bind, raw_instance, owner) if raw_instance else None
        if instance_id is None and legacy:
            if legacy_job_id and _same_owner_job(bind, owner, legacy_job_id):
                instance_id = job_to_instance.get((owner, legacy_job_id)) or _instance_for_job(bind, owner, legacy_job_id)
            if instance_id is None:
                instance_id = legacy_instance_id(owner, lease_id)

        existing = None
        if instance_id:
            existing = bind.execute(
                text(
                    "SELECT id, owner_id, state, data, account_id, current_job_id "
                    "FROM gpu_instances WHERE id=:id"
                ),
                {"id": instance_id},
            ).mappings().first()
            if existing is not None and existing["owner_id"] != owner:
                raise ValueError("legacy instance owner mismatch")

        current_job_id = None
        if existing is not None:
            # The typed NULL is authoritative for an already-upgraded instance:
            # an explicit unbind must never be reconstructed from stale lease JSON.
            current_job_id = existing["current_job_id"]
            existing_account = _identity_from_row(dict(existing), "account_id")
            if existing_account:
                account_id = _owner_account(bind, existing_account, owner) or account_id
            instance_values: dict[str, object] = {}
            if account_id:
                instance_values["account_id"] = account_id
            if current_job_id is not None:
                instance_values["current_job_id"] = current_job_id
            if instance_values:
                _sync_row(bind, "gpu_instances", dict(existing), **instance_values)
        elif instance_id is not None and legacy:
            if legacy_job_id and _same_owner_job(bind, owner, legacy_job_id):
                current_job_id = None if row.get("state") == "deleted" else legacy_job_id
            if current_job_id:
                occupied = _instance_for_job(bind, owner, current_job_id)
                if occupied and occupied != instance_id:
                    instance_id = occupied
                    existing = bind.execute(
                        text(
                            "SELECT id, owner_id, state, data, account_id, current_job_id "
                            "FROM gpu_instances WHERE id=:id"
                        ),
                        {"id": instance_id},
                    ).mappings().one()
                else:
                    existing = None
            if existing is None:
                safe_data = {
                    "legacy_lease_id": lease_id,
                    "legacy_owner_job_id": legacy_job_id,
                }
                if account_id:
                    safe_data["account_id"] = account_id
                if current_job_id:
                    safe_data["current_job_id"] = current_job_id
                bind.execute(
                    text(
                        "INSERT INTO gpu_instances "
                        "(id, owner_id, project_id, state, version, data, created_at, updated_at, account_id, current_job_id) "
                        "VALUES (:id, :owner_id, NULL, :state, 0, :data, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :account_id, :current_job_id)"
                    ),
                    {
                        "id": instance_id,
                        "owner_id": owner,
                        "state": "deleted" if row.get("state") == "deleted" else "unknown",
                        "data": json.dumps(safe_data, ensure_ascii=False, separators=(",", ":")),
                        "account_id": account_id,
                        "current_job_id": current_job_id,
                    },
                )

        if account_id and instance_id:
            _sync_row(bind, "gpu_leases", row, account_id=account_id, instance_id=instance_id)
            lease_to_instance[(owner, lease_id)] = instance_id
            lease_account[(owner, lease_id)] = account_id
            if legacy_job_id and _same_owner_job(bind, owner, legacy_job_id):
                job_to_instance.setdefault((owner, legacy_job_id), instance_id)

    for row in job_rows:
        owner = row.get("owner_id")
        if owner in (None, ""):
            continue
        payload = _json(row.get("data"))
        lease_key = (owner, payload.get("lease_id")) if payload.get("lease_id") else None
        raw_instance = _identity_from_row(row, "instance_id")
        instance_id = _owner_instance(bind, raw_instance, owner) if raw_instance else None
        if instance_id is None and lease_key:
            instance_id = lease_to_instance.get(lease_key)
        if instance_id is None:
            instance_id = job_to_instance.get((owner, row["id"]))
        raw_account = _identity_from_row(row, "account_id")
        account_id = _owner_account(bind, raw_account, owner) if raw_account else None
        if account_id is None and lease_key:
            account_id = lease_account.get(lease_key)
        if account_id is None:
            account_id = accounts.get(owner)
        values = {}
        if account_id:
            values["account_id"] = account_id
        if instance_id:
            values["instance_id"] = instance_id
        if values:
            _sync_row(bind, "gpu_jobs", row, **values)

    for row in execution_rows:
        owner = row.get("owner_id")
        if owner in (None, ""):
            continue
        payload = _json(row.get("data"))
        lease_key = (owner, payload.get("lease_id")) if payload.get("lease_id") else None
        raw_instance = _identity_from_row(row, "instance_id")
        instance_id = _owner_instance(bind, raw_instance, owner) if raw_instance else None
        if instance_id is None and lease_key:
            instance_id = lease_to_instance.get(lease_key)
        if instance_id is None and payload.get("job_id"):
            instance_id = job_to_instance.get((owner, payload["job_id"]))
        raw_account = _identity_from_row(row, "account_id")
        account_id = _owner_account(bind, raw_account, owner) if raw_account else None
        if account_id is None and lease_key:
            account_id = lease_account.get(lease_key)
        if account_id is None:
            account_id = accounts.get(owner)
        values = {}
        if account_id:
            values["account_id"] = account_id
        if instance_id:
            values["instance_id"] = instance_id
        if values:
            _sync_row(bind, "gpu_executions", row, **values)

def upgrade_legacy_identity(bind) -> None:
    repair_legacy_identity(bind)
