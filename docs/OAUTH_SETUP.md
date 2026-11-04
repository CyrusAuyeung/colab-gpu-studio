# Google OAuth 与 Drive 配置指南

本项目不会内置任何人的 Google 账号或密钥。要想让后端替你去开 Colab GPU、读写 Google Drive 里的模型缓存，你需要用自己的 Google Cloud 项目创建一个 OAuth 客户端。

## 第一步：在 Google Cloud 创建客户端 JSON

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)，新建或选择一个个人项目。
2. 在“API 和服务 → 库”里，搜索并启用 **Google Drive API**（如果你的账号列表里有 **Colaboratory API** 也一并启用）。
3. 进入“Google Auth Platform / OAuth 同意屏幕”：
   - 如果设为“测试（Testing）”模式，务必把你自己的 Google 邮箱加进 **测试用户（Test users）** 列表。
   - 在数据访问权限（Scopes）里添加：
     - `https://www.googleapis.com/auth/colaboratory`（用于申请和管理 Colab 运行时）
     - `https://www.googleapis.com/auth/drive.file`（仅允许访问本应用在 Drive 里创建的缓存目录和作品，不会扫你网盘里的其他私人文件）
4. 进入“客户端（Clients）”创建凭据，类型必须选 **Web 应用（Web application）**：
   - **已获授权的 JavaScript 来源**：填你的网站地址，比如 `https://studio.example.com`（本地测试填 `http://127.0.0.1:8788`）
   - **已获授权的重定向 URI**：必须精确填写 `https://studio.example.com/api/gpu/auth/google/callback`（把域名换成你实际配置的 `SITE_URL`）
5. 创建好之后，点击下载客户端的 **JSON 文件**。

## 第二步：直接在网页上导入并连接（推荐）

不用连 SSH 改配置，直接在网页端就能绑好：

1. 登录你的 Colab GPU Studio 网页 → 进入 **设置** 页面。
2. 在 **Google OAuth 应用配置** 卡片里，上传刚才下载的客户端 JSON 文件，勾选确认并保存（Client Secret 会用 `.env` 里的 `ENCRYPTION_KEY` 加密存进数据库，前端不会回显）。
3. 保存成功后，在下方分别点击 **连接 Google Colab** 和 **连接 Google Drive**，跳转到 Google 完成授权。
4. 两个状态都变成“已连接”后，点一下 **检查可用 GPU**，就能看到你当前 Google 账号能用哪些显卡（比如 T4、L4、A100）了。

> **备用方式**：如果你不喜欢网页导入，也可以把 `GPU_GOOGLE_CLIENT_ID`、`GPU_GOOGLE_CLIENT_SECRET`、`GPU_GOOGLE_REDIRECT_URI` 直接填进服务器的 `.env` 文件里再重启容器。

## 第三步：开启真实 GPU 开关与预算保护

授权连上之后，默认还是不会开真 GPU 的。确认要开始跑图前，编辑服务器的 `.env`：

```ini
GPU_REAL_ENABLED=1
GPU_STORAGE=drive
# 填写每分钟消耗的 CU 估算值（例如 L4 约 0.027，T4 约 0.019），不填正数不允许跑真机
GPU_UNIT_RATE=0.027
GPU_DAILY_UNITS=10
GPU_MONTHLY_UNITS=100
GPU_MAX_JOB_UNITS=5
GPU_MAX_RUNTIME_MINUTES=60
```

改完重启服务，就可以去跑一次 CUDA 冒烟或激活内置工作流了。每次任务跑完，系统都会校验作品并自动删掉 Colab runtime。
