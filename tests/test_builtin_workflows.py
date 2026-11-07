from app.db import Store
from app.gpu.registry import Registry
from app.gpu.repository import Repository

EXPECTED = {
    ("moss-image-sft-t2i", "1.0.0"),
    ("krea2-moss-dual-t2i", "1.0.0"),
    ("krea2-moss-dual-t2i", "1.1.1"),
    ("minimax-h3-i2v", "1.1.0"),
    ("minimax-h3-ref2v", "1.4.0"),
}


def test_current_templates_are_seeded_as_untrusted_drafts():
    repo = Repository(Store("sqlite:///:memory:"))
    registry = Registry(repo)
    registry.bootstrap("catalog-owner")
    rows = [r for r in registry.workflows("catalog-owner") if r["model_id"] != "demo-mock"]
    assert {(r["workflow_key"], r["workflow_version"]) for r in rows} == EXPECTED
    for row in rows:
        assert row["state"] == "draft"
        assert row["active"] is False
        assert row["smoke_test_passed"] is False
        assert not row.get("review_approved")
        assert not row["manifest"].get("reviewed")
    real_models = [r for r in registry.models("catalog-owner") if r["model_id"] in {
        "moss-image-sft", "krea2-moss-dual", "minimax-h3",
    }]
    assert len(real_models) == 3
    assert all(not r["enabled"] and not r.get("license_accepted") for r in real_models)
    assert not repo.list("jobs") and not repo.list("leases")


def test_repeated_bootstrap_preserves_existing_drafts_models_and_publication():
    repo = Repository(Store("sqlite:///:memory:"))
    registry = Registry(repo)
    registry.bootstrap("catalog-owner")
    row = registry.get_workflow("catalog-owner", "minimax-h3-ref2v", "1.4.0")
    repo.update("workflows", row["id"], {"state": "published", "active": True}, "catalog-owner")
    model = next(m for m in registry.models("catalog-owner") if m["model_id"] == "minimax-h3")
    repo.update("models", model["id"], {"display_name": "My custom title", "enabled": True}, "catalog-owner")
    before = registry.workflows("catalog-owner"), registry.models("catalog-owner")
    registry.bootstrap("catalog-owner")
    assert (registry.workflows("catalog-owner"), registry.models("catalog-owner")) == before


def test_builtin_records_are_owner_scoped():
    repo = Repository(Store("sqlite:///:memory:"))
    registry = Registry(repo)
    registry.bootstrap("first-owner")
    registry.bootstrap("second-owner")
    first = registry.workflows("first-owner")
    second = registry.workflows("second-owner")
    assert len(first) == len(second) == 6
    assert {r["id"] for r in first}.isdisjoint(r["id"] for r in second)
    assert all(r["owner_id"] == "second-owner" for r in second)


def test_catalog_is_static_valid_and_contains_no_execution_approvals():
    import json
    from pathlib import Path

    from app.gpu import builtin
    from app.review_routes import _review_manifest

    root = Path(builtin.__file__).with_name("templates") / "builtin"
    forbidden = {
        "owner_id", "project_id", "reviewed", "review_approved", "reviewed_by", "reviewed_at",
        "smoke_job_id", "smoke_test_passed", "smoke_test_kind", "access_token", "refresh_token",
        "remote_id", "drive_file_id", "enabled_at", "tested_compute_specs", "license_accepted",
    }

    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    entries = json.loads((root / "catalog.json").read_text())["workflows"]
    assert {(r["workflow_key"], r["version"]) for r in entries} == EXPECTED
    for entry in entries:
        bundle = json.loads((root / entry["file"]).read_text())
        check(bundle)
        manifest, nodes, commit, weights = _review_manifest(bundle)
        assert manifest["version"] == entry["version"]
        assert nodes and len(commit) == 40 and weights
        assert len(bundle["object_info"]) == len({n["class_type"] for n in bundle["api_graph"].values()})


