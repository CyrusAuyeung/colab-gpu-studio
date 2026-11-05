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


## v0.2 本地开发清单协议（未发布、未真实GPU验收）

Lease 可承载多个串行 Execution；Mock 批次复用与120秒智能空闲由本地测试覆盖。真实 Comfy 热模型仍未验收，不代表已支持真实批次运行。

状态探针优先读取 meta.json 的 artifacts 数组，只传 metadata，不读取媒体字节。无 artifacts 的旧记录保留100MiB Base64兼容路径。清单需包含 execution_id、artifact_id、filename、mime、size_bytes、sha256、storage.provider/object_key。MCP 最多100项、1MiB metadata；禁止将令牌或签名URL写入长期清单。

本地 Mock Worker 使用 LocalArtifactStore.begin/append/offset/verify；默认块上限8MiB、对象上限2GiB，上传偏移来自磁盘，跨进程文件锁协调追加/校验。owner、execution与文件名绑定opaque key，防路径穿越和符号链接。verify_manifest忽略调用者的成功声明，重新读取并校验实际文件；调度器只有校验通过才进入上传，commit_output检查媒体头/图片类型并以确定性artifact ID提交一次私有图库资产。下载仍经既有鉴权路径。

支持的清单provider仅local/mock；真实Drive直传尚待适配。旧runtime_worker目前仍可输出旧协议，不宣称真实500MiB视频链路已可用。详见 REAL_GPU_VALIDATION.md 和 OFFLINE_IMPLEMENTATION.md。

## 同Job的Mock attempt重试

新增POST /api/gpu/jobs/{job_id}/attempts，必须带Idempotency-Key。仅failed/cancelled普通Mock任务、关联租约全部deleted才允许。保留snapshot/request_body/resolved_seed和旧Execution；终态数据归档attempt_history。重复同key只返回当前Job，不重复排队。下一次启动增加attempt_number、创建新Execution。GET同路径返回脱敏执行记录。普通/retry仍沿用新Job再次生成兼容接口。

当前attempt新接口只开放Mock，不代表真实Colab重试已验收。系统/工作流冒烟、预热必须原确认入口。usage去重增加execution_id；云端存储仍未接入。

## 服务端清单文件的云端备份接口

DriveStorage.put_file(key, source_path, mime) 接受文件路径，流式SHA256、最多8MiB分块上传，上传会话能力加密存入owner范围内的files记录；进程重建后先查询会话已确认范围再续传。零进度重试有上限。上传完成后下载流式校验长度和SHA256，成功才持久化verified文件记录并清除会话能力。LocalArtifactStore清单在Scheduler上传阶段可传给此接口，verified云端记录与本地私有图库资产关联；原put(bytes)兼容保留。

目前仅以MockTransport和150/500MiB假媒体验证协议。远端runtime_worker CLI/bootstrap尚未注入云端上传适配器；本改动不证明远端Worker大文件已打通，也不代表真实Drive权限/续传/费用已验证。会话失效、并发初始化等边界等待独立审查，不宣称生产就绪。

## Worker CLI 的 MCP 分块传输接入

MCPGPUExecutor启动payload现在包含owner_id和output_transport=mcp-chunks-v1。CLI自动注入描述符写入器，输出文件留在execution目录，仅对文件流式计算SHA256并写入meta.json。服务端poll收到无storage字段的远端清单后，用固定代码按文件索引与偏移读取，每次最多1MiB，结果绑定execution_id和offset；不接受调用方路径。私有LocalArtifactStore持久化偏移，重建executor后可从已有字节续传，完整SHA256校验后才返回本地清单并进入图库提交及现有云端备份接口。旧小文件内联路径仍兼容。

定向验证：新增5项通过（CLI注入及清理缓存凭据、分块中断后重建续传、越界路径拒绝、生成读取器字节准确、CLI→生成poll/chunk代码→私有图库闭环）；旧MCP/Worker相关11项兼容通过。均为本地执行/假工具响应，不证明真实Colab MCP载荷上限、延迟或稳定性。调度器遇传输异常的自动恢复策略仍需接入；目前只证明重建executor直接重试poll可续传。未运行全量。

## 调度器临时断线续传

MCP传输重试耗尽及408/429/500/502/503/504归类为MCPTransient。Scheduler在同execution保持running，持久化poll_retry_count和poll_retry_at，最多允许5轮退避重试，超限进入原有失败清理；鉴权、身份、编码、校验错误直接失败。重建Scheduler/Executor后从数据库退避时间及对象已写偏移续传，成功清零重试字段。新增恢复策略3项及分块相关5项定向通过，旧MCP executor4项兼容通过；未跑全量，未访问真实服务。实际断线续传用例经过Scheduler恢复并进入uploading，不再只是直接调用poll。
