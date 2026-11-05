# 独立审查与修复

独立审查 deleg_50ca8c6e 返回4项问题，不是APPROVED结论。

1. 批次子任务缺request_body：已修复。使用原始Job请求保存，既有retry接口回归通过。
2. 批次行永远queued：已修复。Repository更新Job状态及租约交接时，在同一事务重新汇总Batch状态、counts和finished_jobs。持久化终态测试通过；此前仅修响应层不充分。
3. 大文件协议无实际Runtime生产路径：确认仍未完成。本轮新增run_job显式artifact_store注入及publish_file生产端，150MiB文件、断点续传及1MiB分块读取专项通过。远程Runtime适配与启动注入仍未完成；旧云端路径仍有100MiB Base64兼容限制，不得声称真实大视频链路完成。
4. create_batch唯一键竞争：已增加IntegrityError及SQLite locked有界重试；独立Repository并发同键返回同一批次、仅一次创建的回归通过。跨进程/其他数据库负载仍未完整验收。

修复后全量：131 passed, 1 warning in 180.24s。Ruff通过。修复由主助手完成，尚无第二次独立复审批准；真实GPU未验证。

## 独立复审 deleg_8c62861e

复审确认批次原始请求、Batch持久化、SQLite幂等修复有效；云端生产链路仍未完成。新增边界：随机Seed重试重新解析；system smoke不适配普通retry；JSON诊断报告不被媒体ArtifactStore接受；Job/Execution/Event仍多事务。

复审发现的普通Admission残缺窗口已修复：create_job以admitted参数在同一事务插入解析快照和任务，不再二次update。故障注入回归通过。修复后全量132 passed, 1 warning in 118.44s，Ruff和diff check通过。此新增修复尚无独立复审批准。

## 本轮后续修复

- transition_job同事务写Job、Execution、Batch和Event；事件插入失败全部回滚、终态回放不重复事件。Scheduler传expected_state；旧状态回调不覆盖新状态。PostgreSQL读取加行锁，实际PostgreSQL未验收。
- 普通retry固定resolved_seed，新Job保存retry_of，旧Job/Execution证据保留；这仍是兼容既有“新Job再次执行”接口，不宣称已完成同Job多attempt模型。
- 特殊诊断/预热retry显式409，要求回对应确认入口，不绕过真实算力开关。
- JSON产物支持本地对象清单；入库仅允许诊断/prep、<=1MiB、JSON对象。普通任务JSON拒绝。
- MockWorkerSessions验证owner、签名、串行执行、重复start和会话复用；租约删除后关闭。它模拟服务生命周期，不运行Comfy或证明真实热模型。
- Worker→清单→服务端verify→图库→完成→Mock租约删除的离线闭环已通过。

本轮独立审查deleg_8d8c12b5已派发，待结果。最新全量140通过后又增加expected_state专项，相关19项通过；最终全量见latest-checks.json。

## 独立审查deleg_8d8c12b5后续修复

五项发现已复现并补回归：workflow_smoke普通retry现409；Mock handle身份验证及owner绑定，恢复仅按runtime/request/job匹配补绑；同execution幂等start也校验签名；prep清单读取服务端校验的JSON并持久化prep_report；跨owner Execution转换抛错并回滚Job/Event。

收紧handle后出现3项旧恢复测试失败，已修复受限recover_handle协调路径；scheduler专项16通过。最终全量147 passed, 1 warning in 117.38s，Ruff和diff check通过。本次修复尚无独立复审批准；云端路径与原有规格缺口仍在。

## deleg_58bd1b73七项修复

1. record_usage_once使用owner/job/execution哈希主键，数据库主键约束去重；并发插入返回相同记录。create将IntegrityError包装为ValueError，已实测并处理。
2. starting Execution恢复重放同ID幂等start，不跳过实际启动，不新增Runtime。
3. job.lease_id需匹配owner_job_id；同owner其他Job租约拒绝。
4. handoff拒绝后继已有其他租约引用或活跃租约。
5. poll所有输出分支统一拒绝存在但不匹配的execution_id，保留无字段旧协议兼容。
6. Mock poll只允许会话最后一次execution，旧poll拒绝。
7. retry清除旧stage/timings/output_bytes/started_at/finished_at，原值保留attempt_history。

新增test_review_recovery_edges.py七项回归通过，全量158 passed, 1 warning in 122.62s；Ruff/diff通过。最新修复未取得独立复审批准。历史随机ID usage未自动改写/去重，禁止破坏历史账务；真实数据库与真实GPU仍未验证。

## deleg_dee5a5fc 定向修复

会话过期404/410恢复已在审查返回前补修。新增三项缺陷已复现后修复：上传初始化使用按数据库/owner/业务key的非阻塞锁，竞争者得到DriveError busy且不创建第二会话；资产重放先核验已有execution/hash及verified cloud_record，不再次访问云端；非法完成size统一返回DriveError，不登记verified文件。

定向验证：Drive文件上传、资产清单、旧Drive契约共29 passed in 24.28s，Ruff通过。没有重跑全量；此前全量结果不能代表本次最新补丁全量验收。SQLite独立Store竞争测试通过，PostgreSQL锁代码未在真实数据库验证。用户要求后续仅运行失败用例与直接相关回归，不再运行全量。远端Worker bootstrap未接通，真实Google/Drive/GPU未调用。

## deleg_d1d5eb43 两项定向修复

HTTP200 JSON-RPC temporary unavailable/deadline错误现在归类MCPTransient，可进入既有有限退避恢复；协议/权限错误仍为永久失败，消息不回显。GIF已补入Artifact白名单，并通过真实Pillow解码验证后提交资产。复现时3 failed、2 passed；修复后同5项通过（2.30s），未跑全量。审查本身未发现其覆盖范围内其他缺陷，但不等同全规格验收。
