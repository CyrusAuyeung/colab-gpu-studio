# Colab GPU Studio v0.2.0 —— 完整版本实施规划与 Agent 执行规格

> 文档性质：**单次完整版本迭代规格 / 一次性交付**。不是路线图，不分“第一阶段/第二阶段”，不以占位按钮、Mock 假成功、仅写接口或“待下一版实现”替代功能。
>
> 基线：2026-11-07 所核对的 `TizenryA/colab-gpu-studio`，`main` 的项目说明标为 `0.1.31-dev.8`。实施前必须重新核对 HEAD 与未提交改动，以最新生产分支为基础，禁止覆盖开发者未提交工作。
>
> 目标版本：`v0.2.0`（建议命名，可依仓库版本规则调整）。部署、迁移、回退、接口、前端、测试、文档必须匹配同一个发布版本。

## 0. 一句话目标与不可妥协的交付范围

把现有“提交任务 → 申请 Colab → 执行 → 回收”拓展成一个完整的**手机优先 GPU Studio**：同一个后台能管理多个经过授权的 Google 账号连接；按账号归属与策略选择运行环境；每个任务在分配后都能看到自己正在使用哪台真实机器；生成前或任务执行过程中可以**保留这台实例**；实例在任务结束后成为可管理的独立对象，支持继续运行兼容任务、明确停止、超时释放与重启恢复；提供依托已有受审查工作流的移动端 ComfyUI 工作台；费用、失败状态、身份和数据全程可追踪。

**本版必须同时交付：**

- 多 Google OAuth 连接/账号目录、账号启停、状态、身份隔离、明确的账号选择与合规自动选择（不是用多个账号规避 Google 使用限制）。
- 实例中心、任务→实例稳定绑定、实例→任务历史、运行时状态、GPU 规格与在线状态、保留/取消保留、手动释放与回收确认。
- `save / batch / smart / retain` 四种运行生命周期策略在后端正确落地；真实 GPU 的模式只能开放已验证且受预算保护的行为，禁止前端假装可用。
- 支持任务提交前选账号/运行中的实例，支持“启动本次任务并保留机器”，支持运行中开启保留，支持已保留实例继续接兼容任务。
- 后端定时调度、分配原子性、并发锁、设备归属、异常恢复、失联观察、Watchdog 和预算硬上限。
- 受控的手机端 Comfy 工作台：运行中的实例、工作流参数/图结构、提交、排队、日志、预览、产物历史、模型热复用状态。不得通过不受控公网端口、任意反向代理或泄露 Colab connectionInfo 来“实现”。
- 配套后端接口、类型定义、迁移、测试、用户文档、发布记录，保持旧任务与旧作品可读，默认 Mock 环境零真实 GPU 消费。

**不是本版交付成功的情况：** 仅新增 UI Switch、仅给 Job 加 `runtime_id` 字段、仅写个轮询指针、仅创建了账号表却所有 API 仍用默认账号、保留模式被 watchdog 立刻删除、未真实验证的功能被描述为已验证、把原生 ComfyUI 公网暴露，或增加界面但不能从后端重启恢复。

## 1. 事实基线：现有工程与具体改造点

项目是 React/TypeScript 手机端 + FastAPI + SQLAlchemy Repository + worker/watchdog + Google OAuth + Colab REST/MCP；已存在 `gpu_jobs`、`gpu_batches`、`gpu_executions`、`gpu_leases`、`gpu_credentials`、Usage、Drive 缓存与 Mock；批次租约交接、同机环境签名与 `warm_reuse` 已有实现。请保留并改造，而不是重新发明一套后台。

