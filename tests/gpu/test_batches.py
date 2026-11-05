from app.db import Store
from app.gpu.repository import Repository


def test_batch_atomic_idempotency_and_conflict():
    repo = Repository(Store("sqlite:///:memory:"))
    jobs = [
        {
            "project_id": "p",
            "execution_policy": {"provider": "mock"},
            "snapshot": {"manifest": {"mock": True}},
        }
    ] * 3
    batch, created = repo.create_batch("u", jobs, "key", {"mode": "batch"})
    assert created and len(repo.list("jobs", "u")) == 3
    again, created = repo.create_batch("u", jobs, "key", {"mode": "batch"})
    assert not created and again["id"] == batch["id"]
    import pytest

    with pytest.raises(ValueError, match="conflict"):
        repo.create_batch("u", jobs[:1], "key", {"mode": "batch"})
    assert len(repo.list("jobs", "u")) == 3


def test_thirty_jobs_reuse_one_lease():
    from app.gpu.compute import MockColabProvider, MockGPUExecutor
    from app.gpu.scheduler import Scheduler

    repo = Repository(Store("sqlite:///:memory:"))
    jobs = [
        {
            "project_id": "p",
            "snapshot": {"manifest": {"mock": True}},
            "execution_policy": {"provider": "mock"},
        }
        for _ in range(30)
    ]
    batch, _ = repo.create_batch("u", jobs, "thirty", {"mode": "batch"})
    provider, executor = MockColabProvider(), MockGPUExecutor()
    scheduler = Scheduler(repo, provider_factory=lambda j: provider, executor_factory=lambda j: executor)
    for _ in range(400):
        scheduler.tick()
        if all(j["state"] == "completed" for j in repo.list("jobs", "u")):
            break
    assert [j["state"] for j in repo.list("jobs", "u")] == ["completed"] * 30
    assert provider.create_calls == 1
    assert provider.delete_calls == 1
    assert len(executor.started) == 30
    assert executor.max_concurrent == 1
    assert len(repo.list("executions", "u")) == 30


def setup_batch(count=3, mode="batch"):
    from app.gpu.compute import MockColabProvider, MockGPUExecutor
    from app.gpu.scheduler import Scheduler

    repo = Repository(Store("sqlite:///:memory:"))
    jobs = [
        {
            "project_id": "p",
            "snapshot": {"manifest": {"mock": True}},
            "execution_policy": {"provider": "mock"},
        }
        for _ in range(count)
    ]
    batch, _ = repo.create_batch("u", jobs, "batch", {"mode": mode, "idle_seconds": 120})
    provider, executor = MockColabProvider(), MockGPUExecutor()
    scheduler = Scheduler(repo, provider_factory=lambda j: provider, executor_factory=lambda j: executor)
    return repo, batch, provider, executor, scheduler


def finish(repo, scheduler, count=3):
    for _ in range(count * 16):
        scheduler.tick()
        if all(j["state"] in {"completed", "failed", "cancelled"} for j in repo.list("jobs", "u")):
            break


def test_save_policy_releases_when_compatible_queue_empty():
    repo, batch, provider, executor, scheduler = setup_batch(mode="save")
    finish(repo, scheduler)
    assert provider.create_calls == 1
    assert provider.delete_calls == 1


def test_cancel_transferred_lease_does_not_hold_queue():
    repo, batch, provider, executor, scheduler = setup_batch()
    for _ in range(30):
        scheduler.tick()
        rows = repo.list("jobs", "u")
        if sum(j["state"] == "completed" for j in rows) == 1:
            break
    transferred = next(j for j in rows if j.get("lease_id") and j["state"] != "completed")
    scheduler.cancel("u", transferred["id"])
    finish(repo, scheduler)
    assert all(j["state"] in {"completed", "cancelled"} for j in repo.list("jobs", "u"))
    assert all(lease["state"] == "deleted" for lease in repo.list("leases", "u"))


def test_prompt_and_seed_do_not_invalidate_environment_signature():
    repo = Repository(Store("sqlite:///:memory:"))
    jobs = [
        {
            "project_id": "p",
            "execution_policy": {"provider": "mock"},
            "snapshot": {
                "manifest": {"mock": True, "model_sha256": "a" * 64},
                "resolved_params": {"prompt": str(i), "seed": i},
                "bound_graph": {"prompt": str(i)},
            },
        }
        for i in range(2)
    ]
    repo.create_batch("u", jobs, "signature", {"mode": "batch"})
    assert len({j["compatibility_signature"] for j in repo.list("jobs", "u")}) == 1


