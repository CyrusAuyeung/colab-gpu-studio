# 本轮开发基线与边界

本轮以维护者网站实际运行的 0.1.30 程序和 GitHub main 为共同基线，不能用旧本地私有源码替代。

- GitHub HEAD：`ae4211d0f4de4c2335c81120cf5bd07af94a5e3d`。
- 开发分支：`refactor/v0.2-offline-ui`。
- 开发工作树：`/opt/data/workspace/colab-gpu-studio-offline`。
- 网站：`https://gpu.tizenty.xyz`。
- 只读部署核对：HD `/opt/colab-gpu-studio`，容器镜像 `colab-gpu-studio:0.1.30`。
- 网站源码快照位于开发机独立目录，仅提取容器 `/app/api` 的程序文件及 `/app/web/dist` 的静态资源，没有读取环境文件、数据库、Google 账号或 Drive 用户文件。

旧目录 `/opt/data/workspace/colab-gpu-studio` 不再作为实现依据。维护者已允许淘汰旧版本；它目前仍承载独立工作树的 Git 对象库，删除前应先把开发成果迁至独立 Git 仓库，避免损坏新工作树。旧源码保留不代表继续采用。

## 合并取舍

网站相对 GitHub 的程序差异已逐项对照：

- 合入网站 `runtime_worker.py` 中精确固定的 Krea conditioning 插件支持、Drive 权重来源校验、LoRA 完整性检查和缓存恢复修复。
- 合入网站 `prep.py` 对 Drive 权重的缓存签名支持，保留历史 HF 缓存键兼容。
- 合入网站 `review_routes.py` 对 `ConditioningKrea2Rebalance` 的精确 repo/commit 校验。
- 合入网站 scheduler 对终态/清理态的重复失败保护。
- 保留网站独立 Atelier 外部 API 联动模块和 worker 的对应调用入口；本轮离线环境不会调用真实外部服务。
- 保留 GitHub 的内置草稿模板初始化和项目重命名 PATCH 接口，不因复制网站旧实现而撤销公开仓库新增功能。
- `main.py` 采用 GitHub 当前路由，增量恢复网站 Atelier 项目标识和路由，保留网站单任务默认 60 分钟上限参数。
- 网站 0.1.30 是历史基线；本轮没有重新验证其任何 GPU、Drive、模型输出或 CU 数据。

## 强制边界

开发仅限本地独立数据库、假账号、Mock Provider、受拦截网络的自动测试及本地浏览器。禁止真实 Google OAuth/Drive/Colab 业务请求、CPU/GPU Runtime 分配、真实账单访问或线上自动部署。本轮不发布 Release。测试证据以实际命令和拦截层记录为准。