| 源文件 | 当前事实与本版必须变更 |
|---|---|
| `api/app/gpu/google.py` | `_credential(purpose)` 以 owner+purpose 取单槽；OAuth callback 可能更新旧凭据。改为可指向稳定 `account_id` 的独立凭据存储。 |
| `api/app/gpu/colab.py`, `api/app/gpu/mcp.py` | Provider/Executor 根据 owner 获取 Google token；改为以经过授权的 `account_id` 选择对应令牌，不能靠请求任意字符串冒用其他账号。 |
| `api/app/gpu/scheduler.py` | `_has_capacity_blocker()` 跨租约阻塞；Lease 主要挂在 `owner_job_id`；`_cleanup()` 和 watchdog 随任务回收；增加账号范围容量控制、实例级生命周期、保留判定与安全释放。 |
| `api/app/gpu/repository.py` | 已有租约原子交接及重试事务；新增实例实体、账号实体与原子分配，保留已有记录和迁移兼容。 |
| `api/app/admission.py` | 明确拒绝 `shutdown_after_job=false`、`idle_grace_minutes!=0`；重新定义策略白名单与预算/归属验证。 |
| `api/app/job_schemas.py` | Policy 不含 `account_id/instance_id/retention` 等字段；扩展并保留旧请求含义。 |
| `api/app/batch_routes.py` | 真实 batch 只能 `batch`；调整为遵循统一策略与实例不变量。`/leases` 是脱敏基础接口，可继续兼容。 |
| `api/app/presenters.py` | Job 返回字段白名单没有 `instance_id`、实例显示标签；增加安全字段而不回传 handle/token。 |
| `web/src/App.tsx` | `submitJob()` 将 `shutdown_after_job:true` 写死，真实多张强制 batch；统一提交策略、账号/实例选择和错误反馈。 |
| `web/src/pages/CreatePage.tsx` | 运行设置只有三个生命周期选项；加入保留与机器选择。 |
| `web/src/pages/JobsPage.tsx` | 卡片还没有机号、账号和保留操作；显示实例关联及状态。 |
| `web/src/pages/ComputePage.tsx` | 当前主要显示总量与停止全部任务；加入在线实例中心、开关机/续用及费用归属。 |
| `web/src/pages/SettingsPage.tsx` | 新增 Google 账号目录与账号配置，不在其他页面复制一套设置入口。 |
| `web/src/lib/gpu-api.ts` | 更新 TypeScript 类型与强类型 API；所有关键操作支持幂等键和明确状态码。 |
| `api/app/gpu/runtime_worker.py` | 目前 Comfy 可能按任务重新启动；实现可信的同实例服务管理与热复用证明，不允许未审查任意代码。 |
| `migrations/versions/`, `tests/gpu/`, `docs/` | 增量迁移、回归、完整验收与运行文档。 |

## 2. 核心领域模型：账号、机器、任务必须分开

### 2.1 GoogleAccount（账号）

- 代表一个由当前站内用户真实授权的 Google 身份，而不是一个“余额桶”；业务主键 `account_id` 为 UUID，不能用数组下标或邮箱字符串做外键。
- 账号别名由用户设置（例如“主账号”“实验账号”），默认展示别名；邮件地址只有在明确取得身份识别许可后才能展示且默认脱敏，未授权身份 scope 时不伪造邮箱。
- 对 Colab 和 Drive 各有独立的授权状态、scope、到期时间和加密 token；支持同一 Google 身份按用途分授权，不得静默借用另一个账号的 Drive。
- 可用状态：`connected / needs_reauth / disabled / error`；额外可见探测时间、活动实例数、允许的工作流/项目、预算和最近失败原因（脱敏）。
- 删除或解除授权时，先检查其正在运行的实例和任务；有活动资源时阻止直接删除或进入“先安全停机再解除”确认流。保留历史任务的匿名账号 ID/别名快照。

### 2.2 GpuInstance（独立机器）

- `instance_id` 为本站稳定 UUID；对应一个具体的外部 `runtime_name`（敏感内部字段），另有 `lease_id`、`account_id`、`provider`、`runtime_spec_requested`、`runtime_spec_reported`、`gpu_reported`、`state`、`current_job_id`、`created_at`、`last_seen_at`。
- 记录 `retention_mode`、`retain_requested_at`、`idle_deadline_at`、`hard_deadline_at`、`manual_stop_requested`、`cleanup_retry_at`、`last_error_code`、`generation`。外部 runtime 名与别名分离；后端禁用裸 handle 公网输出。
- 实例是存活资源，任务只是它执行过的工作。Job 结束**不得让实例关联消失**；必须能从历史 Job 追溯当时的 `instance_id`、账号别名快照和报告卡型。
- 独立状态：`requested → allocating → ready → busy → retained_idle → releasing → deleted`；另有 `unknown / cleanup_pending / quarantined`。禁止把 `unknown` 显示成已关闭，也不能因读取失败自动启动第二台替代。
- 保留指的是“在本服务允许的期限内不主动销毁”，不是保证 Colab 永久在线；Google 自行回收后必须显示真实失效。

### 2.3 GpuJob / GpuExecution / GpuLease

- Job 创建时可有 `requested_account_id`, `requested_instance_id`；正式原子分配后固化 `account_id`, `instance_id`, `lease_id`，在当前 attempt 内不可随意更换。
- Execution 存储 `instance_id`、`account_id`、`execution_id`、稳定 worker 标记；重试应该成为新的 attempt/Execution，不覆盖旧证据。
- Lease 继续作为底层资源租约和去重/释放凭证；由 Instance 作为生命周期主体管理，而非“任务完成=自动失去所有权”。保留已有 handle retarget 的身份约束，调整为分别验证资源归属与当前执行权限。
- `GPUInstance` 独占串行执行兼容 Job；同一 `instance_id` 在任一时刻最多一个主动执行的 Job；受控工作台操作与长耗时采样不得并发破坏状态。
- 资源、授权凭据、任务和文件均限制在同一 owner；如果将来实现多人租户，不允许当前版本跨 owner 混用。

