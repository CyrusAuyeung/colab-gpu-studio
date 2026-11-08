from app.job_history import job_timing_details, job_timings, safe_history_value


def test_job_timings_separates_persisted_worker_stages_from_event_stages_without_fabricated_zeroes():
    job = {
        "state": "completed",
        "worker_timings": {"restore_weights": 91.2, "sampling": 8.4},
    }
    events = [
        {"state": "queued", "created_at": "2026-11-08T00:00:00+00:00"},
        {"state": "waiting_for_capacity", "created_at": "2026-11-08T00:00:12+00:00"},
        {"state": "allocating", "created_at": "2026-11-08T00:00:20+00:00"},
        {"state": "loading", "created_at": "2026-11-08T00:00:25+00:00"},
        {"state": "running", "created_at": "2026-11-08T00:01:00+00:00"},
        {"state": "uploading", "created_at": "2026-11-08T00:01:20+00:00"},
        {"state": "cleaning_up", "created_at": "2026-11-08T00:01:25+00:00"},
        {"state": "completed", "created_at": "2026-11-08T00:01:32+00:00"},
    ]

    timings = job_timings(job, events)

    assert timings["queue"] == 20.0
    assert timings["allocate"] == 5.0
    assert timings["upload"] == 5.0
    assert timings["cleanup"] == 7.0
    assert timings["restore_weights"] == 91.2
    assert timings["sampling"] == 8.4
    assert "restore_env" not in timings
    assert "delete" not in timings


def test_timing_details_mark_an_active_stage_and_ignore_events_without_timestamps():
    job = {"state": "running", "stage": "sampling", "worker_timings": {}}
    events = [
        {"state": "queued", "created_at": "2026-11-08T00:00:00Z"},
        {"state": "running", "created_at": "2026-11-08T00:00:10Z"},
        {"state": "running", "created_at": None},
    ]

    details = job_timing_details(job, events, now=25.0)

    assert details["queue"]["seconds"] == 10.0
    assert details["queue"]["source"] == "events"
    assert details["sampling"]["status"] == "in_progress"
    assert details["sampling"]["seconds"] is None
    assert "cleanup" not in details


def test_timing_details_derive_active_lifecycle_stage_from_persisted_events():
    details = job_timing_details(
        {"state": "uploading", "worker_timings": {}},
        [
            {"state": "queued", "created_at": 0},
            {"state": "uploading", "created_at": 10},
        ],
        now=25.0,
    )

    assert details["upload"] == {
        "seconds": 15.0,
        "source": "events",
        "status": "in_progress",
    }


def test_timing_details_never_turn_missing_active_event_time_into_zero():
    details = job_timing_details(
        {"state": "uploading", "worker_timings": {}},
        [{"state": "uploading", "created_at": None}],
    )

    assert details["upload"] == {
        "seconds": None,
        "source": "events",
        "status": "in_progress",
    }


def test_active_event_clock_spans_repeated_states_and_queue_substates():
    details = job_timing_details(
        {"state": "waiting_for_capacity", "stage": "untrusted_stage"},
        [
            {"state": "queued", "created_at": 0},
            {"state": "validating", "created_at": 5},
            {"state": "waiting_for_capacity", "created_at": 10},
            {"state": "waiting_for_capacity", "created_at": 15},
        ],
        now=25.0,
    )

    assert details["queue"]["seconds"] == 25.0
    assert "untrusted_stage" not in details


def test_timing_details_preserve_zero_only_when_worker_explicitly_persisted_it():
    details = job_timing_details(
        {"state": "completed", "worker_timings": {"warm_reuse": 0.0}},
        [],
    )
    assert details["warm_reuse"] == {
        "seconds": 0.0,
        "source": "worker",
        "status": "completed",
    }


def test_safe_history_value_drops_credentials_inside_nested_metadata():
    value = safe_history_value({
        "api_key": "api-secret",
        "client_secret": "client-secret",
        "private_key": "private-secret",
        "metadata": {
            "safe": "kept",
            "credentials": {
                "api_key": "nested-api-secret",
                "private_key": "nested-private-secret",
            },
        },
    })

    assert value == {"metadata": {"safe": "kept"}}
    assert "api-secret" not in repr(value)
    assert "nested-api-secret" not in repr(value)