def test_smart_idle_reuses_and_expires_with_fake_clock():
    repo, batch, provider, executor, scheduler = setup_batch(count=1, mode="smart")
    now = [1000.0]
    scheduler.clock = lambda: now[0]
    finish(repo, scheduler, 1)
    lease = repo.list("leases", "u")[0]
    assert lease["state"] == "idle"
    assert provider.delete_calls == 0
    now[0] += 60
    new = [
        {
            "project_id": "p",
            "snapshot": {"manifest": {"mock": True}},
            "execution_policy": {"provider": "mock"},
        }
    ]
    repo.create_batch("u", new, "second-smart", {"mode": "smart", "idle_seconds": 120})
    finish(repo, scheduler, 2)
    assert provider.create_calls == 1
    assert len(executor.started) == 2
    now[0] += 121
    scheduler.watchdog()
    assert all(lease["state"] == "deleted" for lease in repo.list("leases", "u"))
    assert provider.delete_calls == 1


def test_incompatible_versions_never_share_lease():
    from app.gpu.compute import MockColabProvider, MockGPUExecutor
    from app.gpu.scheduler import Scheduler

    repo = Repository(Store("sqlite:///:memory:"))
    jobs = [
        {
            "project_id": "p",
            "workflow_version": v,
            "snapshot": {"manifest": {"mock": True}},
            "execution_policy": {"provider": "mock"},
        }
        for v in (1, 2, 1)
    ]
    repo.create_batch("u", jobs, "mixed", {"mode": "batch"})
    p, e = MockColabProvider(), MockGPUExecutor()
    scheduler = Scheduler(repo, provider_factory=lambda j: p, executor_factory=lambda j: e)
    finish(repo, scheduler)
    assert len(e.started) == 3 and p.create_calls == 3 and p.delete_calls == 3


def test_hundred_jobs_cancel_ten_preserves_ninety_outputs():
    repo, batch, provider, executor, scheduler = setup_batch(count=100)
    rows = sorted(repo.list("jobs", "u"), key=lambda job: job["queue_order"])
    for job in rows[50:60]:
        scheduler.cancel("u", job["id"])
    finish(repo, scheduler, 100)
    rows = repo.list("jobs", "u")
    assert sum(job["state"] == "completed" for job in rows) == 90
    assert sum(job["state"] == "cancelled" for job in rows) == 10
    assert len(executor.started) == 90
    assert executor.max_concurrent == 1
    assert provider.create_calls == 1 and provider.delete_calls == 1
    assert len(repo.store.list("assets", "u")) == 90


def test_handoff_is_atomic_when_repeated():
    repo, batch, provider, executor, scheduler = setup_batch(count=2)
    for _ in range(20):
        scheduler.tick()
        rows = sorted(repo.list("jobs", "u"), key=lambda job: job["queue_order"])
        if rows[0]["state"] == "cleaning_up":
            break
    job, following = rows
    lease = repo.list("leases", "u")[0]
    assert repo.handoff_lease("u", job["id"], following["id"], lease["id"], scheduler.clock())
    assert not repo.handoff_lease("u", job["id"], following["id"], lease["id"], scheduler.clock())
    assert repo.get("jobs", job["id"], "u")["state"] == "completed"
    assert repo.get("leases", lease["id"], "u")["owner_job_id"] == following["id"]
    finish(repo, scheduler, 2)
    assert len(executor.started) == 2 and provider.create_calls == 1


def test_final_execution_record_matches_job_terminal_state():
    repo,batch,provider,executor,scheduler=setup_batch(count=1)
    finish(repo,scheduler,1)
    job=repo.list('jobs','u')[0]
    execution=repo.get('executions',job['execution_id'],'u')
    assert execution['state']==job['state']=='completed'
    assert execution.get('finished_at') is not None


def test_batch_terminal_summary_is_persisted():
    repo,batch,provider,executor,scheduler=setup_batch(count=2)
    finish(repo,scheduler,2)
    persisted=repo.get('batches',batch['id'],'u')
    assert persisted['state']=='completed'
    assert persisted['finished_jobs']==2


def test_separate_repositories_concurrent_batch_idempotency(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    url='sqlite:///'+str(tmp_path/'race.db')
    repositories=[Repository(Store(url)),Repository(Store(url))]
    barrier=Barrier(2)
    jobs=[{'project_id':'p','execution_policy':{'provider':'mock'}}]
    def create(index):
        barrier.wait()
        return repositories[index].create_batch('u',jobs,'same-key',{'mode':'batch'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(create,range(2)))
    assert results[0][0]['id']==results[1][0]['id']
    assert sorted(result[1] for result in results)==[False,True]
    assert len(repositories[0].list('jobs','u'))==1