### 2.4 数据库建议

新增 `gpu_accounts`、`gpu_instances`、`gpu_instance_usage_segments`（或等价独立机时计费表）。为现有 `gpu_credentials` 引入 `account_id`，给 `gpu_jobs/gpu_executions/gpu_leases` 补充 `account_id/instance_id`。保留 `idempotency_key` 唯一性，实例使用 DB 级别唯一/部分唯一索引或锁防止并发抢占；迁移的约束必须兼容 SQLite 和 PostgreSQL。维护 `version`/CAS 语义，防多 worker 的竞争更新。

旧单账号凭据需要安全地挂载为“默认历史账号”，不强迫用户重新授权，也不能让历史记录的 owner_id 丢失。历史 Lease 在迁移时构造对应实例外壳，若无法判定远端状态则设为 `unknown`，**绝不假定已经释放**。实现 forward 迁移、备份校验和回退说明。任何已保留的在线实例在回退前都必须先安全停止或确认收尾，避免老版 watchdog 漏管。

## 3. 账号选择与轮询规则（合规、可解释）

### 3.1 在创作页选择

账号选项：`自动选择（仅限当前 owner 授权并允许的账号）` / `指定账号`；实例选项：`自动分配` / `继续使用某台在线且兼容的机器`。如果用户指定 `instance_id`，则该实例账号为唯一有效账号，不得把任务偷偷路由到另一个身份。

### 3.2 自动选择算法

1. 检查 Job 的 owner、项目、工作流审核、资源规格、账号授权和预算；任何未满足者直接明确报错/排队等待，不绕过。
2. 如指定机器，校验同 owner、同账号、仍在线、规格匹配、空闲可锁定、剩余时长足够；失败则返回可理解原因，不静默分配其他机。
3. 如未指定机器，优先选当前账号范围内可复用的兼容实例，节约启动与模型恢复开销。
4. 否则从**用户已明确允许分配、满足配额和权限的账号集合**中按持久化 Round Robin 游标选择；在同优先级时可参考现有负载，不能根据伪造余额或不可用的后台信息调度。
5. 选择与实例占用在数据库事务内原子完成；持久记录 `selection_reason`（复用、用户指定、自动轮询）和 `account_id`，创建或附加实例后再进入 Provider I/O。
6. 各账号的运行实例容量、预算、队列、公平游标、错误退避独立；单账号错误不能无故阻塞别的、合法的账号。

**合规硬约束：** Google Colab 禁止通过多个账号绕过资源访问/用量限制。因此本功能是面向合法授权主体、明确业务隔离与选择的多账号连接管理；**不得**实现“账号 A CU 不足或超限→自动换 B 继续”“批量开户/批量刷 token”“故意在多个账号间切分同一受限负载以规避限制”等策略。遇到 `RESOURCE_EXHAUSTED`、账号限制、`TOO_MANY_ACTIVE_RUNTIMES` 等，暂停该账号对应的任务并提示用户，且不跨身份自动续跑正在执行的 Job。需要保证持续大规模资源时推荐使用允许的专用 GPU 服务。

### 3.3 切换安全

- 任务一旦已分配到某台机器，不得中途更换账号或 Runtime；远程执行状态未知时先协调、后让用户决定重新提交。
- `create_runtime` 必须使用稳定 requestId；网络超时不能无判断创建第二台。
- 自动选择不等于无限重试；权限失效、预算失败、平台限制与网络未知是不同的错误类别。
- 账号被停用后，已在运行的实例由原账号正常收尾，不能交给其他账号的 token 去管理。

## 4. 实例生命周期与“保留这台机器”行为

### 4.1 四种可选策略

| 策略 | 意图 | 后端行为 |
|---|---|---|
| `save` 节省 | 本任务结束尽快释放 | 确认所有产物持久化后删除 Runtime，核实删除。 |
| `batch` 批次 | 同批兼容任务复用 | 按队列交接 Lease/Instance，最后一个任务完毕释放，除非用户明确选择保留。 |
| `smart` 智能 | 任务结束短暂等待 | 明确授权、预算保护的有限空闲保留；不可复用时到期释放。真实 Colab 未验证前应显示明确受限状态，不能暗中按 Mock 模式冒充成功。 |
| `retain` 保留实例 | 完成后机器仍可继续使用 | 在已配置的上限内停在 `retained_idle`，提供继续提交、保留时间查看与手动释放。硬截止到达自动释放。 |

