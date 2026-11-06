"""Colab GPU Studio client example. Default Mock; --real permits paid submission."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import httpx

TERMINAL = {"completed", "failed", "cancelled", "partial"}


def api(client, method, path, **kwargs):
    retryable = method == "GET" or "Idempotency-Key" in kwargs.get("headers", {})
    for attempt in range(3):
        try:
            response = client.request(method, path, **kwargs)
        except httpx.TransportError:
            if not retryable or attempt == 2:
                raise RuntimeError("网络失败；保留状态文件，恢复时沿用同一幂等键") from None
            time.sleep(attempt + 1)
            continue
        if response.status_code in {429, 502, 503, 504} and retryable and attempt < 2:
            time.sleep(attempt + 1)
            continue
        try:
            body = response.json()
        except ValueError:
            raise RuntimeError(f"HTTP {response.status_code}: 非 JSON 响应，请检查后端地址") from None
        if response.is_error or body.get("error"):
            error = body.get("error") or body
            raise RuntimeError(f"HTTP {response.status_code} {error.get('code', 'ERROR')}: "
                               f"{error.get('message', '请求失败')} request_id={body.get('request_id', '')}")
        return body["data"]
    raise RuntimeError("重试次数已用尽")


def save_state(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def submit(client, state_path, base_url, workflow_key, params, *, project_id=None,
           reference=None, real=False, max_runtime=60):
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["base_url"] != base_url:
            raise RuntimeError("状态文件属于其他后端；不要跨站重放任务")
        saved_real = state["body"]["jobs"][0]["execution_policy"]["provider"] == "colab"
        if saved_real and not real:
            raise RuntimeError("恢复真实任务也必须显式传入 --real")
    else:
        models = api(client, "GET", "/api/gpu/models")
        workflows = api(client, "GET", "/api/gpu/workflows")
        candidates = [w for w in workflows if w["workflow_key"] == workflow_key
                      and w.get("state") == "published"]
        active = [w for w in candidates if w.get("active")]
        if len(active) == 1:
            workflow = active[0]
        elif len(candidates) == 1:
            workflow = candidates[0]
        else:
            raise RuntimeError("找不到唯一已发布版本；请检查工作流列表和 active 字段")
        model = next((m for m in models if m["model_id"] == workflow["model_id"]), None)
        if not model or not model.get("enabled") or bool(model.get("is_mock")) != (not real):
            raise RuntimeError("模型不可用，或 Provider 与模型类型不匹配")
        api(client, "GET", f"/api/gpu/workflows/{workflow['id']}/schema")
        if not project_id:
            project_id = api(client, "POST", "/api/gpu/projects", json={"title": "API 调用示例"})["id"]
        asset_ids = []
        if reference:
            with reference.open("rb") as file:
                asset = api(client, "POST", "/api/assets", data={"project_id": project_id},
                            files={"file": (reference.name, file)})
            asset_ids.append(asset["id"])
            params = {**params, "ref_1": asset["id"]}
        job = {"project_id": project_id, "model_id": workflow["model_id"],
               "workflow_key": workflow["workflow_key"],
               "workflow_version": workflow["workflow_version"], "params": params,
               "asset_ids": asset_ids, "execution_policy": {
                   "provider": "colab" if real else "mock", "shutdown_after_job": True,
                   "max_runtime_minutes": max_runtime, "idle_grace_minutes": 0}}
        state = {"base_url": base_url, "idempotency_key": str(uuid.uuid4()),
                 "body": {"mode": "batch", "idle_seconds": 0, "jobs": [job]}}
        save_state(state_path, state)
    if not state.get("batch_id"):
        batch = api(client, "POST", "/api/gpu/batches", json=state["body"],
                    headers={"Idempotency-Key": state["idempotency_key"]})
        state["batch_id"] = batch["id"]
        state["job_ids"] = batch["job_ids"]
        save_state(state_path, state)
    return state


def wait_and_download(client, state, output_dir, *, timeout=7200, interval=5):
    deadline = time.monotonic() + timeout
    while True:
        batch = api(client, "GET", f"/api/gpu/batches/{state['batch_id']}")
        print(f"batch={batch['id']} state={batch['state']} "
              f"finished={batch.get('finished_jobs', 0)}/{batch['total_jobs']}", flush=True)
        if batch["state"] in TERMINAL:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("客户端等待超时；任务未自动取消，可用同一状态文件恢复查询")
        time.sleep(interval)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for job in batch["jobs"]:
        if job["state"] != "completed":
            print(f"job={job['id']} state={job['state']} "
                  f"error={job.get('error_code')} {job.get('error_message', '')}")
            continue
        assets = api(client, "GET", f"/api/gpu/jobs/{job['id']}/outputs")
        if not assets:
            raise RuntimeError("任务完成但产物列表为空，请检查任务事件")
        for asset in assets:
            target = output_dir / (asset["id"] + "-" + Path(asset["filename"]).name)
            partial = target.with_suffix(target.suffix + ".partial")
            checksum = hashlib.sha256()
            size = 0
            with client.stream("GET", f"/api/assets/{asset['id']}/download") as response:
                if response.status_code != 200:
                    raise RuntimeError(f"下载失败：HTTP {response.status_code}")
                with partial.open("wb") as file:
                    for chunk in response.iter_bytes():
                        file.write(chunk)
                        checksum.update(chunk)
                        size += len(chunk)
            if size != asset["size"] or checksum.hexdigest() != asset["sha256"]:
                raise RuntimeError("下载校验失败；仅保留 .partial，不作为完整结果使用")
            partial.replace(target)
            paths.append(str(target))
            print(f"saved={target} mock={job.get('mock')} sha256={checksum.hexdigest()}")
    if batch["state"] != "completed":
        raise RuntimeError("批次未全部成功；已有成功产物已保存，失败任务请检查 events")
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("STUDIO_BASE_URL", "http://127.0.0.1:8788"))
    parser.add_argument("--workflow-key", default="demo-mock")
    parser.add_argument("--project-id")
    parser.add_argument("--params", type=Path, help="参数 JSON 文件，仅包含 params 对象")
    parser.add_argument("--reference", type=Path, help="H3 Ref2V 参考图，自动填充 ref_1")
    parser.add_argument("--real", action="store_true", help="明确允许提交真实付费任务")
    parser.add_argument("--max-runtime", type=int, default=60)
    parser.add_argument("--wait-seconds", type=int, default=7200)
    parser.add_argument("--state", type=Path, default=Path("studio-api-state.json"))
    parser.add_argument("--output", type=Path, default=Path("studio-api-outputs"))
    args = parser.parse_args()
    token = os.getenv("STUDIO_API_TOKEN", "")
    if not token:
        parser.error("请在环境变量 STUDIO_API_TOKEN 中设置凭据")
    params = json.loads(args.params.read_text(encoding="utf-8")) if args.params else {}
    if not isinstance(params, dict):
        parser.error("参数文件必须是 JSON 对象")
    base = args.base_url.rstrip("/")
    try:
        with httpx.Client(base_url=base, headers={"Authorization": "Bearer " + token},
                          timeout=60, follow_redirects=False) as client:
            state = submit(client, args.state, base, args.workflow_key, params,
                           project_id=args.project_id, reference=args.reference, real=args.real,
                           max_runtime=args.max_runtime)
            print(f"batch_id={state['batch_id']} job_ids={state['job_ids']}", flush=True)
            wait_and_download(client, state, args.output, timeout=args.wait_seconds)
    except RuntimeError as exc:
        import sys
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError):
        import sys
        print("调用失败：检查文件格式、路径、后端版本及任务事件。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
