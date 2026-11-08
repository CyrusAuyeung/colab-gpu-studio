"""Pure helpers for persisted job timing and history views.

The API intentionally keeps worker timings (runtime-side measurements) separate
from lifecycle timings derived from persisted scheduler events. Missing data is
omitted rather than converted to a misleading zero.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import datetime, timezone

_EVENT_STAGES = {
    "queue": frozenset({"queued", "validating", "waiting_for_capacity"}),
    "allocate": frozenset({"allocating"}),
    "upload": frozenset({"uploading"}),
    "cleanup": frozenset({"cleaning_up", "cleanup_pending"}),
}
_WORKER_KEYS = frozenset({
    "restore_env",
    "restore_weights",
    "warm_reuse",
    "comfy_start",
    "sampling",
    "prep_env",
    "prep_weights",
    "prep_cache",
})
_ACTIVE_STATES = frozenset({
    "queued", "validating", "waiting_for_capacity", "allocating", "preparing",
    "loading", "running", "uploading", "cleaning_up", "cleanup_pending",
})
_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
_HISTORY_BLOCKED_KEYS = frozenset({
    "owner_id",
    "storage_key",
    "handle",
    "runtime_handle",
    "connection_info",
    "connectioninfo",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "signed_url",
    "request_body",
    "snapshot",
    "api_key",
    "apikey",
    "client_secret",
    "clientsecret",
    "private_key",
    "privatekey",
    "secret_key",
    "secretkey",
    "credential",
    "credentials",
})


def _epoch(value) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _round(value: float) -> float:
    return round(value, 1)


def _event_rows(events) -> list[tuple[float, Mapping]]:
    rows = []
    for index, event in enumerate(events or []):
        if not isinstance(event, Mapping):
            continue
        stamp = _epoch(event.get("created_at", event.get("timestamp", event.get("created_epoch"))))
        state = event.get("state")
        if stamp is None or not isinstance(state, str):
            continue
        rows.append((stamp, event, index))
    rows.sort(key=lambda item: (item[0], item[2]))
    return [(stamp, event) for stamp, event, _ in rows]


def _event_durations(events) -> tuple[dict[str, float], dict[str, dict]]:
    rows = _event_rows(events)
    totals: dict[str, float] = {}
    detail: dict[str, dict] = {}
    for index, (start, event) in enumerate(rows):
        state = event.get("state")
        if index + 1 >= len(rows):
            continue
        stop = rows[index + 1][0]
        span = max(0.0, stop - start)
        for name, states in _EVENT_STAGES.items():
            if state in states:
                totals[name] = totals.get(name, 0.0) + span
                detail[name] = {
                    "seconds": _round(totals[name]),
                    "source": "events",
                    "status": "completed",
                }
    return {name: _round(seconds) for name, seconds in totals.items()}, detail


def _event_stage(state: str) -> str | None:
    for name, states in _EVENT_STAGES.items():
        if state in states:
            return name
    return None


def _active_stage_start(rows: list[tuple[float, Mapping]], state: str) -> float | None:
    """Find the beginning of the latest contiguous lifecycle stage."""
    matching = [index for index, (_, event) in enumerate(rows) if event.get("state") == state]
    if not matching:
        return None
    index = matching[-1]
    stage = _event_stage(state)
    if stage is None:
        return rows[index][0]
    while index > 0 and _event_stage(str(rows[index - 1][1].get("state") or "")) == stage:
        index -= 1
    return rows[index][0]


def job_timing_details(job: Mapping, events, *, now: float | None = None) -> dict[str, dict]:
    """Return honest timing metadata keyed by stage.

    ``source=worker`` means the runtime persisted the measurement.  ``source=events``
    means it was derived from durable scheduler transitions. An active stage is
    represented as ``status=in_progress`` with no fabricated seconds.
    """
    details: dict[str, dict] = {}
    worker = job.get("worker_timings") if isinstance(job.get("worker_timings"), Mapping) else {}
    for name, raw in worker.items():
        if name not in _WORKER_KEYS:
            continue
        seconds = _number(raw)
        if seconds is None:
            continue
        details[str(name)] = {"seconds": _round(seconds), "source": "worker", "status": "completed"}
    prep_seconds = _number(job.get("prep_seconds"))
    if prep_seconds is not None:
        details["prep_cache"] = {"seconds": _round(prep_seconds), "source": "worker", "status": "completed"}

    _, event_details = _event_durations(events)
    for name, value in event_details.items():
        details[name] = value

    state = str(job.get("state") or "").lower()
    if state not in _TERMINAL_STATES:
        current = str(job.get("stage") or "").strip()
        if current in _WORKER_KEYS and current not in details:
            details[current] = {"seconds": None, "source": "worker", "status": "in_progress"}
        event_stage = _event_stage(state)
        if event_stage:
            details[event_stage] = {"seconds": None, "source": "events", "status": "in_progress"}
        elif state in _ACTIVE_STATES and current not in _WORKER_KEYS:
            # If the scheduler has persisted a lifecycle state but the worker
            # has not supplied a finer-grained stage, expose that state rather
            # than guessing a completed duration.
            known_current = current if current in _EVENT_STAGES or current in _ACTIVE_STATES else ""
            event_stage = known_current or state
            details[event_stage] = {"seconds": None, "source": "events", "status": "in_progress"}
        if now is not None and state in _ACTIVE_STATES:
            rows = _event_rows(events)
            current_start = _active_stage_start(rows, state)
            if current_start is not None and event_stage:
                details[event_stage] = {
                    "seconds": _round(max(0.0, now - current_start)),
                    "source": "events",
                    "status": "in_progress",
                }
    return details


def job_timings(job: Mapping, events, *, now: float | None = None) -> dict[str, float]:
    """Return only completed numeric stage seconds for backward-compatible APIs."""
    details = job_timing_details(job, events, now=now)
    return {
        name: value["seconds"]
        for name, value in details.items()
        if value.get("status") == "completed" and value.get("seconds") is not None
    }


def safe_log(value, limit: int = 4000) -> str:
    text = str(value or "")[:limit]
    if re.search(r"(?:token|authorization|password|secret|cookie|api[_-]?key)", text, re.I):
        return "[REDACTED]"
    return re.sub(r"(?:https?|wss?)://[^\s\"'<>]+", "[REDACTED_URL]", text)


def safe_history_value(value):
    """Copy JSON-shaped history while dropping runtime and credential material."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key)).lower()).strip("_")
            if normalized in _HISTORY_BLOCKED_KEYS or any(part in normalized for part in ('token', 'secret', 'password', 'private_key', 'api_key')) or "url" in normalized:
                continue
            result[str(key)] = safe_history_value(item)
        return result
    if isinstance(value, list):
        return [safe_history_value(item) for item in value]
    if isinstance(value, tuple):
        return [safe_history_value(item) for item in value]
    if isinstance(value, str):
        return safe_log(value)
    return copy.deepcopy(value)