推荐默认仍是 `save`。在 `CreatePage` 的运行设置用一个明确的「任务完成后保留这台 GPU」开关或第四选项；若勾选，显示当前账号、预计型号（不是确定型号）、可配置最长保留时间、预算风险和确认提示。支持从任务卡片在运行时**开启保留**（`PATCH retention`），不得在释放已经确认启动后改成“保留成功”。

### 4.2 时间和费用保护

- `hard_deadline_at` 由申请机时绑定的管理员最大运行时长决定，不因切换模式、页面刷新或 Job 重试自动延长；需要延长必须重新完成授权、可用性和预算检查，并受平台约束。
- `idle_deadline_at` 是用户选择的空闲保留截止；剩余时间显示为“本站设定截止”，不得承诺远端实际存活时间。
- 默认空闲保留上限建议配置为 15–30 分钟；管理员可以限制得更短，不能超过实例硬截止；不允许“永久保活”或通过伪造活动绕过平台回收。
- 活动任务允许完成必要的安全清理，但超过不可逾越的实例硬截止时进入中止/回收协议；任务如果因平台终止而失败，保留已经持久化的证据。
- 估算 CU：按**实例从申请至 Google 确认删除**分段累积，覆盖任务运行及空闲。当前无法获取可信实时扣费时显示“站内估算 / 官方实际未知”，不得显示为 0 或“实际账单”。
- 每个账号和全站都有预算硬限制。新 Job、实例保留请求、续用及延时之前要检查并预留最坏情况预算；失去预算依据时 fail-closed。避免双重记账：Job 关联到 Instance 账目，而不是把多个 Job 的整段机时相加。

### 4.3 用户动作与准确语义

- 「保留此机器」：任务已分配且实例未释放，校验后更新实例 policy，成功才返回 `retained=true`。
- 「不再保留」：标记在当前 Job 完毕或空闲时释放；不要直接强杀仍在采样的任务，除非用户另外选择「立即停止机器」。
- 「停止此机器」：独立的实例停止操作，二次确认并明确可能中断运行中任务；将任务取消/清理与远端删除结果分开显示。`202` 表示开始请求，只有确认远端不存在后才显示 `deleted`。
- 「继续使用」：从机器详情进入创作页，选中 `instance_id`；新 Job 对该机器进行独占锁定、提交后再次验证连接。
- 「任务详情」：始终保留实例历史记录；机器已关闭也要显示“当时使用的机器（已释放）”。
- 「停止全部任务」不得偷偷释放用户保留但目前无 Job 的 GPU；若需要“全部机器关机”，必须是另外一个明确确认动作。

## 5. 受控 ComfyUI 工作台（本版同交付）

本版提供移动端“工作台”面板，入口放在**在线实例详情**，而不是新加底部导航或桌面三栏。核心是同一个运行实例的：已审核工作流选择、参数编辑、受限图结构查看/编辑、提交队列、运行日志、进度、产物预览、历史回到创作。保持图编辑与现有表单参数一致，保存/导入新版本必须经过既有 manifest/review 规则。

- 复用同台 Comfy 环境需要**实例级**的进程和健康状态检查；仅 `warm_reuse` 或缓存命中不等于 ComfyUI 进程本身热存活。不得为了漂亮的 UI 编造“热加载成功”。
- 在 Colab Runtime 内继续使用回环绑定 `127.0.0.1:8188`；远端执行只走现有受信 MCP/REST 能力，新增固定的受控命令协议：`health`, `list_allowed_workflows`, `get_reviewed_graph`, `set_reviewed_params`, `queue_reviewed_workflow`, `job_status`, `read_safe_log`, `preview_artifacts`, `stop_current_job`。所有请求均归属实例、账号和授权 Job。
- 不允许“任意执行 Python”“任意 URL 代理”“任意 shell”或把 `connectionInfo.token` 传给浏览器；不能把未经审核的节点/模型直接投入真实执行。
- Google Colab 对公开服务、远程代理及资源使用有限制。**不得**通过公网 ComfyUI 端口、任意反向隧道、绕过超时的保活等手段展示所谓“原生 ComfyUI Web”。本版交付的是等价核心创作能力的**站内受控 Comfy 工作台**。若存在经过独立审查、被平台允许且可安全鉴权的原生 UI 连接机制，才能作为可选入口；否则不得在界面宣传原生全功能 ComfyUI。
- 机器释放或失联时工作台转为只读历史，不出现已经失效仍可操作的按钮；手机挂后台不影响服务端任务，但不会让不被允许的 GPU 无限运行。
- 多 Job 可以复用已下载且版本相符的模型/环境；确切模型加载命中取决于存活进程和内存，UI 展示可信检测结果及原因。不同 workflow/依赖不兼容时不强行热复用。

## 6. API 契约：一次性完整发布，不预留假接口

