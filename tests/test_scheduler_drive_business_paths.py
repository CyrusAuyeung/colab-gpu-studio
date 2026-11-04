"""Scheduler must use Drive's canonical business roots before spending GPU."""

import hashlib

from app.config import settings
from app.gpu.drive import DriveStorage
from app.gpu.scheduler import Scheduler

from tests.gpu.test_scheduler import MemoryStore, drive, make_job


def test_scheduler_outputs_use_valid_drive_business_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    store = MemoryStore()
    job = make_job(store)
    seen = []

    class CheckedStorage:
        def put(self, key, content, mime):
            # Reuse actual Drive key validation, with no credentials or network.
            normalized = DriveStorage._normalize_key(None, key)
            seen.append(normalized)
            return {
                "id": "drive-" + str(len(seen)),
                "sha256": hashlib.sha256(content).hexdigest(),
                "name": key.split("/")[-1],
            }

        def verify(self, record, content):
            return record["sha256"] == hashlib.sha256(content).hexdigest()

    scheduler = Scheduler(store, storage_factory=lambda _: CheckedStorage())
    result = drive(scheduler, job["id"])
    assert result["state"] == "completed", result
    assert len(seen) == 2
    assert len(result["output_asset_ids"]) == 1
    assert len(result["persisted_outputs"]) == 2
    assert all(k.startswith("outputs/") for k in seen)
