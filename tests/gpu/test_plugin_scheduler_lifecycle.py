from __future__ import annotations

from app.db import Store
from app.gpu.compute import MockColabProvider, MockGPUExecutor
from app.gpu.newgpu.executor_plugins import TrustedExecutorAdapter
from app.gpu.repository import Repository
from app.gpu.scheduler import Scheduler


class FakeTrustedPluginExecutor(TrustedExecutorAdapter):
    """Offline trusted adapter whose hooks are observable without provider I/O."""

    def __init__(self, *, polls_before_complete=0, fail_on_poll=False, fail_cleanup=False):
        self.calls = []
        self.observed_deadlines = []
        self.fail_cleanup = fail_cleanup
        self.delegate = MockGPUExecutor(
            polls_before_complete=polls_before_complete,
            fail_on_poll=fail_on_poll,
        )

    def start(self, handle, job, execution_id):
        self.calls.append("start")
        self.observed_deadlines.append(("start", job.get("deadline_epoch")))
        return self.delegate.start(handle, job, execution_id)

    def poll(self, handle, job):
        self.calls.append("poll")
        self.observed_deadlines.append(("poll", job.get("deadline_epoch")))
        return self.delegate.poll(handle, job)

    def cancel(self, handle, job):
        self.calls.append("cancel")
        if self.fail_cleanup:
            raise RuntimeError("plugin-secret-must-not-leak")
        return {"state": "cancelled"}

    def cleanup(self, handle, job):
        self.calls.append("cleanup")
        if self.fail_cleanup:
            raise RuntimeError("plugin-secret-must-not-leak")
        return {"state": "cleaned"}


def make_job(repo, key, **extra):
    return repo.create_job(
        "owner-1",
        {
            "provider": "mock",
            "project_id": "project-1",
            "model_id": "demo",
            "workflow_key": "demo",
            "workflow_version": 1,
            "params": {},
            "snapshot": {"manifest": {"mock": True}},
            "execution_policy": {"provider": "mock", "lifecycle": "save"},
            **extra,
        },
        key,
    )[0]


def run_to_terminal(scheduler, repo, job_id, limit=60):
    for _ in range(limit):
        scheduler.tick()
        job = repo.get("jobs", job_id, "owner-1")
        if job["state"] in {"completed", "failed", "cancelled"}:
            return job
    return repo.get("jobs", job_id, "owner-1")


def scheduler_for(repo, executor, provider):
    return Scheduler(
        repo,
        provider_factory=lambda _job: provider,
        executor_factory=lambda _job: executor,
        clock=lambda: 1_000.0,
    )


def test_completed_plugin_cleanup_precedes_terminal_and_runtime_delete():
    repo = Repository(Store("sqlite:///:memory:"))
    job = make_job(repo, "plugin-completed")
    provider = MockColabProvider()
    executor = FakeTrustedPluginExecutor()
    scheduler = scheduler_for(repo, executor, provider)

    result = run_to_terminal(scheduler, repo, job["id"])

    assert result["state"] == "completed"
    assert executor.calls == ["start", "poll", "cleanup"]
    assert provider.delete_calls == 1
    assert repo.get("leases", result["lease_id"], "owner-1")["state"] == "deleted"
    assert result["trusted_plugin_cleanup_succeeded"] is True

    scheduler._cleanup(repo.get("jobs", job["id"], "owner-1"))
    assert executor.calls == ["start", "poll", "cleanup"]
    assert provider.delete_calls == 1


def test_cancelled_plugin_is_cancelled_then_cleaned_before_runtime_delete():
    repo = Repository(Store("sqlite:///:memory:"))
    job = make_job(repo, "plugin-cancelled")
    provider = MockColabProvider()
    executor = FakeTrustedPluginExecutor(polls_before_complete=20)
    scheduler = scheduler_for(repo, executor, provider)

    for _ in range(8):
        scheduler.tick()
        if repo.get("jobs", job["id"], "owner-1")["state"] == "running":
            break
    scheduler.cancel("owner-1", job["id"])
    result = run_to_terminal(scheduler, repo, job["id"])

    assert result["state"] == "cancelled"
    assert executor.calls[-2:] == ["cancel", "cleanup"]
    assert provider.delete_calls == 1
    assert result["trusted_plugin_cancel_succeeded"] is True
    assert result["trusted_plugin_cleanup_succeeded"] is True


def test_failed_plugin_hook_does_not_block_confirmed_runtime_delete_or_leak_error():
    repo = Repository(Store("sqlite:///:memory:"))
    job = make_job(repo, "plugin-failed")
    provider = MockColabProvider()
    executor = FakeTrustedPluginExecutor(fail_on_poll=True, fail_cleanup=True)
    scheduler = scheduler_for(repo, executor, provider)

    result = run_to_terminal(scheduler, repo, job["id"])

    assert result["state"] == "failed"
    assert executor.calls[-1] == "cleanup"
    assert provider.delete_calls == 1
    assert repo.get("leases", result["lease_id"], "owner-1")["state"] == "deleted"
    assert "plugin-secret-must-not-leak" not in repr(result)
    assert result.get("trusted_plugin_cleanup_succeeded") is not True
    assert result["trusted_plugin_cleanup_error"] == "trusted_plugin_cleanup_failed"


def test_trusted_plugin_start_and_poll_receive_lease_deadline_in_memory():
    repo = Repository(Store("sqlite:///:memory:"))
    job = repo.create(
        "jobs",
        "owner-1",
        {
            "state": "loading",
            "provider": "mock",
            "model_id": "demo",
            "workflow_key": "demo",
            "workflow_version": 1,
            "params": {},
            "snapshot": {"manifest": {"mock": True}},
        },
        record_id="deadline-job",
    )
    lease = repo.create(
        "leases",
        "owner-1",
        {
            "state": "active",
            "owner_job_id": job["id"],
            "request_id": "request-1",
            "runtime_id": "runtime-1",
            "deadline_epoch": 1234.0,
            "handle": {
                "runtime_name": "mock-runtime-runtime-1",
                "runtime_id": "runtime-1",
                "request_id": "request-1",
                "job_id": job["id"],
                "owner_job_id": job["id"],
                "owner_id": "owner-1",
                "mock": True,
            },
        },
        record_id="deadline-lease",
    )
    job = repo.update("jobs", job["id"], {"lease_id": lease["id"]}, "owner-1")
    executor = FakeTrustedPluginExecutor()
    scheduler = scheduler_for(repo, executor, MockColabProvider())

    running = scheduler._load(job)
    scheduler._run(running)

    assert executor.observed_deadlines == [("start", 1234.0), ("poll", 1234.0)]
