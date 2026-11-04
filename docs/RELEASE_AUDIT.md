# 首次开源整理与验证报告（历史基线）

> 本文记录首次源码包的验证状态，不代表后续版本现状。当前 0.1.28 已增加 4 套内置草稿，见 `BUILTIN_WORKFLOWS.md`；下文“未发布/未导出”指首次整理时的状态。

## 范围与状态

这是独立源码整理包，**不是生产环境完整迁移包，也未发布到 GitHub**。仅按要求移除外部创作站联动、清理私密材料、整理公开文档；不重构其他功能。

基线提交：`a2a8869756f638cccbfa73ad06fcf58a73db0245`。工作流版本、提交说明和程序内版本字符串的区别见根 README。没有将工作流 `1.4.0` 改写为全应用版本。

## 保留与移除

保留仓库中的 FastAPI/React、Google OAuth、Colab/Drive 适配、CPU 缓存准备、GPU worker/watchdog、LoRA、调度器、manifest/工作流审查、素材与任务管理、迁移、依赖锁及原 MIT 许可。

移除联动路由与连接器、自动回传、项目映射字段、外站设置表单、导出按钮/弹窗、前端专用 API、相关初始化配置及专用测试。原联动模块内的本地项目改名能力迁回独立 API。

保留 GPU/Drive 底层历史 metadata 名称和演示字段，以免改变现有协议。这些字段不会连接外部创作站。

代码差异限定为：

- `api/app/main.py`
- `api/app/worker.py`
- `scripts/init_env.py`
- `web/src/App.tsx`
- `web/src/lib/gpu-api.ts`

删除：`api/app/atelier_routes.py`、`api/app/integrations/atelier.py`、`tests/test_atelier_connector.py`。
新增：`tests/test_standalone_release.py`，验证联动路由/入口/配置消失及核心项目/任务路由仍在。

其余保留的 57 个原有代码文件逐字一致；`api/app/gpu` 下的原有文件全部逐字一致。配置与说明的变化仅用于脱敏、移除联动和准确描述开源包。

## 隐私处理

采用文件白名单导出，而非复制整个运维目录。未包含：

- 原 `.git` 和提交历史。
- `.env`、历史 `.env`、OAuth 客户端 JSON 与令牌。
- 数据库、生产项目/任务/素材、Drive 缓存与 GPU 成片。
- 私钥、登录信息、生产快照、旧交接材料和私人 API QA 脚本。
- 虚拟环境、node_modules、编译输出和测试生成数据。

公开 OAuth 示例改用保留示例域名。作者在原 MIT 许可证中的公开署名保留。

## 实际验证

- 在独立目录从锁文件执行 `uv sync --locked --extra test` 成功。
- 原基线隔离测试：96 passed、1 skipped；跳过项为需外站测试账号的 live 合约测试。
- 整理后独立测试：**91 passed**；存在一条上游 Starlette/httpx 弃用警告，不影响退出码。
- `ruff check api tests`：通过。
- `npm ci --ignore-scripts`：成功。
- 前端 `test:contract`：通过。
- TypeScript 与 Vite 生产构建：通过。
- 独立本地 API 入口 `/api/health` 返回 200，OpenAPI 中外站联动路由为 0。
- 本地真实 Chromium：登录、新建项目、选择内置 Mock 工作流、确认入队、刷新后完成、作品图片加载成功。
- 五个业务页在 360/390/430 宽度下无横向溢出；检查桌面设置渲染。Google 配置/连接和 GPU 操作仍在；无外站联动表单、映射或导出按钮。
- 浏览器 pageerror 为 0，观察到的外站联动请求为 0，外网请求为 0。
- 已知本地生产凭据精确值比对：0 命中。
- 私人域名/IP/邮箱/开发路径标记扫描：0 命中。
- Gitleaks v8.30.1 默认规则扫描：0 发现。
- 原项目 165 个受保护源文件哈希一致，原 HEAD 和干净 Git 状态不变。

## 未验证与限制

- 未创建任何真实 Colab runtime（CPU/GPU 均无），未执行收费生成、Google 授权或模型下载。
- 测试证明离线/Mock 队列和删联动后的应用组装可用，不证明生产图像/视频画质或真实 CU 消费。
- 生产数据库中的已发布 graph/manifest/object_info 未导出，新安装只有内置演示工作流。
- 本机 Docker daemon 不可用，**没有声称 Docker 镜像构建或 Compose 容器运行已验收**。
- 保留现有样式；桌面下 vw 缩放较大，未为此调整 UI。
- 本次不是完整漏洞审计，秘密扫描零发现不等于零安全风险。

## GitHub 边界

本次只检查了账户与权限，未建仓、未推送、未改变任何仓库可见性。发布应从此整理包新建历史，不推原项目历史。操作步骤见 `GITHUB_RELEASE.md`。