def test_h3_modes_and_vae_choices_bind_without_running_models():
    import json
    from pathlib import Path

    from app.gpu import builtin
    from app.gpu.manifest import bind_parameters, resolve_parameters

    root = Path(builtin.__file__).with_name("templates") / "builtin"
    bundle = json.loads((root / "minimax-h3-ref2v-1.4.0.json").read_text())
    manifest = bundle["manifest"]
    modes = manifest["parameters"]["mode"]
    assert modes["default"] == "fast8"
    assert {o["value"] for o in modes["options"]} == {"fast", "fast8", "refine", "refine8", "hq"}
    vaes = manifest["parameters"]["video_vae"]
    assert len(vaes["options"]) == 2
    for mode in ("fast", "fast8", "refine", "refine8", "hq"):
        for vae in vaes["options"]:
            params = resolve_parameters(manifest, {"ref_1": "reference-asset", "mode": mode, "video_vae": vae["value"], "seed": 42})
            graph = bind_parameters(manifest, bundle["api_graph"], params)
            assert graph["13"]["inputs"]["vae_name"] == vae["value"]
            assert ("34" in graph) is (mode in {"refine", "refine8"})
            assert graph["22"]["inputs"]["steps"] == {"fast": 4, "fast8": 8, "refine": 4, "refine8": 8, "hq": 20}[mode]


def test_builtin_templates_cannot_be_published_without_local_approval():
    import pytest

    repo = Repository(Store("sqlite:///:memory:"))
    registry = Registry(repo)
    registry.bootstrap("catalog-owner")
    for row in registry.workflows("catalog-owner"):
        if row["model_id"] == "demo-mock":
            continue
        registry.validate("catalog-owner", row["id"])
        with pytest.raises(ValueError, match="模型未启用"):
            registry.publish("catalog-owner", row["id"], confirmed=True)
    assert not repo.list("jobs") and not repo.list("leases")


def test_catalog_bootstrap_never_calls_external_services(monkeypatch):
    import httpx

    def reject(*args, **kwargs):
        raise AssertionError("built-in registration must be offline")

    monkeypatch.setattr(httpx.Client, "request", reject)
    registry = Registry(Repository(Store("sqlite:///:memory:")))
    registry.bootstrap("offline-owner")
    assert len(registry.workflows("offline-owner")) == 6


def test_every_bundled_lora_node_is_shipped_or_refused():
    """A bundle may not name a LoRA that its manifest does not bring in.

    1.1.0 chained seven LoRAs while shipping none, so every fresh install burned a Colab
    runtime and then failed with "LoRA 文件没有随任务下发".  The check lives in the bundle
    loader now, and this test keeps the shipped catalog honest.
    """
    from app.gpu import builtin

    for bundle in builtin._bundles():  # noqa: SLF001 - the loader itself is the contract
        manifest = bundle["manifest"]
        weights = manifest["dependencies"]["weight_files"]
        assert not [w for w in weights if w.get("source") == "drive"]
        shipped = {w.get("target", "").removeprefix("loras/") for w in weights}
        for node_id, node in bundle["api_graph"].items():
            if node.get("class_type") == "LoraLoaderModelOnly":
                assert node["inputs"]["lora_name"] in shipped, (
                    f"{bundle['workflow_key']} {manifest['version']} node {node_id} "
                    f"references an unshipped LoRA"
                )


def test_bundle_loader_rejects_unshipped_lora(tmp_path, monkeypatch):
    import json

    from app.gpu import builtin

    bundle = json.loads(
        (builtin._ROOT / "krea2-moss-dual-t2i-1.1.1.json").read_text(encoding="utf-8")  # noqa: SLF001
    )
    bundle["manifest"]["dependencies"]["weight_files"] = [
        w for w in bundle["manifest"]["dependencies"]["weight_files"] if not w["target"].startswith("loras/")
    ]
    (tmp_path / "broken-1.0.0.json").write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "catalog.json").write_text(
        json.dumps(
            {
                "catalog_version": 1,
                "workflows": [
                    {
                        "file": "broken-1.0.0.json",
                        "workflow_key": bundle["workflow_key"],
                        "version": bundle["manifest"]["version"],
                        "model_id": bundle["model_id"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(builtin, "_ROOT", tmp_path)
    builtin._bundles.cache_clear()  # noqa: SLF001
    try:
        import pytest

        with pytest.raises(ValueError, match="未随包下发的 LoRA"):
            builtin._bundles()  # noqa: SLF001
    finally:
        builtin._bundles.cache_clear()  # noqa: SLF001

