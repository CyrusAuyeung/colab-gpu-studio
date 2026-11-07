"""Offline regressions for release records left behind by lease handoff (#5/#6)."""

import httpx
import pytest
from app.db import Store
from app.gpu.colab import GoogleColabProvider
from app.gpu.compute import GpuConfig, MockColabProvider, MockGPUExecutor
from app.gpu.instances import BudgetError
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


class InspectOnlyProvider:
    def __init__(self, status=None):
        self.status = {"deleted": True} if status is None else status
        self.handles = []

    def inspect(self, handle):
        self.handles.append(handle)
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    def list_runtimes(self):
        return []  # An empty account listing is deliberately not deletion evidence.

    def create(self, *args, **kwargs):
        pytest.fail("orphan reconciliation must not allocate a runtime")

    def delete(self, *args, **kwargs):
        pytest.fail("orphan reconciliation must not delete a possibly handed-off runtime")


@pytest.fixture
def orphan(tmp_path):
    repo = Repository(Store(f"sqlite:///{tmp_path / 'orphan.sqlite'}"))
    repo.create("accounts", "owner", {
        "account_id": "account", "provider": "colab", "connected": True,
        "enabled": True, "allow_allocation": True, "budget": {"daily": 10, "monthly": 10},
    }, record_id="account")
    repo.create("jobs", "owner", {
        "state": "completed", "provider": "colab", "instance_id": "previous-instance",
        "lease_id": "shared-lease",
    }, record_id="previous-job")
    repo.create("jobs", "owner", {"state": "completed"}, record_id="next-job")
    repo.create("leases", "owner", {
        "state": "deleted", "owner_job_id": "next-job", "provider": "colab",
        "instance_id": "next-instance", "account_id": "account",
        "runtime_id": "studio-orphan", "request_id": "request-orphan", "create_attempts": 1,
        "operation_done": True,
    }, record_id="shared-lease")
    repo.create("instances", "owner", {
        "state": "releasing", "provider": "colab", "account_id": "account",
        "current_job_id": "previous-job", "lease_id": "shared-lease", "budget_reserved": 8,
        "manual_stop_requested": True,
    }, record_id="previous-instance")
    provider = InspectOnlyProvider()
    scheduler = Scheduler(repo, provider_factory=lambda job: provider,
                          clock=lambda: 1000, config=GpuConfig(daily_units=10, monthly_units=10))
    return repo, scheduler, provider


def reservation(scheduler):
    with scheduler.db_store.Session() as session:
        return scheduler.instances._budget_totals(session, "owner", "account")


def check_budget(scheduler, amount):
    with scheduler.db_store.Session() as session:
        account = scheduler.instances._account_row(session, "account", "owner")
        scheduler.instances._check_budget(session, account, amount)


@pytest.mark.parametrize("state", ["releasing", "cleanup_pending"])
def test_watchdog_converges_handoff_orphan_and_releases_only_stale_reservation(orphan, state):
    repo, scheduler, provider = orphan
    repo.update("instances", "previous-instance", {"state": state}, "owner")
    assert reservation(scheduler) == (8, 8)
    with pytest.raises(BudgetError):
        check_budget(scheduler, 3)
    assert scheduler.watchdog()["recovered"] == 1
    deleted = repo.get("instances", "previous-instance", "owner")
    assert deleted["state"] == "deleted" and deleted["remote_deleted"] is True
    assert reservation(scheduler) == (0, 0)
    check_budget(scheduler, 3)
    with pytest.raises(BudgetError):
        check_budget(scheduler, 11)  # Reconciliation must not disable budget enforcement.
    assert provider.handles[0]["owner_job_id"] == "next-job"
    assert provider.handles[0]["owner_id"] == "owner"
    assert scheduler.watchdog()["recovered"] == 0
    assert repo.get("instances", "previous-instance", "owner") == deleted
    assert len(provider.handles) == 1
    assert len(repo.list("instances")) == 1