统一沿用 `/api/gpu` 前缀、登录鉴权、`{data,error,request_id}` envelope、`Idempotency-Key`；写操作校验 CSRF/Origin、owner、账号、实例状态、预算，使用一致的 400/403/404/409/422/429/503 错误码。以下路径是推荐契约，Agent 可按现有路由组织实现，但必须统一并写到 OpenAPI。

### 6.1 账号接口

| HTTP | 路径 | 行为 |
|---|---|---|
| `GET` | `/accounts` | 返回 owner 的脱敏账号列表、授权/健康状态、容量、实例数和选择能力。 |
| `POST` | `/accounts` | 创建账号目录占位/别名，不能直接将 token 填在请求正文。 |
| `PATCH` | `/accounts/{account_id}` | 修改别名、启用、允许项目、调度策略或预算配置。 |
| `POST` | `/accounts/{account_id}/auth/{colab|drive}/start` | 发起带 account_id + purpose + 防 CSRF nonce 的 OAuth PKCE。 |
| `POST` | `/accounts/{account_id}/disconnect` | 经过活动资源检查的断开授权；幂等，确认远端撤销结果。 |
| `GET` | `/accounts/{account_id}/runtimes` | 只读已授权可见 Runtime 诊断，不返回连接凭据。 |

OAuth callback 沿用既有安全入口，以服务端保存的 state 映射到账号，不能相信浏览器回传的 account_id。授权状态只显示实际成功的 scope，Token 使用按账号加密、刷新和锁保护。

### 6.2 实例接口

| HTTP | 路径 | 行为 |
|---|---|---|
| `GET` | `/instances?state=...` | 在线/历史实例、对应任务、真实状态和超时信息。 |
| `GET` | `/instances/{instance_id}` | 机器详情、显示名、可信 GPU 信息、retention、CU 估算、最近任务。 |
| `GET` | `/instances/{instance_id}/jobs` | 该机器执行过的任务列表，保留历史。 |
| `PATCH` | `/instances/{instance_id}/retention` | 开启/取消保留和设定空闲截止，必须通过版本/状态校验。 |
| `POST` | `/instances/{instance_id}/stop` | 请求停止，返回操作状态；最终删除靠协调结果确认。 |
| `GET` | `/instances/{instance_id}/events` | 状态转换、授权检查、实例回收、远端错误的脱敏记录。 |
| `GET` | `/instances/{instance_id}/workbench` | 可用工作台能力/状态/受审查工作流。 |
| `POST` | `/instances/{instance_id}/workbench/actions` | 仅固定动作 allowlist；不接受通用执行代码。 |

`POST /instances` 可以作为“先开一台再创作”的可选入口，但必须有明确实例用途、最长时间、预算、账号、规格、二次确认与自动回收。不得默认预开空闲 GPU。

### 6.3 任务接口兼容扩展

`POST /jobs`、`POST /batches` 接受新策略对象（示意）：

```json
{
  "execution_policy": {
    "provider": "colab",
    "max_runtime_minutes": 30,
    "allocation": "auto",
    "account_id": null,
    "instance_id": null,
    "lifecycle": "retain",
    "retain_idle_minutes": 15
  }
}
```

- `allocation: auto | existing`; `existing` 必须传 `instance_id`；`account_id=null` 由合规自动分配；指定实例时不允许冲突账号。
- `lifecycle: save | batch | smart | retain`；旧调用的 `shutdown_after_job=true, idle_grace_minutes=0` 继续按 `save` 处理。新旧字段同时提供且冲突时返回 422。
- Batch 的所有子任务共享同一实例时必须满足账号/规格/环境签名兼容；有一个不兼容则不能静默复用，也不能泄露另一个账号的数据。
- Job 返回允许显示的字段：`account_id`, `account_alias_snapshot`, `instance_id`, `instance_label`, `instance_state`, `gpu_reported`, `retained_until`, `runtime_deleted`, `stage` 等；**不返回** access token、refresh token、Colab connectionInfo、runtime handle、signed Drive URL。
- 为 `PATCH retention`、stop、资源申请等写操作添加稳定幂等键；并发重复提交只能得到同一个结果或已开始的操作状态，不允许多开机器。
- 取消 Job 和停止 Instance 是两个不同的 API，不得混淆。

## 7. 后端调度：必须修复的状态机细节

