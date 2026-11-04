# 发布到 GitHub

## 推荐方式

创建**全新仓库和全新 Git 历史**，只上传本开源整理包的内容。不要推送原项目的 `.git`、旧分支、标签或运维备份；仅删除工作区中的秘密不能清除旧历史里的秘密。

建议仓库名：`colab-gpu-studio`；建议先私有导入核对，再改公开。原有 MIT 许可与作者署名已保留，无需再次选择许可证模板。

## 网页上传

1. GitHub 新建空仓库，不初始化 README/License。
2. 在电脑解压源码 ZIP，进入 `colab-gpu-studio`。
3. 上传**目录里的文件**，不要仅上传 ZIP，也不要多套一层目录。
4. 注意点文件（`.gitignore`、`.env.example` 等）不能漏掉。
5. 检查 README、License、代码目录及依赖文件。
6. 核对没有私密文件，再把仓库改为公开。

## Git 命令

在解压目录执行；先把 URL 中 `YOUR_ACCOUNT` 改为自己的 GitHub 用户名：

```bash
git init -b main
git add .
git status --short
git diff --cached --check
git commit -m "chore: 初始化脱敏开源版本"
git remote add origin https://github.com/YOUR_ACCOUNT/colab-gpu-studio.git
git push -u origin main
```

不要在运行中的原项目目录执行上述命令，也不要使用 `git push --mirror`。

## 首次 Release

当前开源版为 `0.1.28`，如需发布 Release，建议使用 `v0.1.28` 标签，并在说明中注明“内置 4 套工作流草稿，H3 ref2v 模板为 1.4.0”。不要用 `v1.4.0` 冒充整个应用的版本。

将已验收的源码 ZIP 与 SHA256 校验文件附到 Release。发布后用无登录浏览器查看 README/许可证，并从公开下载地址回下载校验 SHA256。本仓库现已公开；这份指南保留为自行部署和后续发布参考。

本包不附 GitHub Actions 工作流，以避免现有令牌缺少 workflow 权限导致首次推送失败；本地测试命令见 README。后续可自行添加纯离线 CI，切勿把 Colab/OAuth 密钥注入公共 PR 工作流。
