# Google OAuth 与 Drive 配置

## 在网页配置（推荐）

打开 https://studio.example.com → 设置 → Google OAuth 应用配置。按页面指引在自己的 Google Cloud 项目创建 Web 应用客户端，导入下载的客户端 JSON，勾选确认并保存。Client Secret 加密存在后端；浏览器不会持久保存，也不需要 SSH。

固定回调：`https://studio.example.com/api/gpu/auth/google/callback`

JavaScript来源（如需填写）：`https://studio.example.com`

客户端类型必须是 Web，不能上传服务账号或桌面应用JSON。Google Auth Platform 测试模式须把自己的 Google 邮箱加入测试用户；“数据访问权限”添加下方 Colab 与 drive.file scopes。下载JSON的 redirect_uris 必须包含上述完整回调。

保存后分别点击“连接 Google Colab”和“连接 Google Drive”，跳转Google授权。只有本人在Google确认后才是已连接；导入客户端不是账户授权，更不会启用GPU。更换已连接的OAuth客户端前需先断开Colab/Drive。

以下环境变量方式仍可作为运维备用；Web配置优先。回调始终由本站SITE_URL计算，不接受浏览器或客户端JSON指定其他回调。


## 尚需用户本人完成

本项目没有预置 Google 账户、refresh token 或 OAuth Client Secret。没有本人授权，不能核实套餐、GPU 库存、计算单元或真实 Drive 文件。准备完成之前保持 `GPU_REAL_ENABLED=0`。

1. Google Cloud 创建/选择个人项目，启用 Colab API (`colaboratory.googleapis.com`) 与 Google Drive API。
2. 配置 OAuth consent screen，测试模式添加自己的 Google 账号；发布/敏感 scopes 验证遵循 Google 当前要求。测试模式 refresh token 生命周期可能受限。
3. 创建 Web application OAuth client。授权回调必须精确为 `https://你的独立域名/api/gpu/auth/google/callback`，必须对应当前部署站点。
4. 服务器私密 `.env` 设置 `GPU_GOOGLE_CLIENT_ID`、`GPU_GOOGLE_CLIENT_SECRET`、`GPU_GOOGLE_REDIRECT_URI`；保留原 ENCRYPTION_KEY，重建三服务加载配置。
5. 网页设置页分别连接 Colab 与 Drive。

Scopes：

- Colab：`https://www.googleapis.com/auth/colaboratory`，必须个人付费权益对应用户，不默认 service account 继承个人计算单元。
- Drive：`https://www.googleapis.com/auth/drive.file`，仅管理应用创建/获授文件，不承诺能遍历整个 Drive。

OAuth start 是已认证 POST，后端返回授权 URL；PKCE verifier 与 token 加密存库，state 有期限且仅可使用一次。Callback 根据服务端 state 恢复 owner，不通过前端传 refresh token。

## 最小验证顺序

1. 先 GET `/api/gpu/compute/subscription`、`/api/gpu/compute/specs?provider=colab`；这些不创建 GPU。
2. 为目标机型填写可信当前估算 `GPU_UNIT_RATE`（每分钟点数），设置日/月/单任务与时长限额。没有可靠费率就保持关闭。
3. 明确启用 `GPU_REAL_ENABLED=1`，POST `/api/gpu/admin/compute/smoke`，带 `Idempotency-Key` 与 `{confirmed:true,spec:{key:真实规格}}`。最长五分钟，走与生成相同的预算/租约/清理服务。
4. 查看任务真实 CUDA 诊断输出，确认租约 deleted，再到 Google 账户核对实际消费。未确认删除时 UI 为 cleanup_pending，不重新开下一台。
5. 设置 `GPU_STORAGE=drive`，用小文件验证上传、回读校验和权限；不调用无头 drive.mount()。只有通过回读验证才完成。
6. 完成真实模型许可、固定权重和硬件审查后才做一张最低成本 MOSS 图。

## 安全

本站登录 Bearer token 不等于 Google token。Google access/refresh token 不发浏览器，不写 Notebook、Drive、源代码、日志和任务事件。运行时短期代理 token 仅后端内部使用。默认不下载 gated 权重，也不会把你的 Hugging Face 私密 token 自动传给 Colab；私有/gated 模型需要单独合规方案。
