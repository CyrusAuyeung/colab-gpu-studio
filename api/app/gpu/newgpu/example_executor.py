"""Offline, non-Comfy trusted executor plugin example.

This is a deterministic contract fixture for deployments and tests.  It does
not call a GPU, a model service, Comfy, Laya, or the network; its only artifact
is an explicitly labelled JSON fixture produced from the admitted payload.
"""

from __future__ import annotations

import base64
import json

contract_version = "1"
plugin_version = "1.0.0"


class OfflineFixtureExecutor:
    """Small trusted-plugin implementation that exercises all four hooks."""

    contract_version = contract_version
    plugin_version = plugin_version
    idempotent = True

    def __init__(self) -> None:
        self._started: set[str] = set()

    @staticmethod
    def _result(payload: dict, state: str, *, outputs: list[dict] | None = None) -> dict:
        return {
            "schema": "trusted-executor-output-v1",
            "contract_version": contract_version,
            "execution_id": payload["execution_id"],
            "state": state,
            "cache": payload["cache"],
            "outputs": outputs or [],
            "timings": {},
            "message": "offline fixture only; no GPU, model service, Comfy, Laya, or network was used",
        }

    def start(self, payload: dict) -> dict:
        self._started.add(payload["execution_id"])
        return self._result(payload, "running")

    def poll(self, payload: dict) -> dict:
        execution_id = payload["execution_id"]
        if execution_id not in self._started:
            return self._result(payload, "failed")
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
        self._started.discard(payload["execution_id"])
        return self._result(payload, "cleaned")


def create_plugin() -> OfflineFixtureExecutor:
    """Entrypoint selected by the administrator allowlist."""
    return OfflineFixtureExecutor()
