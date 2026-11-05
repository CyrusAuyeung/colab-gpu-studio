"""Allowlisted public representations; never expose runtime connection handles."""

def public_asset(asset):
    return {k: v for k, v in asset.items() if k not in {"storage_key", "owner_id"}}


def public_job(job):
    # Only explicit business fields are exposed. Runtime handles/connectionInfo never leave the server.
    keys = {
        "id",
        "project_id",
        "model_id",
        "workflow_key",
        "workflow_version",
        "state",
        "provider",
        "params",
        "resolved_seed",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "estimated_units",
        "actual_units",
        "simulated_units",
        "unit_rate",
        "max_runtime_minutes",
        "output_asset_ids",
        "cancel_requested",
        "progress",
        "runtime_deleted",
        "execution_id",
        "attempt_number",
        "batch_id",
        "queue_order",
        "stage",
        "prep",
        "for_job_id",
        "prep_job_id",
        "awaiting_prep",
    }
    result = {k: v for k, v in job.items() if k in keys}
    result["job_id"] = job["id"]
    result["mock"] = job.get("provider") == "mock"
    result["resolved_params"] = (job.get("snapshot") or {}).get("resolved_params", {})
    return result


