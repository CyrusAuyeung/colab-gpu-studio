# 本地验收边界（未完成整份规格）

版本0.1.31-dev.1。未部署，未调用真实Google业务或Runtime。验收基于本地隔离API、SQLite、真实浏览器及Mock provider/executor。

- 最新全量131项通过，Ruff、前端契约、TypeScript构建与70组浏览器检查通过。
- SQLite重开后继续既有Execution，start不重复；远端marker丢失时失败清理，不自动重跑。
- 最终Execution状态随Job完成/失败/取消同步；Lease交接已有事务路径。
- 浏览器真实提交3任务批次，轮询API确认本轮新增恰好3个Job完成；图库加载真实Mock资产，非写死成功响应。
- 已人工复核390×844真实视口截图：顶栏与导航位置正常。长页面截图不得代替视口定位检查。
- 七宽度×双主题×五页70组：无横向溢出，无pageerror，无外部请求；Session刷新保留。
- 作品搜索和清空后图片naturalWidth通过；390×430模拟键盘视口无溢出。不能代替真实安卓键盘验证。
- application-network-audit.json及network-audit.json来自拦截层，四项真实调用计数为0，blocked_attempts为空。
- 连续同步双击确认提交，仅1次POST；两实例同时重放相同chunk不重复写入。
- 修改与未追踪文本文件凭据特征扫描0命中；只覆盖私钥/Google token特征，不能当作完整安全审计。
- 独立review已返回4项问题；三项修复并回归，一项Worker生产路径仍缺。尚无独立复审批准。

尚欠完整云端常驻进程/热模型实现验收、真实Drive直传、完整API模块分层、全部故障矩阵、真实安卓与负载测试。详见REAL_GPU_VALIDATION.md。截图与DOM检查不证明所有交互均已完成。

本轮Worker生产端：显式注入LocalArtifactStore后，run_job写artifacts清单；publish_file分块校验/续传，Comfy下载分块落盘。150MiB本地文件生产测试通过（采样函数被替换，未运行Comfy）。远程CLI/MCP云存储适配仍未接入。全量131项通过、1项弃用警告。

## 最新闭环复验

141项全量通过（119.99s），1项Starlette弃用警告，Ruff与diff check通过。前端build及最新重启离线应用的70组浏览器验收通过。事务回滚、旧回调、Mock会话owner/签名/串行、JSON报告、固定Seed重试均新增实际测试。另见latest-checks.json和REVIEW_FINDINGS.md。整份规格仍有明确缺口，不能标完成。

最新142项全量通过（111.65s）；Admission拆分回归17项通过，执行准备原子/幂等测试通过。普通任务也写Execution。真实云端适配、同Job多attempt重试与独立最新复审仍待完成。

最新147项全量通过，Ruff/diff通过。独立审查5项缺陷均复现修复，含prep报告清单闭环和Mock恢复handle协调；未标独立复审批准。

本轮151项全量、Ruff和build通过；重启故障服务后browser attempt检查通过，涵盖模拟503弹窗错误、幂等键保留、真实本地失败/重试/作品/清理和详情两次执行记录。真实GPU均未验证。

最新复审修复：七项recovery-edge回归通过，全量158通过（122.62s），Ruff/diff通过。覆盖并发用量去重、prepare后崩溃恢复start、错误租约归属、重复handoff、旧raw poll、Mock旧poll和重试展示字段清理。真实GPU未执行。

统一离线验收：`uv run python scripts/verify_offline.py` 已连续两次完整通过（第一次正常提交因SCHEDULER_BUSY失败，修复后两次通过）。最新160 passed、1 warning、122.64s；Ruff/前端契约/构建/正常批次70组合/失败重试/200任务1000作品长列表通过。脚本拥有并关闭两套新临时服务，三份网络审计零调用/零拦截尝试。提交入口新增最多1秒等待，调度tick仍非阻塞；两项锁回归通过。CI配置已新增但未推送、未在GitHub运行。失败运行先清除旧成功标记与审计，避免旧证据冒充本轮成功。

服务端文件上传新增5项回归：150/500MiB假媒体分块、持久化会话跨对象重建续传、符号链接拒绝、清单云端记录关联。统一验收最新165 passed、1 warning、138.78s；全部浏览器用例及零网络审计通过。独立审查deleg_dee5a5fc待返回；远端Worker bootstrap仍未接入，真实Drive未验证。

Worker CLI远端清单协议已接入MCP分块取回；本地生成代码至图库闭环通过，新增5项定向测试、旧相关11项通过；先前另一次manifest兼容1项通过。未跑全量，未调用真实MCP/Google/GPU。调度器网络异常后的自动恢复仍待处理，不能宣称全部规格完成。

## 调度器临时断线续传

MCP传输重试耗尽及408/429/500/502/503/504归类为MCPTransient。Scheduler在同execution保持running，持久化poll_retry_count和poll_retry_at，最多允许5轮退避重试，超限进入原有失败清理；鉴权、身份、编码、校验错误直接失败。重建Scheduler/Executor后从数据库退避时间及对象已写偏移续传，成功清零重试字段。新增恢复策略3项及分块相关5项定向通过，旧MCP executor4项兼容通过；未跑全量，未访问真实服务。实际断线续传用例经过Scheduler恢复并进入uploading，不再只是直接调用poll。

工作流兼容补证：tests/gpu/test_workflow_acceptance_matrix.py 6 passed in 0.11s，覆盖四类内置流程的五个版本和未知命名参数模板，均仅解析/绑定，不生成真实图像或视频。源文件静态凭据扫描180候选路径零高置信模式命中，未加载生产密钥；边界见source-scan.json。最新MCP恢复定向审查deleg_d1d5eb43进行中，未跑全量。

作品查看器新增全屏按钮，支持全屏中直接关闭并退出全屏；390手机长标题与按钮分行布局。offline_mobile_interactions.py实际浏览器通过，含短视口输入、空图库、进入全屏、退出、全屏中关闭，零pageerror/外部请求。图片为明确Mock假数据，真实Android键盘未验证。前端构建通过；没有运行后端全量。

双Scheduler同Job竞争专项：1 passed in 2.86s，独立SQLite连接共享磁盘、start阻塞期间第二调度器不推进，最终create/start/delete各一次、Execution/Lease各一条。Ruff通过，没有全量重跑。

500MiB精确60%断线专项：test_mcp_large_resume.py 1 passed in 34.28s。使用稀疏假mp4文件（非真实视频），实际执行生成读取器；前段读偏移0..299MiB，断点300MiB，重建DB连接/Scheduler/Executor后读300..499MiB，未重复前段；最终SHA256和长度校验并登记私有图库。每次读取最多1MiB。Ruff通过，未跑全量，未调用真实MCP/GPU。

队列公平性定向：test_queue_fairness.py 1 passed in 2.41s；连续12轮新v1到达，较早的不兼容v2未被越过，执行顺序保持最早两项且串行。仅Mock有限到达流，不代表无限流或多owner公平性保证。Ruff通过，无全量。

Worker进程退出：先复现running卡住（1 failed），修复PID记录和读探针后，死亡/存活/completed三项通过（0.16s），另启动幂等回归通过。仅本地CPU子进程；保留文件，未自动重跑或宣称恢复未完成采样。
