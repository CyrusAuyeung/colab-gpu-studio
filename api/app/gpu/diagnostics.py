"""Bounded, non-identifying hardware evidence reported by the trusted worker."""

import math
import re


def gpu_diagnostics(value):
    if not isinstance(value, dict) or value.get("source") != "runtime_nvidia_smi":
        return None
    devices = value.get("devices")
    captured = value.get("captured_at")
    if (not isinstance(devices, list) or not 1 <= len(devices) <= 8
            or type(captured) not in (int, float) or not math.isfinite(captured) or captured <= 0):
        return None
    clean = []
    for device in devices:
        if not isinstance(device, dict):
            return None
        name, memory = device.get("name"), device.get("memory_mib")
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()+/-]{0,119}", name)
                or type(memory) is not int or not 0 < memory <= 10_000_000):
            return None
        clean.append({"name": name, "memory_mib": memory})
    return {"source": "runtime_nvidia_smi", "captured_at": captured, "devices": clean}
