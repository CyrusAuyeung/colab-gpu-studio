from __future__ import annotations

import threading
from pathlib import Path

import pytest
from app.db import MODELS, Base, CommonMixin, Store
from app.gpu import repository as repository_module
from app.gpu.instances import (
    AllocationError,
    BudgetError,
    InstanceConflict,
    InstanceService,
)
from app.gpu.repository import Repository


@pytest.fixture
def repo(tmp_path: Path):
    """Register the v0.2 tables locally until the repository worker lands."""
    classes = {}
    tables = {
        "accounts": "gpu_accounts_test_v020",
        "instances": "gpu_instances_test_v020",
        "instance_usage_segments": "gpu_instance_usage_segments_test_v020",
        "allocation_cursors": "gpu_allocation_cursors_test_v020",
        "operations": "gpu_operations_test_v020",
    }
    for kind, table in tables.items():
        model = repository_module._GPU_MODELS.get(kind)
        if model is None:
            model = type(
                f"V020{kind.title().replace('_', '')}Row",
                (CommonMixin, Base),
                {"__tablename__": table},
            )
            repository_module._GPU_MODELS[kind] = model
            MODELS["gpu_" + kind] = model
        classes[kind] = model
    store = Store(f"sqlite:///{tmp_path / 'instances.sqlite'}")
    Repository(store)
    Base.metadata.create_all(store.engine, tables=[m.__table__ for m in classes.values()])
    return Repository(store)


def job(owner="owner-1", job_id="job-1", provider="mock", **extra):
    data = {
        "id": job_id,
        "owner_id": owner,
        "project_id": "project-1",
        "provider": provider,
        "execution_policy": {"provider": provider},
        "compatibility_signature": "compat-v1",
        "attempt_number": 1,
        "lifecycle": "save",
        **extra,
    }
    return data


def account(repo, account_id="acct-1", **extra):
    return repo.create(
        "accounts",
        "owner-1",
        {
            "account_id": account_id,
            "alias": account_id,
            "provider": "mock",
            "connected": True,
            "enabled": True,
            "allow_allocation": True,
            **extra,
        },
        record_id=account_id,
    )


def test_mock_allocate_is_stable_across_restart_and_creates_default_account(repo):
    first = InstanceService(repo, clock=lambda: 100.0)
    allocated = first.allocate(job())
    restarted = InstanceService(repo, clock=lambda: 101.0)
    duplicate = restarted.allocate(job())

    assert allocated["id"] == duplicate["id"]
    assert allocated["account_id"].startswith("mock-")
    assert len(repo.list("instances", "owner-1")) == 1
    assert len(repo.list("accounts", "owner-1")) == 1


