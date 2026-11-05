# 原规格故障场景证据索引

这是用例与规格的可追溯索引，不是一次新的全量测试结果。历史执行与当前定向结果分开记录；不存在对应单例不自动等于实现缺失。所有资源为Mock。

|场景|代码证据|范围限制|
|---|---|---|
|单任务及图库|`tests/gpu/test_artifact_scheduler.py:93` `test_worker_to_gallery_manifest_closed_loop`|离线专项，不能证明真实服务行为|
|30任务单租约|`tests/gpu/test_batches.py:25` `test_thirty_jobs_reuse_one_lease`|离线专项，不能证明真实服务行为|
|100任务取消10|`tests/gpu/test_batches.py:165` `test_hundred_jobs_cancel_ten_preserves_ninety_outputs`|离线专项，不能证明真实服务行为|
|混合版本隔离|`tests/gpu/test_batches.py:144` `test_incompatible_versions_never_share_lease`|离线专项，不能证明真实服务行为|
|智能空闲|`tests/gpu/test_batches.py:118` `test_smart_idle_reuses_and_expires_with_fake_clock`|离线专项，不能证明真实服务行为|
|硬截止|`tests/gpu/test_scheduler.py:177` `test_timeout_cleans_up_without_polling_forever`|离线专项，不能证明真实服务行为|
|创建回包丢失协调|`tests/gpu/test_scheduler.py:326` `test_real_sqlite_unknown_create_is_inspected_and_deleted`|验证查找和回收，不证明所有故障下继续原任务|
|执行身份写盘后恢复|`tests/gpu/test_review_recovery_edges.py:12` `test_prepared_but_not_started_execution_recovers_same_identity`|离线专项，不能证明真实服务行为|
|重建调度恢复|`tests/gpu/test_batch_recovery.py:7` `test_new_scheduler_recovers_existing_execution_without_restart`|离线专项，不能证明真实服务行为|
|删除失败阻塞及重试|`tests/gpu/test_scheduler.py:134` `test_cleanup_pending_blocks_next_job_until_watchdog`|离线专项，不能证明真实服务行为|
|500MiB分块|`tests/gpu/test_drive_file_streaming.py:90` `test_large_file_streams_without_whole_file_bytes`|150/500MiB分块与500MiB精确60%断线本地协议已覆盖|
|MCP分块断线恢复|`tests/gpu/test_mcp_chunk_transfer.py:11` `test_remote_manifest_transfer_resumes_after_interruption`|离线专项，不能证明真实服务行为|
|幂等批次冲突|`tests/gpu/test_batches.py:5` `test_batch_atomic_idempotency_and_conflict`|离线专项，不能证明真实服务行为|
|并发批次入库|`tests/gpu/test_batches.py:214` `test_separate_repositories_concurrent_batch_idempotency`|证明提交幂等，不代表双Scheduler抢同Lease|
|预算先于分配|`tests/gpu/test_scheduler.py:160` `test_budget_rejection_precedes_provider_create`|离线专项，不能证明真实服务行为|
|恢复重试有界|`tests/gpu/test_poll_recovery.py:35` `test_poll_retries_have_finite_budget`|离线专项，不能证明真实服务行为|

## 当前定向执行

硬截止、删除失败、混合版本3项：3 passed in 2.91s；无全量重跑。

## 尚缺精确证据的验收项

- 双Scheduler同Job认领已补定向证据：test_dual_scheduler.py，两个独立SQLite连接、暂停start边界及后续并发tick，1 passed in 2.86s；仅一个create/start/delete，不代表真实PostgreSQL或超出claim TTL的长调用竞争。
- 混合队列新任务插入已补证据：test_queue_fairness.py，连续12轮插入v1任务，原先排队v2仍为第二个启动并完成；1 passed in 2.41s。未证明无限流、多owner、长期负载的公平性上界。
- Worker死亡已复现并修复：bootstrap记录PID，读取器识别进程不存在/僵尸，不重启采样、保留现场，报告gpu_worker_exited；正常进程与completed不误判。3项通过。未完成图不当成功产物恢复；PID复用与bootstrap写PID前崩溃窗口仍依赖硬截止，未证明无损采样恢复。
- 500MiB假视频精确60%断线已补本地协议证据：test_mcp_large_resume.py，实际执行生成的poll/chunk代码；300MiB处断开后重建数据库连接、调度器与执行器，从300MiB续传至图库校验。1 passed in 34.28s；不证明真实MCP网络行为。
- 实体安卓键盘；前端提交后响应丢失的结果核对与全状态矩阵。
- 生产数据库副本迁移回退及完整依赖许可证审查。

不能把本索引或源码包称为整份规格验收完成。以上可离线补齐者仍可继续实现；实体设备与生产副本不能用模拟结果替代。
