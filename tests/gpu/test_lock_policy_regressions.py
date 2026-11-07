from __future__ import annotations

import threading

from app.db import Store
from app.gpu.repository import Repository
from app.job_schemas import JobInput, Policy


def test_admission_lock_does_not_wait_on_dispatch_snapshot(tmp_path):
    """A scheduler/watchdog snapshot must not starve HTTP admission."""
    store = Store(f"sqlite:///{tmp_path / 'lock.db'}")
    held = threading.Event()
    release = threading.Event()

    def hold_dispatch_lock():
        with store.dispatch_lock() as acquired:
            assert acquired
            held.set()
            release.wait(2)

    worker = threading.Thread(target=hold_dispatch_lock)
    worker.start()
    assert held.wait(2)
    try:
        with store.admission_lock(timeout=0) as acquired:
            assert acquired
            repo = Repository(store)
            job, created = repo.create_job(
                "owner",
                {
                    "project_id": "project",
                    "workflow_key": "workflow",
                    "workflow_version": 1,
                    "model_id": "model",
                    "execution_policy": {"provider": "mock"},
                },
                "admission-under-snapshot",
            )
            assert created
            assert repo.get("jobs", job["id"], "owner")["state"] == "queued"
    finally:
        release.set()
        worker.join(2)


def test_admission_lock_still_serializes_admissions(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'lock.db'}")
    held = threading.Event()
    release = threading.Event()

    def hold_admission_lock():
        with store.admission_lock() as acquired:
            assert acquired
            held.set()
            release.wait(2)

    worker = threading.Thread(target=hold_admission_lock)
    worker.start()
    assert held.wait(2)
    try:
        with store.admission_lock(timeout=0) as acquired:
            assert not acquired
    finally:
        release.set()
        worker.join(2)


def test_explicit_lifecycle_survives_policy_model_dump_round_trip():
    policy = Policy(lifecycle="retain")

    dumped = policy.model_dump()
    assert dumped["lifecycle"] == "retain"
    assert "shutdown_after_job" not in dumped
    assert "idle_grace_minutes" not in dumped
    assert Policy.model_validate(dumped).lifecycle == "retain"


def test_job_input_policy_round_trip_does_not_invent_legacy_lifecycle_fields():
    job = JobInput(
        project_id="project",
        model_id="model",
        workflow_key="workflow",
        workflow_version=1,
        execution_policy={"provider": "mock", "lifecycle": "retain"},
    )

    round_tripped = JobInput.model_validate(job.model_dump())
    assert round_tripped.execution_policy.lifecycle == "retain"
