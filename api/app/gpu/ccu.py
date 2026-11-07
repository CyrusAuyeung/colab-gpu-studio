"""Compute-unit reconciliation against Google's live balance.

Google exposes no per-job billing ledger: the only authoritative number is the
account balance from ``GET /tun/m/ccu-info`` (CLI tunnel accounts) or the REST
subscription payload (Web-OAuth accounts).  Per-job cost therefore has to be
measured as a before/after balance delta around the job, and only attributed
when the account was not serving another job at the same time.

Everything here is fail-soft: a failed sample must never fail a job.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

STATE_FILE = "ccu_state.json"
MAX_ANOMALIES = 20
ACTIVE_LEASE_STATES = frozenset(
    {"creating", "active", "busy", "idle", "cleanup_pending", "quarantined", "releasing"}
)
TERMINAL_JOB_STATES = frozenset({"completed", "failed", "cancelled", "expired"})

_BALANCE_KEYS = (
    "paidComputeUnitsBalance",
    "paid_compute_units_balance",
    "currentBalance",
    "current_balance",
    "computeUnitsBalance",
    "balance",
)
_RATE_KEYS = ("consumptionRateHourly", "consumption_rate_hourly", "rateHourly", "usageRateHourly")
_ASSIGNMENT_KEYS = ("assignmentsCount", "assignments_count", "activeAssignments", "active_assignments")


def _number(payload: Any, keys: tuple[str, ...], depth: int = 0) -> float | None:
    """Find the first numeric value for any key, peeking one level into dicts."""
    if not isinstance(payload, dict) or depth > 2:
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    for value in payload.values():
        if isinstance(value, dict):
            found = _number(value, keys, depth + 1)
            if found is not None:
                return found
    return None


def normalize_subscription(payload: Any) -> dict[str, Any]:
    """Normalize tunnel and REST subscription payloads to one shape."""
    balance = _number(payload, _BALANCE_KEYS)
    rate = _number(payload, _RATE_KEYS)
    assignments = _number(payload, _ASSIGNMENT_KEYS)
    return {
        "balance": balance,
        "rate": rate,
        "assignments": int(assignments) if assignments is not None else None,
    }


def sampling_enabled() -> bool:
    if os.getenv("GPU_CCU_SAMPLING", "1") != "1":
        return False
    # Tests must never reach Google: the sampler is monkeypatched when exercised.
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    if os.getenv("STUDIO_OFFLINE_TEST") == "1":
        return False
    return True


def sample_account_balance(repo, owner_id: str, account_id: str | None, *, timeout: float = 8.0) -> dict | None:
    """Read one account's live compute-unit balance. Returns None on any failure."""
    if not account_id or not owner_id:
        return None
    provider = None
    try:
        from .colab import GoogleColabProvider

        provider = GoogleColabProvider(repo, owner_id, account_id=account_id, timeout=timeout)
        payload = provider.subscription()
        sample = normalize_subscription(payload)
        if sample["balance"] is None:
            return None
        sample["at"] = time.time()
        return sample
    except Exception as exc:  # noqa: BLE001 - sampling is best-effort by contract
        log.warning("ccu sample failed for account %s: %s", account_id[:8], type(exc).__name__)
        return None
    finally:
        if provider is not None:
            try:
                provider.close()
            except Exception:  # noqa: BLE001
                pass


def maybe_sample_balance(repo, owner_id: str, account_id: str | None) -> dict | None:
    """Sampler used by admission/cleanup; honours the environment guards."""
    if not sampling_enabled():
        return None
    return sample_account_balance(repo, owner_id, account_id)


def active_lease_count(repo, owner_id: str, account_id: str | None, *, exclude_lease_id: str | None = None) -> int:
    if not account_id:
        return 0
    total = 0
    for lease in repo.list("leases", owner_id):
        if lease.get("account_id") != account_id:
            continue
        if lease.get("state") not in ACTIVE_LEASE_STATES:
            continue
        if exclude_lease_id and lease.get("id") == exclude_lease_id:
            continue
        total += 1
    return total


