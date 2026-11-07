# 0.2.5 更新说明

## 修复：内置 Krea2 模板缺 LoRA 权重（issue #3）

`krea2-moss-dual-t2i 1.1.0` 的 workflow 图里串联了 7 个 `LoraLoaderModelOnly`，但模板清单没有随包下发任何 LoRA 权重，
任务在分配完 Colab 运行时后才报 `LoRA 文件没有随任务下发`，等于先烧一次 GPU 配额再失败。

- 新增内置模板 `krea2-moss-dual-t2i 1.1.1`：保留 4 个可公开下载的 Hugging Face LoRA（电影胶片 2 / realism_engine v3.1 /
  影视柔光 / 风格滤镜）并写入 `weight_files`；3 个作者私有 LoRA 从图中摘除并重连模型链。
- 退役 1.1.0：从 `catalog.json` 移除并删除模板文件。
- 内置模板导入阶段新增校验：LoRA 节点未随包下发、或引用私人 Drive 权重时直接启动报错，不再拖到 GPU 阶段才失败。
- 新增回归测试两条；`workflows/krea2-moss-dual-t2i/1.2.0/` 文档补上 3 个私有权重的获取来源（作者整合包与 C 站页面）。

## 变更：compose 文件拆分（issue #2）

- `docker-compose.yml` 改为 `docker-compose.example.yml` 并只跟踪示例；
- 本地 `docker-compose.yml` 进入 `.gitignore`，改端口/挂载不再弄脏工作区；
- `scripts/deploy.sh` 缺少本地编排文件时自动复制一份。

## 版本说明

版本号统一为 `0.2.5`，与线上部署一致。0.2.1–0.2.4 只以服务端镜像补丁形式存在（数据库/调度器/实例中心的小步修复），
不单独发布 Release，其改动包含在本次镜像基线中。

## 验证

- `uv run pytest -q`：304 passed
- `uv run ruff check api tests`：通过
- 线上 0.2.5 容器内 5 个内置模板全部通过导入校验；对线上库副本执行种子演练，1.1.1 正常落为 draft，4 个 LoRA 全覆盖
