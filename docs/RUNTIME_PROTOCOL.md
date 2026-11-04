# Colab MCP runtime protocol

This project uses the official Google Colab MCP endpoint, not a public Comfy
endpoint and not an in-process execution registry:

`POST https://colaboratory.googleapis.com/mcp`

Every request is JSON-RPC `tools/call` with an owner-bound OAuth bearer token
from `GoogleService(Repository, owner_id).access_token("colab")`.

## Start

Before the scheduler calls the executor, the lease and its UUID4 runtime ID
are durable in the repository. `MCPGPUExecutor.start(handle, job,
execution_id)` validates that the handle belongs to the owner and calls:

```json
{"name":"create_session","arguments":{
  "parent":"runtimes/<runtime-id>",
  "sessionId":"<stable job id>"
}}
```

It then calls `execute_code` using the returned full session name and the
scheduler's fixed UUID4 execution ID:

```json
{"name":"execute_code","arguments":{
  "session":"runtimes/<runtime-id>/sessions/<session-id>",
  "executionId":"<execution-id>",
  "code":"<checked-in fixed bootstrap>"
}}
```

The bootstrap is not a user program. It contains the checked-in
`runtime_worker.py` as a trusted payload and transports the immutable job
snapshot as base64 JSON. It creates `/content/ai-studio/<execution-id>/`,
uses an exclusive `O_CREAT|O_EXCL` lock (`Path.open("x")`), writes
`runtime_worker.py` and `payload.json`, writes `marker.json`, and starts the
worker only as an argument list:

```python
Popen([sys.executable, str(worker), str(payload)], shell=False, ...)
```

A marker, lock, or status file means that a repeated `start` is idempotent; it
never starts a second worker. If the JSON-RPC response is lost after Colab
accepted the call, the scheduler persists the original execution ID and
recovers it with `poll` rather than generating a new execution.

The client accepts both ordinary JSON and `text/event-stream` JSON-RPC
responses. It never logs the bearer token or upstream response body.

## Recovery poll

`poll` creates a fresh MCP session and uses a fresh UUID4 **probe** execution
ID. The probe code is read-only: it reads `status.json`, `meta.json`, and
worker output files under the fixed execution directory. It does not write a
marker, launch a process, or regenerate a job. Output transfer is base64 and
bounded to 100 MiB total. Missing status is reported as running/unknown; it is
never treated as permission to restart.

Worker states are mapped as follows:

- `starting`, `reserved`, `running`, or missing status: `running`
- `completed`: durable output records are returned to the scheduler
- `failed`: safe `error_code`/`error_message`, with no fabricated output

The scheduler must persist outputs to backend storage and verify them before
allowing runtime cleanup and the terminal `completed` state.

## Trusted worker behavior

The worker accepts only the immutable `job.snapshot`:

- a published, reviewed manifest is required;
- custom nodes must match the fixed reviewed repository/commit allowlist in `runtime_worker.py`;
- ComfyUI is cloned from the fixed official repository and checked out at a
  40-hex commit; an existing checkout at another commit fails closed;
- requirements and `huggingface_hub` are installed with argument-list
  subprocesses and bounded download timeouts;
- every weight has an allowlisted Hugging Face `repo_id`, filename, 40-hex
  revision, 64-hex SHA-256, and a target confined below `ComfyUI/models`;
- downloads are hash-verified before and after placement;
- the bound graph is checked against the manifest node allowlist;
- Comfy is started only on `127.0.0.1:8188`, queued through `/prompt`, and
  polled through `/history`; output bytes are bounded and copied to the
  execution directory;
- missing reviewed model weights, unsafe paths, failed hash checks, startup
  failures, and execution errors are hard failures. No placeholder image is
  produced.

All worker status is written atomically to `status.json`; `meta.json` records
only output metadata and relative paths. Logs are kept in the execution
folder and do not include OAuth or model credentials.

## CUDA system smoke

A system smoke is a separate trusted path. It runs only when
`snapshot.system_smoke is true` **and** `snapshot.manifest.executor ==
"cuda-smoke"`. The manifest alone cannot enable it. The worker executes real
`nvidia-smi` and `torch.cuda` probes and fails if a CUDA device is unavailable.
The only output is a diagnostic JSON asset:

```json
{"filename":"cuda-smoke.json","mime":"application/json","content":"..."}
```

The parent API must gate real smoke creation behind its confirmed admin
request, budget, lease, and default-disabled configuration. A smoke diagnostic
is not an image and must never be presented as generated media.

## Safety boundaries

No runtime token is placed in the repository or in the job snapshot. The
executor does not accept arbitrary Python, shell, URLs, model revisions, or
unreviewed custom nodes from a caller. Runtime repositories are limited to the
fixed ComfyUI repository and explicitly reviewed pinned dependencies; model downloads use the Hugging Face client with
pinned revisions and hashes. The 600-second network/download bounds are
failure bounds, not a restart policy: timeout marks the worker failed and the
next poll observes that durable state.