def active_job_count(repo, owner_id: str, account_id: str | None) -> int:
    if not account_id:
        return 0
    total = 0
    for job in repo.list("jobs", owner_id):
        if job.get("account_id") != account_id:
            continue
        if job.get("state") in TERMINAL_JOB_STATES:
            continue
        total += 1
    return total


def reconcile(before: Any, after: Any) -> float | None:
    """Measured spend = before - after, never negative, never invented."""
    try:
        delta = float(before) - float(after)
    except (TypeError, ValueError):
        return None
    if delta < 0:
        return None
    return round(delta, 6)


def _state_path() -> Path:
    return Path(settings.data_dir) / STATE_FILE


def read_state() -> dict[str, Any]:
    path = _state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"snapshots": {}, "anomalies": [], "updated_at": None}
        payload.setdefault("snapshots", {})
        payload.setdefault("anomalies", [])
        return payload
    except (OSError, ValueError):
        return {"snapshots": {}, "anomalies": [], "updated_at": None}


def _write_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _detect(snapshots: dict[str, dict], previous: list[dict], now: float) -> list[dict]:
    """An assignment with no job behind it is a leaked runtime, i.e. money on fire."""
    known = {item.get("account_id"): item for item in previous if isinstance(item, dict)}
    anomalies: list[dict] = []
    for account_id, snapshot in snapshots.items():
        assignments = snapshot.get("assignments") or 0
        leaked = (
            assignments > 0
            and not snapshot.get("active_jobs")
            and not snapshot.get("active_leases")
        )
        if not leaked:
            continue
        previous_item = known.get(account_id) or {}
        anomalies.append(
            {
                "account_id": account_id,
                "alias": snapshot.get("alias"),
                "assignments": assignments,
                "balance": snapshot.get("balance"),
                "rate": snapshot.get("rate"),
                "first_seen": previous_item.get("first_seen") or now,
                "last_seen": now,
                "resolved_at": None,
            }
        )
    active_accounts = {item["account_id"] for item in anomalies}
    for account_id, previous_item in known.items():
        if account_id in active_accounts or previous_item.get("resolved_at"):
            continue
        anomalies.append({**previous_item, "resolved_at": now})
    anomalies.sort(key=lambda item: item.get("last_seen") or 0, reverse=True)
    return anomalies[:MAX_ANOMALIES]


def tick_ccu(repo, owner_id: str, *, sampler=None, now: float | None = None) -> dict[str, Any]:
    """Refresh per-account balance snapshots and idle-runtime anomalies."""
    sample = sampler or maybe_sample_balance
    moment = time.time() if now is None else now
    state = read_state()
    previous = state.get("anomalies") or []
    snapshots: dict[str, dict] = {}

    for account in repo.list("accounts", owner_id):
        account_id = account.get("account_id")
        if not account_id or account.get("enabled") is False:
            continue
        if account.get("provider", "colab") != "colab":
            continue
        sample_value = sample(repo, owner_id, account_id)
        snapshot = {
            "alias": account.get("alias"),
            "balance": (sample_value or {}).get("balance"),
            "rate": (sample_value or {}).get("rate"),
            "assignments": (sample_value or {}).get("assignments"),
            "sampled_at": (sample_value or {}).get("at"),
            "active_jobs": active_job_count(repo, owner_id, account_id),
            "active_leases": active_lease_count(repo, owner_id, account_id),
        }
        if sample_value is None:
            snapshot["error"] = "sample_failed"
        snapshots[account_id] = snapshot

    anomalies = _detect(snapshots, previous, moment)
    new_state = {"snapshots": snapshots, "anomalies": anomalies, "updated_at": moment}
    try:
        _write_state(new_state)
    except OSError as exc:
        log.warning("ccu state write failed: %s", type(exc).__name__)
    return new_state


def summarize(snapshots: dict[str, dict]) -> dict[str, Any]:
    """Totals across accounts for the usage endpoint."""
    balances = [s["balance"] for s in snapshots.values() if isinstance(s.get("balance"), (int, float))]
    assignments = sum(int(s.get("assignments") or 0) for s in snapshots.values())
    return {
        "accounts": len(snapshots),
        "total_balance": round(sum(balances), 6) if balances else None,
        "assignments": assignments,
    }
