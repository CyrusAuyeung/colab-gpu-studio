# GitHub 维护与版本发布说明

如果你 Fork 或克隆了本仓库自己改代码，发布新版本时请注意以下几点：

## 1. 千万别把私密文件提交上去

提交代码前先用 `git status` 看一眼，确保以下内容**绝对不会**进入 Git：
- `.env` 及任何备份配置文件
- `data/` 目录（含 SQLite 数据库和本地素材）
- Google OAuth 客户端 JSON 文件、令牌或日志

## 2. 版本号同步

更新版本时，建议同步修改以下文件中的版本号：
- `api/app/__init__.py`
- `pyproject.toml`
- `docker-compose.example.yml`（本地 `docker-compose.yml` 不入库，改它不算版本变更）
- `web/package.json`

注意区分 **程序版本号**（如 `0.1.28`）与单个 **工作流模板版本号**（如 H3 ref2v 的 `1.4.0`），避免混淆。

## 3. 本地检查命令

推送前建议跑一遍测试和构建：

```bash
uv run pytest -q
uv run ruff check api tests
npm --prefix web run test:contract
npm --prefix web run build
git diff --check
```
