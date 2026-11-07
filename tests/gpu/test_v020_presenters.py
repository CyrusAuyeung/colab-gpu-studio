from app.presenters import public_job


def test_job_exposes_stable_instance_history_without_connection_material():
    job = {
        "id": "job-1", "provider": "mock", "state": "completed",
        "account_id": "account-1", "account_alias_snapshot": "主账号",
        "instance_id": "instance-1", "instance_label": "GPU-01",
        "instance_state": "retained_idle", "gpu_reported": None,
        "retained_until": 12345, "selection_reason": "user_specified",
        "handle": {"token": "private"}, "connectionInfo": {"token": "private"},
        "runtime_name": "private-runtime", "encrypted_refresh_token": "private",
    }
    result = public_job(job)
    assert result["instance_id"] == "instance-1"
    assert result["account_alias_snapshot"] == "主账号"
    assert result["instance_state"] == "retained_idle"
    assert result["gpu_reported"] is None
    assert result["retained_until"] == 12345
    assert result["selection_reason"] == "user_specified"
    assert not {"handle", "connectionInfo", "runtime_name", "encrypted_refresh_token"} & result.keys()