1. **原子分配**：在短事务内验证账户与实例状态、容量和预算并锁定；记录申请 intent/幂等键；事务外进行 Google REST/MCP 网络请求；再事务性落盘结果。遵守项目现有的 `dispatch_lock` 与 `admission_lock`，但不可跨长网络调用持锁。
2. **账号容量**：重构 `_has_capacity_blocker()`，从“跨所有 lease 阻塞”改为按账号+实例/配置限制判断，仍受全站全局资源上限约束。不要随便删除已有跨进程原子保障。
3. **实例独立于任务**：原 `_lease(job)` 需支持实例级归属。`owner_job_id` 只能代表当前执行使用权，不代表保留实例永久归属；保留中 `current_job_id=null` 为合法状态。
4. **同机接力**：已有 `handoff_lease` 和 `attach_idle_lease` 可借鉴；真实 provider 必须额外验证 Colab 资源和账号归属。兼容签名不仅含工作流，还要考虑运行环境、Comfy版本、模型修订、CUDA/VRAM 等；不能对不兼容工作流伪造热恢复。
5. **保留的提交时机**：Job 成功上传且校验作品后，若实例仍可保留，Job 正常 `completed`，Instance 转 `retained_idle`；若需释放则 Instance 走 `releasing`。作品完成不能依赖先停机成功。
6. **Watchdog**：扫描活跃 Job、实例、孤儿 Lease。已完成 Job 所属的 `retained_idle` Instance 在许可时间内**不能按孤儿删除**。但硬截止、保留截止、用户 stop、未知状态超过协调上限时必须执行安全清理或隔离报警。
7. **未知状态**：Google 创建结果丢失、GET 404 与正在创建 Operation 相互矛盾时必须进入 `unknown/cleanup_pending` 协调；不得直接创建替代 Runtime。删除结果确认以前不写 `deleted`。
8. **断电重建**：API/worker/watchdog 重启后，依数据库中的 `account_id`, `instance_id`, `lease_id`, `execution_id`, `deadline` 继续恢复。不得仅依赖内存变量或前端 localStorage。
9. **费用拆分**：为单实例记录起止、运行段/空闲段、账号与估算费率，并对 job 输出做关联。任务重试、批次交接、重复 watchdog 不能重复计费。
10. **会话生命周期**：Google MCP `create_session` 可使用可重试的稳定 ID，但每个 Job session/工作台 session 应隔离；结束/放弃时尽可能用允许的 `delete_session` 释放 kernel 资源，不把删除 Session 当作关闭 GPU 的证据。
11. **平台失败分类**：权限/refresh 错误、规格不可用、CU/容量限制、瞬时网络、明确删除、执行失败、作品传输失败必须可区分；未知时界面不能显示成功。
12. **安全默认值**：真实 GPU 仍保持显式开启；新安装或测试不允许请求真实硬件。配置关闭后不能让正在运行的已保留实例变成无人管理；停止/回收权限始终可用。

## 8. 手机优先 UI/UX 完整规格

设计继续遵守已有 390px 风格：黑白为主、少量柔和层次、不要花哨玻璃、不要重复设置入口；四个底栏固定为**创作 / 任务 / 作品 / 算力**，设置仍由顶栏进入。正文清晰、触控目标 ≥44px、下拉/弹层单手可用，兼容横屏与平板。

### 创作页

- 运行设置中的顺序：执行方式（Mock/Colab）→ 账号（自动/指定）→ 机器（自动/已有实例）→ 策略（节省/批次/智能/保留）→ 保留时间/预算说明 → 立即生成。
- 账号或机器为空时有真正的禁用与原因文案，而不是空白选择框；切换到已有机器后显示“机器名 · 卡型 · 在线状态 · 预计剩余允许时间”。
- 用户勾选“生成后保留此机”时，提交确认弹窗明确说明**任务结束也可能继续扣 CU**；无真实 GPU/Mock 时保持正确语义。
- 多图/多视频任务仍使用统一批次语义，不因 `batchCount>1` 暗中覆盖用户选定的账户、实例与保留策略。

### 任务页

- 已分配任务卡片在标题/进度附近显示：`GPU-01 · L4（如果已确认）`、账号别名、实例状态，点开进入机器详情。分配前只显示“等待分配机器”。
- 运行中卡片显示「保留这台机器」快捷入口；已经开启保留显示可关闭的状态及截止时间。操作有加载/失败/成功反馈，禁止乐观地假称已保留。
- 完成/失败历史卡片保持机器信息与本次 Execution/attempt 关联；已释放机器不可继续提交，点击后提示重新分配。
- 详情可查看任务、执行、机器、缓存/模型恢复状态、准确的产物上传阶段和错误。

### 算力页（实例中心）

- 顶部简洁显示：在线机器数、当前任务数、站内估算 CU、实际 CU 来源状态和已授权账号数。
- 每台机器一个紧凑卡片：`GPU-01`、状态、账号别名、GPU 卡型（未确认时明确标注）、本次在线时间、当前任务、截止剩余时间、估算 CU、已保留标记。
- 机器卡片操作：`继续创作`、`打开工作台`、`保留/取消保留`、`停止机器`、`详情`。危险动作通过底部确认 Sheet，不与“取消任务”混同。
- 支持按账号筛选并查看已删除/异常实例，不加重复的设置页按钮。