def test_concurrent_jobs_cannot_claim_same_instance(repo):
    account(repo)
    idle = repo.create(
        "instances",
        "owner-1",
        {
            "account_id": "acct-1",
            "provider": "mock",
            "state": "retained_idle",
            "current_job_id": None,
            "compatibility_signature": "compat-v1",
            "hard_deadline_at": 1000.0,
        },
        record_id="instance-idle",
    )
    barrier = threading.Barrier(2)
    results = []

    class WaitingService(InstanceService):
        def allocate(self, value):
            barrier.wait()
            return super().allocate(value)

    def run(job_id):
        results.append(WaitingService(repo, clock=lambda: 100.0).allocate(job(job_id=job_id)))

    threads = [threading.Thread(target=run, args=(f"job-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert len({result["id"] for result in results}) == 2
    assert repo.get("instances", idle["id"], "owner-1")["current_job_id"] in {"job-0", "job-1"}


def test_unknown_or_requested_instance_never_falls_back(repo):
    account(repo)
    service = InstanceService(repo, clock=lambda: 100.0)
    with pytest.raises(AllocationError):
        service.allocate(job(requested_instance_id="does-not-exist"))
    assert repo.list("instances", "owner-1") == []


def test_retention_is_idempotent_and_stop_wins(repo):
    service = InstanceService(repo, clock=lambda: 100.0, config={"max_idle_minutes": 30})
    instance = service.allocate(job(lifecycle="retain"))
    first = service.retention("owner-1", instance["id"], "retain", 10, "retain-key")
    again = service.retention("owner-1", instance["id"], "retain", 10, "retain-key")
    assert first == again
    assert first["state"] == "retained_idle"
    assert first["idle_deadline_at"] == 700.0

    stopped = service.stop("owner-1", instance["id"], "stop-key")
    assert stopped["state"] == "releasing"
    with pytest.raises(InstanceConflict):
        service.retention("owner-1", instance["id"], "retain", 5, "late-retain")


def test_retention_never_extends_hard_deadline_and_usage_replay_is_free(repo):
    service = InstanceService(repo, clock=lambda: 100.0, config={"max_idle_minutes": 60})
    instance = service.allocate(job(lifecycle="retain", hard_deadline_at=350.0))
    retained = service.retention("owner-1", instance["id"], "smart", 60, "smart-key")
    assert retained["idle_deadline_at"] == 350.0
    assert retained["hard_deadline_at"] == 350.0

    payload = {
        "instance_id": instance["id"],
        "job_id": "job-1",
        "segment_key": "run-1",
        "started_at": 100.0,
        "ended_at": 160.0,
        "estimated_units": 1.5,
        "actual_units": None,
    }
    one = service.record_usage("owner-1", payload)
    two = service.record_usage("owner-1", payload)
    assert one["id"] == two["id"]
    assert len(repo.list("instance_usage_segments", "owner-1")) == 1
    assert two["actual_units"] is None


def test_real_allocation_requires_explicit_rate_and_project_allowlist(repo):
    account(repo, provider="colab", allowed_projects=["project-1"])
    service = InstanceService(repo, clock=lambda: 100.0, config={"unit_rate": None})
    with pytest.raises(BudgetError):
        service.allocate(job(provider="colab", execution_policy={"provider": "colab"}))

    service = InstanceService(repo, clock=lambda: 100.0, config={"unit_rate": 0.1})
    repo.update("accounts", "acct-1", {"allowed_projects": ["other-project"]}, "owner-1")
    with pytest.raises(AllocationError):
        service.allocate(job(provider="colab", execution_policy={"provider": "colab"}))


@pytest.mark.parametrize("rate", [0, -1, float("nan"), float("inf")])
def test_real_budget_rate_must_be_finite_and_positive(repo, rate):
    account(repo, provider="colab", allowed_projects=["project-1"])
    with pytest.raises(BudgetError):
        InstanceService(repo, clock=lambda: 100.0, config={"unit_rate": rate}).allocate(
            job(provider="colab", execution_policy={"provider": "colab"}, max_runtime_minutes=5)
        )


def test_real_budget_is_account_and_global_safe_and_missing_signature_does_not_reuse(repo):
    account(repo, provider="colab", allowed_projects=["project-1"], budget={"daily": 20.0, "monthly": 50.0})
    repo.create(
        "instance_usage_segments",
        "owner-1",
        {"account_id": "acct-1", "instance_id": "old", "estimated_units": 4.0, "actual_units": None},
        record_id="segment-1",
    )
    repo.create(
        "instances",
        "owner-1",
        {
            "account_id": "acct-1", "provider": "colab", "state": "busy", "current_job_id": "old-job",
            "budget_reserved": 1.0, "compatibility_signature": "compat-v1",
        },
        record_id="busy-old",
    )
    with pytest.raises(BudgetError):
        InstanceService(repo, clock=lambda: 100.0, config={"unit_rate": 0.1, "daily_units": 5.0}).allocate(
            job(provider="colab", budget_units=1.0, execution_policy={"provider": "colab"})
        )

    idle = repo.create(
        "instances", "owner-1",
        {"account_id": "acct-1", "provider": "colab", "state": "retained_idle", "current_job_id": None},
        record_id="missing-signature",
    )
    result = InstanceService(repo, clock=lambda: 100.0, config={"unit_rate": 0.1, "daily_units": 20.0}).allocate(
        job(provider="colab", job_id="job-new", budget_units=1.0, execution_policy={"provider": "colab"})
    )
    assert result["id"] != idle["id"]


def test_real_global_budget_sums_reservations_across_accounts(repo):
    account(repo, "acct-1", provider="colab", allowed_projects=["project-1"], budget={"daily": 10.0, "monthly": 50.0})
    account(repo, "acct-2", provider="colab", allowed_projects=["project-1"], budget={"daily": 10.0, "monthly": 50.0})
    repo.create(
        "instances",
        "owner-1",
        {"account_id": "acct-2", "provider": "colab", "state": "retained_idle", "budget_reserved": 1.5},
        record_id="retained-acct-2",
    )
    repo.create(
        "instances",
        "owner-1",
        {"account_id": "acct-2", "provider": "colab", "state": "releasing", "budget_reserved": 1.5},
        record_id="releasing-acct-2",
    )

    service = InstanceService(
        repo,
        clock=lambda: 100.0,
        config={"unit_rate": 0.1, "daily_units": 5.0, "monthly_units": 50.0},
    )
    with pytest.raises(BudgetError):
        service.allocate(
            job(
                provider="colab",
                requested_account_id="acct-1",
                budget_units=2.1,
                execution_policy={"provider": "colab"},
            )
        )


def test_real_allocation_rejects_account_without_nested_budget(repo):
    account(repo, "acct-1", provider="colab", allowed_projects=["project-1"])
    service = InstanceService(
        repo,
        clock=lambda: 100.0,
        config={"unit_rate": 0.1, "daily_units": 100.0, "monthly_units": 100.0},
    )
    with pytest.raises(BudgetError):
        service.allocate(
            job(
                provider="colab",
                requested_account_id="acct-1",
                budget_units=1.0,
                execution_policy={"provider": "colab"},
            )
        )


def test_real_retention_reserves_idle_fee_against_budget(repo):
    account(repo, "acct-1", provider="colab", allowed_projects=["project-1"], budget={"daily": 1.5, "monthly": 20.0})
    service = InstanceService(
        repo,
        clock=lambda: 100.0,
        config={"unit_rate": 0.1, "daily_units": 100.0, "monthly_units": 100.0, "max_idle_minutes": 30},
    )
    instance = service.allocate(
        job(
            provider="colab",
            requested_account_id="acct-1",
            budget_units=1.0,
            execution_policy={"provider": "colab"},
        )
    )
    with pytest.raises(BudgetError):
        service.retention("owner-1", instance["id"], "retain", 10, "retention-over-budget")

    repo.update("accounts", "acct-1", {"budget": {"daily": 3.0, "monthly": 20.0}}, "owner-1")
    retained = service.retention("owner-1", instance["id"], "retain", 10, "retention-with-reserve")
    assert retained["retention_reserved"] == 1.0


def test_usage_reports_request_to_confirmed_deletion_duration(repo):
    service = InstanceService(repo, clock=lambda: 100.0)
    instance = service.allocate(job())
    usage = service.record_usage(
        "owner-1",
        {
            "instance_id": instance["id"],
            "job_id": "job-1",
            "segment_key": "run-with-delete-confirmation",
            "started_at": 110.0,
            "ended_at": 160.0,
            "allocation_requested_at": 100.0,
            "deletion_confirmed_at": 400.0,
            "estimated_units": 1.5,
        },
    )
    assert usage["total_duration_seconds"] == 300.0
    assert usage["total_duration_minutes"] == 5.0


def test_public_and_events_never_expose_handles_or_tokens(repo):
    service = InstanceService(repo, clock=lambda: 100.0)
    instance = service.allocate(job(handle={"runtime_name": "secret", "token": "secret"}))
    public = service.public(instance)
    assert "handle" not in public and "token" not in public
    assert "runtime_name" not in public
    assert all("token" not in str(event).lower() for event in service.events("owner-1", instance["id"]))


def test_instance_retention_override_is_authoritative_and_deletion_requires_confirmation(repo):
    service = InstanceService(repo, clock=lambda: 100.0, config={"max_idle_minutes": 30})
    instance = service.allocate(job(lifecycle="save"))
    retained = service.retention("owner-1", instance["id"], "retain", 10, "keep-key")
    assert retained["retention_mode"] == "retain"
    completed = service.complete(job(lifecycle="save"))
    assert completed["state"] == "retained_idle"

    service.stop("owner-1", instance["id"], "stop-key")
    with pytest.raises(InstanceConflict):
        service.mark_deleted("owner-1", instance["id"])
    deleted = service.mark_deleted("owner-1", instance["id"], confirmed=True)
    assert deleted["state"] == "deleted"


def test_remote_confirmed_deletion_can_close_retained_idle_instance(repo):
    service = InstanceService(repo, clock=lambda: 100.0, config={"max_idle_minutes": 30})
    instance = service.allocate(job(lifecycle="retain"))
    retained = service.retention("owner-1", instance["id"], "retain", 10, "retained-delete-key")
    assert retained["state"] == "retained_idle"
    deleted = service.mark_deleted("owner-1", instance["id"], remote_deleted=True)
    assert deleted["state"] == "deleted"
    assert deleted["deletion_confirmed_at"] == 100.0


def test_bind_lease_persists_instance_and_lease_account_identity_without_fallback(repo):
    service = InstanceService(repo, clock=lambda: 100.0)
    instance = service.allocate(job())
    lease = repo.create("leases", "owner-1", {"state": "active"}, record_id="lease-1")
    bound = service.bind_lease({**job(), "instance_id": instance["id"]}, {**lease, "deadline_epoch": 500.0})
    assert bound["lease_id"] == "lease-1"
    saved_lease = repo.get("leases", "lease-1", "owner-1")
    assert saved_lease["instance_id"] == instance["id"]
    assert saved_lease["account_id"] == instance["account_id"]


def test_mock_allocates_with_unconnected_google_directory(repo):
    account(repo, connected=False, auth_status="not_connected", enabled=False)
    instance = InstanceService(repo).allocate(job())
    assert instance["provider"] == "mock"
    assert instance["account_id"].startswith("mock-")


@pytest.mark.parametrize("projects,allowed", [([], True), (["project-1"], True), (["other-project"], False)])
def test_account_project_directory_empty_means_unrestricted(repo, projects, allowed):
    from types import SimpleNamespace
    row = SimpleNamespace(owner_id="owner-1", data={"connected": True, "enabled": True, "allow_allocation": True, "projects": projects})
    assert InstanceService(repo)._account_legal(row, "owner-1", "project-1", "colab") is allowed


def test_retained_instance_refreshes_idle_deadline_on_subsequent_job_completion(repo):
    now = [100.0]
    service = InstanceService(repo, clock=lambda: now[0], config={"max_idle_minutes": 30})
    j1 = job(job_id="job-1", lifecycle="retain", execution_policy={"provider": "mock", "lifecycle": "retain", "retain_idle_minutes": 30})
    instance = service.allocate(j1)
    first_done = service.complete(j1, success=True)
    assert first_done["state"] == "retained_idle"
    assert first_done["idle_deadline_at"] == 100.0 + 30 * 60

    now[0] = 1850.0
    j2 = job(job_id="job-2", requested_instance_id=instance["id"], lifecycle="retain", execution_policy={"provider": "mock", "lifecycle": "retain", "retain_idle_minutes": 30})
    reused = service.allocate(j2)
    assert reused["id"] == instance["id"]
    assert reused["idle_deadline_at"] is None

    now[0] = 2000.0
    second_done = service.complete(j2, success=True)
    assert second_done["state"] == "retained_idle"
    assert second_done["idle_deadline_at"] == 2000.0 + 30 * 60

