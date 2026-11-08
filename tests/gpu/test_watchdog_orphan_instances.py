"""Colab-adapter contract regressions for the upstream orphan reconciler (#6).

Use the upstream claim/finalize path instead of maintaining a second scheduler.
No real provider calls, allocations, or deletions are permitted by these tests.
"""

import httpx
import pytest
from app.db import Store
from app.gpu.colab import GoogleColabProvider
from app.gpu.compute import GpuConfig
from app.gpu.instances import BudgetError
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


@pytest.fixture
def historical_handoff(tmp_path):
    repo = Repository(Store(f"sqlite:///{tmp_path / 'adapter-orphan.sqlite'}"))
    repo.create("accounts", "owner", {
        "account_id": "account", "provider": "colab", "connected": True,
        "enabled": True, "allow_allocation": True, "budget": {"daily": 10, "monthly": 10},
    }, record_id="account")
    for job_id, instance_id in [("previous-job", "previous-instance"), ("next-job", "next-instance")]:
        repo.create("jobs", "owner", {
            "state": "completed", "provider": "colab", "account_id": "account",
            "instance_id": instance_id, "lease_id": "shared-lease",
        }, record_id=job_id)
    repo.create("leases", "owner", {
        "state": "deleted", "owner_job_id": "next-job", "provider": "colab",
        "instance_id": "next-instance", "account_id": "account",
        "runtime_id": "studio-orphan", "request_id": "request-orphan",
        "create_attempts": 1, "operation_done": True,
    }, record_id="shared-lease")
    repo.create("instances", "owner", {
        "state": "deleted", "provider": "colab", "account_id": "account",
        "current_job_id": None, "lease_id": "shared-lease", "runtime_id": "studio-orphan",
    }, record_id="next-instance")
    repo.create("instances", "owner", {
        "state": "releasing", "provider": "colab", "account_id": "account",
        "current_job_id": "previous-job", "lease_id": "shared-lease",
        "runtime_id": "studio-orphan", "budget_reserved": 8, "manual_stop_requested": True,
    }, record_id="previous-instance")
    return repo


class OfflineGoogle:
    def access_token(self, purpose):
        assert purpose == "colab"
        return "offline-test-only"


class InspectOnlyColab(GoogleColabProvider):
    def create(self, *args, **kwargs):
        pytest.fail("reconciliation must not allocate a runtime")

    def delete(self, *args, **kwargs):
        pytest.fail("reconciliation must not delete a handed-off runtime")

    def list_runtimes(self):
        pytest.fail("an empty account-wide runtime list is not deletion evidence")


def assert_budget(scheduler, reserved):
    with scheduler.db_store.Session() as session:
        assert scheduler.instances._budget_totals(session, "owner", "account") == (reserved, reserved)
        account = scheduler.instances._account_row(session, "account", "owner")
        if reserved:
            with pytest.raises(BudgetError):
                scheduler.instances._check_budget(session, account, 3)
        else:
            scheduler.instances._check_budget(session, account, 3)
        with pytest.raises(BudgetError):
            scheduler.instances._check_budget(session, account, 11)


@pytest.mark.parametrize("response", [
    "gone", "active", "ambiguous-404", "timeout", "forbidden", "server-error", "malformed",
])
def test_owned_runtime_inspection_controls_handoff_budget(historical_handoff, response):
    repo = historical_handoff
    requests = []
    if response == "ambiguous-404":
        repo.update("leases", "shared-lease", {"operation_done": False}, "owner")

    def handler(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET"
        assert request.url.path == "/v1beta/runtimes/studio-orphan"
        if response == "timeout":
            raise httpx.ReadTimeout("offline timeout", request=request)
        if response == "forbidden":
            return httpx.Response(403, json={"error": "offline forbidden"})
        if response == "server-error":
            return httpx.Response(503, json={"error": "offline unavailable"})
        if response == "malformed":
            return httpx.Response(200, content=b"not-json")
        if response == "active":
            return httpx.Response(200, json={"connectionInfo": {"url": "unused"}})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = InspectOnlyColab(repo, "owner", account_id="account", client=client, google=OfflineGoogle())
        scheduler = Scheduler(repo, provider_factory=lambda job: provider,
                              clock=lambda: 1000, config=GpuConfig(daily_units=10, monthly_units=10))
        assert_budget(scheduler, 8)
        assert scheduler.watchdog()["recovered"] == int(response == "gone")
        previous = repo.get("instances", "previous-instance", "owner")
        assert previous["state"] == ("deleted" if response == "gone" else "cleanup_pending")
        assert_budget(scheduler, 0 if response == "gone" else 8)
        assert requests == [("GET", "/v1beta/runtimes/studio-orphan")]
        # A successful confirmation is idempotent; errors respect upstream backoff.
        assert scheduler.watchdog()["recovered"] == 0
        assert requests == [("GET", "/v1beta/runtimes/studio-orphan")]
        assert len(repo.list("instances", "owner")) == 2
        assert repo.get("instances", "next-instance", "owner")["state"] == "deleted"


@pytest.mark.parametrize("field,value", [
    ("account_id", "different-account"), ("provider", "mock"), ("runtime_id", "different-runtime"),
])
def test_adapter_confirmation_cannot_finalize_changed_lease(historical_handoff, field, value):
    repo = historical_handoff
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.method == "GET"
        repo.update("leases", "shared-lease", {field: value}, "owner")
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = InspectOnlyColab(repo, "owner", account_id="account", client=client, google=OfflineGoogle())
        scheduler = Scheduler(repo, provider_factory=lambda job: provider,
                              clock=lambda: 1000, config=GpuConfig(daily_units=10, monthly_units=10))
        assert scheduler.watchdog()["recovered"] == 0
        assert repo.get("instances", "previous-instance", "owner")["state"] != "deleted"
        assert_budget(scheduler, 8)
        assert calls == [("GET", "/v1beta/runtimes/studio-orphan")]