### 设置页（账号管理）

- 账号列表包含别名、Colab/Drive 授权、最近检查时间、允许创建/停用、重新授权、解除连接、单账号安全预算。
- 支持连接新账号时明示即将切换到哪个 Google 身份；OAuth 回调完成后准确返回刚添加的账号卡片。
- 不展示完整邮箱（除非用户主动开启且许可）和敏感 OAuth 客户端密钥；不重复显示“账号池设置”在首页和底部导航。

### 工作台

- 从在线机器卡片进入，不新增第五底栏；手机端默认“参数表单/产物/日志”，复杂图结构由可缩放的全屏画布打开；控制条固定但不挡输入法/底部安全区。
- 显示实例断线、Comfy 服务健康、模型缓存复用状态，不可发送未审核任意节点命令。支持工作流变更提示、提交后回任务页查看。
- 作品来自同一套鉴权图库，不开第二套不受控外链下载通道。

## 9. 功能完整性的验收矩阵（同一版全部满足）

| 编号 | 场景 | 可验收结果 |
|---|---|---|
| A01 | 旧数据迁移 | 原 Job/Batch/Lease/作品继续可读；旧 OAuth 凭据安全映射默认账号。 |
| A02 | 添加两条授权连接 | 各自 token/状态互不覆盖；回调 state 无法串号。 |
| A03 | 账号重授权/禁用/解除 | 不影响其他账号；活动实例不能直接丢失管理权限。 |
| A04 | 合法自动分配 | 同等条件公平轮询，分配理由可见、持久化、重启后游标不丢。 |
| A05 | 禁止规避限额 | 账号达到平台限制时明确阻止，不跨号自动故障转移同一受限任务。 |
| A06 | 指定机器 | 同账号/同 owner/兼容/在线才允许，冲突返回 409/422 而非隐式换机。 |
| I01 | 单次节省模式 | 上传确认后进入安全释放；远端确认为 deleted 才显示已释放。 |
| I02 | 批次同机执行 | 兼容任务绑定相同 instance_id，连续执行后正确关机。 |
| I03 | 开始时选择保留 | Job 完成、Machine 留存为 retained_idle，在截止前可继续用。 |
| I04 | 运行中点击保留 | 前后端一致，重复点击幂等，删除已开始时拒绝保留。 |
| I05 | 取消保留 | 不抢杀正常执行；完成或空闲后安全释放。 |
| I06 | 手动关机 | 当前任务取消确认、远端删除状态与前端 UI 一致。 |
| I07 | 空闲超时/硬截止 | Watchdog 自动执行释放；没有无限活跃的孤儿实例。 |
| I08 | 断网/重复执行 | 不重复开机、不重复 worker、不重复产物、不重复计费。 |
| I09 | 重启恢复 | API/worker/watchdog 重启后仍能恢复实例与 Job 正确归属。 |
| I10 | 远端未知状态 | 显示 unknown/待协调，不假设机器已关。 |
| J01 | 任务机器对应 | 分配后真实反映实例和卡型；历史 Job 仍能追溯已释放机器。 |
| J02 | 多任务并发 | 同一实例只会有一个主动生成，队列顺序和取消公平。 |
| J03 | 批次与工作台并存 | 工作台不能插入破坏已占用 GPU 的执行；排队/失败明确。 |
| W01 | 同机 Comfy 受控操作 | 可信健康检查、工作流提交、参数调整、结果/日志查看可用。 |
| W02 | 工作流隔离 | 未审核节点/脚本无法从工作台执行，外部 token 不流入浏览器。 |
| W03 | GPU/Comfy 非热复用 | 检查失败时准确显示重新准备，不伪称“热加载”。 |
| U01 | CU 统计 | 在线 + 空闲时长纳入估算；实际未知不显示 0，批次不重复计费。 |
| U02 | 预算 | 全局+账号预算并发安全，执行时可见预留和剩余限制。 |
| UI01 | 手机交互 | 360/390/430 px 和桌面宽度正常；深浅主题、软键盘、长工作流无横向溢出。 |
| UI02 | 保留入口 | 创作页开关 + 任务页快捷按钮 + 算力页管理三处指向同一状态。 |
| UI03 | 错误状态 | 401/403/409/422/429/503、网络超时、账号失效、实例被 Google 收回有直观反馈。 |
| S01 | 凭据安全 | 不出现在日志、API、浏览器、产物、文档、Git diff，包含密码/refresh token 测试。 |
| S02 | 旧 API 兼容 | 现有 Mock / save / batch / gallery / workflow API 行为不回归。 |
| R01 | 发布 | 同版前后端、迁移、OpenAPI、README、CHANGELOG、可回退脚本/说明齐全。 |