@pytest.mark.parametrize("status", [
    {"deleted": False, "ready": True}, {}, {"deleted": True, "allocation_unknown": True},
    {"deleted": True, "operation_pending": True}, {"deleted": "true"},
    TimeoutError("offline provider timeout"),
])
def test_active_unknown_or_failed_inspection_keeps_budget_reserved(orphan, status):
    repo, scheduler, provider = orphan
    provider.status = status
    assert scheduler.watchdog()["recovered"] == 0
    assert repo.get("instances", "previous-instance", "owner")["state"] == "releasing"
    assert reservation(scheduler) == (8, 8)
    with pytest.raises(BudgetError):
        check_budget(scheduler, 3)


@pytest.mark.parametrize("case", [
    "active-job", "other-active-job", "foreign-current-job", "missing-current-job", "live-lease",
    "missing-lease", "foreign-lease", "live-runtime-alias", "unknown-instance", "retained-instance",
    "wrong-account", "wrong-provider", "unbound-job-lease", "wrong-lease-provider",
    "active-lease-owner-job", "foreign-lease-owner-job", "missing-lease-owner-job",
])
def test_ownership_work_and_handoff_guards_fail_closed(orphan, case):
    repo, scheduler, provider = orphan
    if case == "active-job":
        repo.update("jobs", "previous-job", {"state": "running"}, "owner")
    elif case == "other-active-job":
        repo.create("jobs", "owner", {"state": "running", "instance_id": "previous-instance"})
    elif case == "foreign-current-job":
        repo.create("jobs", "other-owner", {"state": "completed"}, record_id="foreign-job")
        repo.update("instances", "previous-instance", {"current_job_id": "foreign-job"}, "owner")
    elif case == "missing-current-job":
        repo.update("instances", "previous-instance", {"current_job_id": "missing-job"}, "owner")
    elif case == "live-lease":
        # The current lease belongs to a running handoff recipient, not the old terminal job.
        repo.update("leases", "shared-lease", {"state": "active"}, "owner")
        repo.update("jobs", "next-job", {"state": "running"}, "owner")
    elif case == "missing-lease":
        repo.update("instances", "previous-instance", {"lease_id": "missing-lease"}, "owner")
    elif case == "foreign-lease":
        repo.create("leases", "other-owner", {"state": "deleted"}, record_id="foreign-lease")
        repo.update("instances", "previous-instance", {"lease_id": "foreign-lease"}, "owner")
    elif case == "live-runtime-alias":
        repo.create("leases", "owner", {
            "state": "active", "runtime_id": "studio-orphan", "provider": "colab", "account_id": "account",
            "owner_job_id": "next-job",
        })
        repo.update("jobs", "next-job", {"state": "running"}, "owner")
    elif case == "unknown-instance":
        repo.update("instances", "previous-instance", {"state": "unknown"}, "owner")
    elif case == "retained-instance":
        repo.update("instances", "previous-instance", {"state": "retained_idle"}, "owner")
    elif case == "wrong-account":
        repo.update("leases", "shared-lease", {"account_id": "different-account"}, "owner")
    elif case == "wrong-provider":
        repo.update("leases", "shared-lease", {"provider": "mock"}, "owner")
    elif case == "unbound-job-lease":
        repo.update("instances", "previous-instance", {"lease_id": None}, "owner")
    elif case == "wrong-lease-provider":
        repo.update("leases", "shared-lease", {"provider": "unsupported-provider"}, "owner")
        repo.update("instances", "previous-instance", {"provider": "unsupported-provider"}, "owner")
    elif case == "active-lease-owner-job":
        repo.update("jobs", "next-job", {"state": "running"}, "owner")
    elif case == "foreign-lease-owner-job":
        repo.create("jobs", "other-owner", {"state": "completed"}, record_id="foreign-owner-job")
        repo.update("leases", "shared-lease", {"owner_job_id": "foreign-owner-job"}, "owner")
    elif case == "missing-lease-owner-job":
        repo.update("leases", "shared-lease", {"owner_job_id": "missing-job"}, "owner")
    assert scheduler.watchdog()["recovered"] == 0
    assert repo.get("instances", "previous-instance", "owner")["state"] != "deleted"
    assert reservation(scheduler) == (8, 8)
    assert provider.handles == []


