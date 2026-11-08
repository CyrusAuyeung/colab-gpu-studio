"""Issue #6 regressions: safely converge historical orphan instances."""

import pytest
from app.db import Store
from app.gpu.compute import GpuConfig
from app.gpu.instances import BudgetError
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


class InspectOnlyProvider:
    def __init__(self, status=None):
        self.status = {"deleted": True} if status is None else status
        self.inspect_calls = []
        self.create_calls = 0
        self.delete_calls = 0

    def inspect(self, handle):
        self.inspect_calls.append(handle)
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    def create(self, *args, **kwargs):
        self.create_calls += 1
        raise AssertionError("historical reconciliation must not create a runtime")

    def delete(self, *args, **kwargs):
        self.delete_calls += 1
        raise AssertionError("historical reconciliation must not delete a runtime")


@pytest.fixture
def orphan(tmp_path):
    repo = Repository(Store(f"sqlite:///{tmp_path / 'issue6.sqlite'}"))
    repo.create(
        "accounts",
        "owner",
        {
            "account_id": "account",
            "provider": "colab",
            "connected": True,
            "enabled": True,
            "allow_allocation": True,
            "budget": {"daily": 10, "monthly": 10},
        },
        record_id="account",
    )
    repo.create(
        "jobs",
        "owner",
        {
            "state": "completed",
            "provider": "colab",
            "account_id": "account",
            "instance_id": "previous-instance",
            "lease_id": "shared-lease",
        },
        record_id="previous-job",
    )
    repo.create(
        "jobs",
        "owner",
        {
            "state": "completed",
            "provider": "colab",
            "account_id": "account",
            "instance_id": "previous-instance",
            "lease_id": "shared-lease",
        },
        record_id="next-job",
    )
    repo.create(
        "leases",
        "owner",
        {
            "state": "deleted",
            "owner_job_id": "next-job",
            "provider": "colab",
            "instance_id": "previous-instance",
            "account_id": "account",
            "runtime_id": "studio-orphan",
            "request_id": "request-orphan",
            "handle": {
                "runtime_id": "studio-orphan",
                "job_id": "next-job",
                "owner_job_id": "next-job",
            },
        },
        record_id="shared-lease",
    )
    repo.create(
        "instances",
        "owner",
        {
            "state": "releasing",
            "provider": "colab",
            "account_id": "account",
            "current_job_id": "previous-job",
            "lease_id": "shared-lease",
            "runtime_id": "studio-orphan",
            "budget_reserved": 8,
            "manual_stop_requested": True,
        },
        record_id="previous-instance",
    )
    provider = InspectOnlyProvider()
    scheduler = Scheduler(
        repo,
        provider_factory=lambda job: provider,
        clock=lambda: 1000,
        config=GpuConfig(daily_units=10, monthly_units=10),
    )
    return repo, scheduler, provider


def reservation(scheduler):
    with scheduler.db_store.Session() as session:
        return scheduler.instances._budget_totals(session, "owner", "account")


@pytest.mark.parametrize("state", ["releasing", "cleanup_pending"])
def test_watchdog_reconciles_historical_deleted_handoff_lease(orphan, state):
    repo, scheduler, provider = orphan
    repo.update("instances", "previous-instance", {"state": state}, "owner")

    assert reservation(scheduler) == (8, 8)
    assert scheduler.watchdog()["recovered"] == 1

    instance = repo.get("instances", "previous-instance", "owner")
    assert instance["state"] == "deleted"
    assert instance["remote_deleted"] is True
    assert reservation(scheduler) == (0, 0)
    lease = repo.get("leases", "shared-lease", "owner")
    assert lease["remote_delete_source"] == "orphan_reconciliation_provider_confirmation"
    assert provider.inspect_calls[0]["runtime_id"] == "studio-orphan"
    assert provider.inspect_calls[0]["owner_job_id"] == "next-job"
    assert provider.create_calls == provider.delete_calls == 0

    assert scheduler.watchdog()["recovered"] == 0
    assert len(provider.inspect_calls) == 1


def test_unknown_remote_state_keeps_historical_reservation(orphan):
    repo, scheduler, provider = orphan
    provider.status = {"deleted": False, "ready": True}

    assert scheduler.watchdog()["recovered"] == 0
    assert repo.get("instances", "previous-instance", "owner")["state"] in {"releasing", "cleanup_pending"}
    assert reservation(scheduler) == (8, 8)


@pytest.mark.parametrize("status", [
    {},
    {"deleted": "true"},
    {"deleted": True, "allocation_unknown": True},
    {"deleted": True, "operation_pending": True},
    {"deleted": True, "pending": True},
    PermissionError("offline provider permission denied"),
    TimeoutError("offline provider timeout"),
])
def test_ambiguous_or_failed_inspection_never_releases_budget(orphan, status):
    repo, scheduler, provider = orphan
    provider.status = status

    assert scheduler.watchdog()["recovered"] == 0
    assert repo.get("instances", "previous-instance", "owner")["state"] == "cleanup_pending"
    assert reservation(scheduler) == (8, 8)
    assert provider.create_calls == provider.delete_calls == 0