补充并发必测：两个 Scheduler 同时争一个空闲实例；两个浏览器同时开启保留与关机；同一账号 OAuth 刷新并发；清理失败且进程重启；远端 create 返回丢失；Batch handoff 与 Watchdog 同时执行；Job 完成同时点击继续使用；一个账户失效时另一账户历史数据不被串联。测试必须断言远端模拟调用次数、数据库唯一性和实际返回状态。

## 10. 验证与安全操作约束（Agent 必须遵守）

- 本次开发与自动化验收必须使用纯 Mock/假 Google Transport，保持 `GPU_REAL_ENABLED=0`、`STUDIO_OFFLINE_TEST=1` 或等效零真实资源隔离；不得为了“测通”就用当前真实账号开机器、触发 Colab API 创建、执行 GPU 烟测或消耗 CU。
- 可在无 GPU 成本的条件下运行本地单测、契约测试、浏览器 Mock 验收。真实 Google 的授权、MCP、GPU 规格及 Comfy 常驻效果必须标记“待明确授权实测”，**不得以 Mock 通过冒充真机已经验证**。
- 使用项目现有检查命令作为基线：`uv run pytest -q`、`uv run ruff check api tests`、`npm --prefix web run test:contract`、`npm --prefix web run build`，额外加入实例生命周期、账号隔离和 UI 测试；报告真实命令和通过/失败情况。
- 禁止编辑真实 `.env`、复制数据库私钥、打印 OAuth token、强制推送 main、修改线上部署或直接替用户关机。独立工作分支提交完整代码、迁移、文档，保持线上不受影响。
- Google Colab 托管 runtime 有动态空闲和生命周期上限。不得通过人工心跳、代理隧道、隐藏的后台长任务或多账号切换规避官方使用限制。工作台必须是合规受控交互，不是面向公网的任意 Web 服务。
- 真机验证（需用户另行授权）只允许小额预算和单实例短会话，分别核验实例保留、真实 GPU 卡型、工作台操作、Google 自动回收以及实际 CU。无权限时向用户准确披露“代码集成与离线验证完成，真机未验证”。

## 11. Agent 实施指令与完成定义（不要分阶段交付）

请把本文件当作**唯一完整功能清单**：先检查仓库当前 HEAD、运行现有测试和安全基线，然后在**一个独立分支**中完成所需数据库迁移、账号服务、实例状态机、调度器/API、Comfy 工作台、移动端 UI、兼容性回归、完整验收和文档更新；可以在工程内部按依赖顺序开发，但用户可见交付只能是**一个完整可审查的 v0.2.0 版本**，不要拆成“第1期只做账号、第2期再做持久化”或创建空 API/假按钮。

若某个外部平台能力无法被证明可安全调用，按“受控方案 + 清晰的不可用提示 + 真实 GPU 未验证声明”处理；不要越权使用真实资源、绕过平台政策或声称未验证功能已可用。完成后输出一份完整报告：修改文件、数据模型/接口、迁移与回退、自动化测试真实结果、已知边界、截图/交互验收、需要用户授权的真机清单。除非得到单独明确授权，不部署线上、不操作真实账号和机器。

**一票否决：** 有前端保留开关但后端仍释放；有后端保留但 Watchdog 删机；多账号凭据串号；机器记录不持久；显示不存在的 GPU/实际 CU；实例未删却标“已释放”；直接公网暴露 Comfy/连接令牌；违反 Google 多账号资源限制；真机消耗未经授权。

## 12. 本规划依据与官方边界

- 项目：<https://github.com/TizenryA/colab-gpu-studio>，已查阅 README、`api/app/gpu/{google,colab,mcp,scheduler,repository,compute,runtime_worker}.py`、`admission.py`、`job_schemas.py`、`batch_routes.py`、`presenters.py`、`web/src/{App.tsx,lib/gpu-api.ts,pages/{CreatePage,JobsPage,ComputePage}.tsx}` 与相关测试/说明。
- Google Colab 官方 FAQ（动态资源、禁止多账号规避限制、禁止不相关公开 Web 服务及远程代理）：<https://research.google.com/colaboratory/faq.html>
- Colab 官方 MCP 工具参考（运行时、Session 与执行工具）：<https://developers.google.com/colab/api/reference/mcp>
- `create_session` 幂等与生命周期：<https://developers.google.com/colab/api/reference/mcp/tools_list/create_session>
- Runtime REST API：<https://developers.google.com/colab/api/reference/rest>

---
**最终交付标准：账号能安全连接与管理；任务可以被合法分配到准确的机器；保留模式真正在后端持续管理机器；机器可复用/可停止/会超时释放；任务历史能追溯机器；Comfy 受控工作台可操作；预算与异常真实透明；Mock/离线验收通过；统一作为一个版本交付。**
