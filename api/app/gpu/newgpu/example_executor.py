"""Offline, non-Comfy trusted executor plugin example.

This is a deterministic contract fixture for deployments and tests.  It does
not call a GPU, a model service, Comfy, Laya, or the network; its only artifact
is an explicitly labelled JSON fixture produced from the admitted payload.
"""

from __future__ import annotations

import base64
import hashlib
import json

contract_version = "1"
plugin_version = "1.0.0"
_OFFLINE_WEIGHT = b"offline weight bytes"
_OFFLINE_WEIGHT_SHA256 = hashlib.sha256(_OFFLINE_WEIGHT).hexdigest()


class OfflineFixtureExecutor:
    """Small trusted-plugin implementation that exercises preparation and lifecycle hooks."""

    contract_version = contract_version
    plugin_version = plugin_version
    idempotent = True

    @staticmethod
    def _result(
        payload: dict,
        state: str,
        *,
        outputs: list[dict] | None = None,
        preparation: dict | None = None,
    ) -> dict:
        result = {
            "schema": "trusted-executor-output-v1",
            "contract_version": contract_version,
            "execution_id": payload["execution_id"],
            "state": state,
            "cache": payload["cache"],
            "outputs": outputs or [],
            "timings": {},
            "message": "offline fixture only; no GPU, model service, Comfy, Laya, or network was used",
        }
        if preparation is not None:
            result["preparation"] = preparation
        return result

    def prepare(self, payload: dict) -> dict:
        """Verify the tiny local fixture; production plugins must hash real bytes."""
        weights = payload["preparation"]["weights"]
        if weights != [
            {
                "filename": "weights/laya-fixture.bin",
                "revision": "weights-rev-1",
                "sha256": _OFFLINE_WEIGHT_SHA256,
                "size": len(_OFFLINE_WEIGHT),
            }
        ]:
            raise ValueError("offline fixture weight manifest does not match local bytes")
        preparation = payload["preparation"]
        return self._result(
            payload,
            "prepared",
            preparation={
                "cache_namespace": preparation["cache_namespace"],
                "model_revision": preparation["model_revision"],
                "weights_manifest_sha256": preparation["weights_manifest_sha256"],
                "weights": [{**weight, "verified_sha256": weight["sha256"]} for weight in weights],
            },
        )

    def start(self, payload: dict) -> dict:
        self.prepare(payload)
        return self._result(payload, "running")

    def poll(self, payload: dict) -> dict:
        execution_id = payload["execution_id"]
        # This pure fixture is reconstructible from the admitted payload.
        # Real plugins must persist lifecycle state outside the Python object.
        self.prepare(payload)
        artifact = {
            "artifact_type": "trusted-executor-offline-fixture",
            "execution_id": execution_id,
            "offline": True,
            "network_used": False,
            "model_invoked": False,
            "description": "Deterministic JSON contract fixture; not a model result.",
        }
        content = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self._result(
            payload,
            "completed",
            outputs=[
                {
                    "filename": "offline-fixture.json",
                    "mime": "application/json",
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "metadata": {"offline_fixture": True, "model_result": False},
                }
            ],
        )

    def cancel(self, payload: dict) -> dict:
        return self._result(payload, "cancelled")

    def cleanup(self, payload: dict) -> dict:
        return self._result(payload, "cleaned")


def create_plugin() -> OfflineFixtureExecutor:
    """Entrypoint selected by the administrator allowlist."""
    return OfflineFixtureExecutor()