def test_never_bound_placeholder_can_converge_without_provider_io(orphan):
    repo, scheduler, provider = orphan
    repo.update("instances", "previous-instance", {"lease_id": None}, "owner")
    repo.update("jobs", "previous-job", {"lease_id": None}, "owner")
    assert scheduler.watchdog()["recovered"] == 1
    assert reservation(scheduler) == (0, 0)
    assert provider.handles == []


def test_changed_instance_during_inspection_is_not_finalized(orphan):
    repo, scheduler, provider = orphan

    def inspect(handle):
        repo.update("instances", "previous-instance", {"state": "unknown"}, "owner")
        return {"deleted": True}

    provider.inspect = inspect
    assert scheduler.watchdog()["recovered"] == 0
    assert repo.get("instances", "previous-instance", "owner")["state"] == "unknown"
    assert reservation(scheduler) == (8, 8)


def test_batch_handoff_orphan_is_converged_with_real_service_and_provider(tmp_path):
    """End-to-end #5 path: batch handoff, manual stop, then watchdog convergence."""
    repo = Repository(Store(f"sqlite:///{tmp_path / 'handoff.sqlite'}"))
    jobs = [{"project_id": "project", "snapshot": {"manifest": {"mock": True}},
             "execution_policy": {"provider": "mock", "lifecycle": "batch"}} for _ in range(2)]
    repo.create_batch("owner", jobs, "batch-key", {"mode": "batch"})
    provider, executor = MockColabProvider(), MockGPUExecutor()
    scheduler = Scheduler(repo, provider_factory=lambda job: provider,
                          executor_factory=lambda job: executor)
    for _ in range(200):
        scheduler.tick()
        if all(j["state"] in {"completed", "failed", "cancelled"} for j in repo.list("jobs", "owner")):
            break
    else:
        pytest.fail("batch did not finish")
    assert all(j["state"] == "completed" for j in repo.list("jobs", "owner"))
    states = [i["state"] for i in repo.list("instances", "owner")]
    assert sorted(states) == ["busy", "deleted"], states
    orphan = next(i for i in repo.list("instances", "owner") if i["state"] == "busy")
    scheduler.instances.stop("owner", orphan["id"], "manual-stop")
    creates, deletes = provider.create_calls, provider.delete_calls
    assert scheduler.watchdog()["recovered"] == 1
    assert all(i["state"] == "deleted" for i in repo.list("instances", "owner"))
    assert provider.create_calls == creates == 1
    assert provider.delete_calls == deletes == 1
    assert all(lease["state"] == "deleted" for lease in repo.list("leases", "owner"))


@pytest.mark.parametrize("response", ["gone", "active", "unknown", "timeout", "forbidden"])
def test_real_colab_adapter_only_accepts_unambiguous_owned_404(orphan, response):
    repo, scheduler, _ = orphan
    requests = []

    class OfflineGoogle:
        def access_token(self, purpose):
            return "offline-test-only"

    def handler(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET"
        assert request.url.path == "/v1beta/runtimes/studio-orphan"
        if response == "timeout":
            raise httpx.ReadTimeout("offline timeout", request=request)
        if response == "forbidden":
            return httpx.Response(403, json={"error": "offline forbidden"})
        if response == "active":
            return httpx.Response(200, json={"connectionInfo": {"url": "unused"}})
        return httpx.Response(404)

    if response == "unknown":
        repo.update("leases", "shared-lease", {"operation_done": False}, "owner")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleColabProvider(repo, "owner", account_id="account",
                                       client=client, google=OfflineGoogle())
        scheduler.provider_factory = lambda job: provider
        assert scheduler.watchdog()["recovered"] == int(response == "gone")
    assert requests == [("GET", "/v1beta/runtimes/studio-orphan")]
    assert reservation(scheduler) == ((0, 0) if response == "gone" else (8, 8))