@pytest.mark.parametrize("case", ["wrong-account", "wrong-provider", "missing-runtime", "active-job", "active-alias"])
def test_identity_and_live_work_guards_fail_closed(orphan, case):
    repo, scheduler, provider = orphan
    if case == "wrong-account":
        repo.update("instances", "previous-instance", {"account_id": "other-account"}, "owner")
    elif case == "wrong-provider":
        repo.update("leases", "shared-lease", {"provider": "mock"}, "owner")
    elif case == "missing-runtime":
        repo.update("leases", "shared-lease", {"runtime_id": None}, "owner")
    elif case == "active-job":
        repo.update("jobs", "previous-job", {"state": "running"}, "owner")
    elif case == "active-alias":
        repo.create(
            "leases",
            "owner",
            {
                "state": "active",
                "owner_job_id": "next-job",
                "provider": "colab",
                "instance_id": "previous-instance",
                "account_id": "account",
                "runtime_id": "studio-orphan",
            },
            record_id="live-alias",
        )

    assert scheduler.watchdog()["recovered"] == 0
    assert repo.get("instances", "previous-instance", "owner")["state"] in {"releasing", "cleanup_pending"}
    with scheduler.db_store.Session() as session:
        assert scheduler.instances._budget_totals(session, "owner") == (8, 8)
    assert provider.inspect_calls == []
    assert provider.create_calls == provider.delete_calls == 0




def test_historical_reconcile_accepts_rebound_deleted_handoff_lease(orphan):
    repo, scheduler, provider = orphan
    repo.create(
        "instances",
        "owner",
        {
            "state": "deleted",
            "provider": "colab",
            "account_id": "account",
            "current_job_id": "next-job",
            "lease_id": "shared-lease",
            "runtime_id": "studio-orphan",
        },
        record_id="next-instance",
    )
    repo.update("jobs", "next-job", {"instance_id": "next-instance"}, "owner")
    repo.update("leases", "shared-lease", {"instance_id": "next-instance"}, "owner")

    assert scheduler.watchdog()["recovered"] == 1
    assert repo.get("instances", "previous-instance", "owner")["state"] == "deleted"
    assert provider.inspect_calls[0]["owner_job_id"] == "next-job"


def test_historical_reconcile_honors_cleanup_next_epoch(orphan):
    repo, scheduler, provider = orphan
    repo.update("instances", "previous-instance", {"cleanup_next_epoch": 2000}, "owner")

    assert scheduler.watchdog()["recovered"] == 0
    assert provider.inspect_calls == []
    assert repo.get("instances", "previous-instance", "owner")["state"] == "releasing"


@pytest.mark.parametrize("mutation", ["job", "instance", "lease"])
def test_historical_finish_rechecks_claimed_identity_and_version(orphan, mutation):
    repo, _, _ = orphan
    token = "finish-fence"
    claimed = repo.claim_orphan_instance("owner", "previous-instance", "shared-lease", token, 1000)
    assert claimed is not None
    expected_version = claimed[0]["version"]
    if mutation == "job":
        repo.update("jobs", "next-job", {"state": "running"}, "owner")
    elif mutation == "instance":
        repo.update("instances", "previous-instance", {"last_seen_at": 1001}, "owner")
    else:
        repo.update("leases", "shared-lease", {"runtime_id": "different-runtime"}, "owner")

    assert repo.finish_orphan_instance(
        "owner",
        "previous-instance",
        "shared-lease",
        token,
        1001,
        confirmed=True,
        expected_version=expected_version,
        expected_lease_version=claimed[1]["version"],
    ) is False
    assert repo.get("instances", "previous-instance", "owner")["state"] != "deleted"


@pytest.mark.parametrize("field,value", [("account_id", "other-account"), ("provider", "mock"), ("request_id", "other-request")])
def test_historical_finish_rejects_changed_lease_version(orphan, field, value):
    repo, scheduler, provider = orphan
    original = provider.inspect

    def changed(handle):
        result = original(handle)
        repo.update("leases", "shared-lease", {field: value}, "owner")
        return result

    provider.inspect = changed
    assert scheduler.watchdog()["recovered"] == 0
    assert repo.get("instances", "previous-instance", "owner")["state"] != "deleted"


def test_historical_finish_clears_typed_current_job_binding(orphan):
    repo, scheduler, _ = orphan
    assert scheduler.watchdog()["recovered"] == 1
    assert repo.get("instances", "previous-instance", "owner")["current_job_id"] is None


def test_budget_still_enforces_limits_after_reconciliation(orphan):
    repo, scheduler, _ = orphan
    assert scheduler.watchdog()["recovered"] == 1
    with scheduler.db_store.Session() as session:
        account = scheduler.instances._account_row(session, "account", "owner")
        scheduler.instances._check_budget(session, account, 3)
        with pytest.raises(BudgetError):
            scheduler.instances._check_budget(session, account, 11)
