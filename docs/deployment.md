# Docker 部署与一天试运行

本轮先在本机完成 Linux Docker 验证；阿里云 SSH 连接、服务器资源和真实邮箱将在提供连接信息后接入。后端与管理页面由同一个服务提供，另启 RSSHub；SQLite、运行日志、日报文件和 CLI 登录状态分别使用持久卷。本次默认通过 SSH 隧道访问后台。

## 运行行为

- 普通采集每 180 分钟一次，维持现有 `hybrid_rerank`、各来源请求预算和独立的平台热度计算。
- Docker 部署模板启用 `WEIBO_CLI_ENABLED=true`，每轮对全部有效 RSS 话题各搜索一次，固定 `sort=time`、默认 `count=10`，逐条核对相关性后汇总热度指标。当前读取 20 条并跳过首条，正常有 19 个话题；若设置不跳过首条，则覆盖 20 个。`WEIBO_CLI_TOPIC_LIMIT=50` 和每轮 `REQUEST_LIMITS.weibo_cli=50` 对齐采集器最大范围，实际调用数只取决于入选话题数，不固定调用 50 次。
- 使用旧 `.env` 时，同步将 `WEIBO_CLI_TOPIC_LIMIT` 从 3 改为 50、`WEIBO_CLI_SEARCH_COUNT` 改为 10；如显式配置了 `REQUEST_LIMITS`，也要将其中的 `weibo_cli` 改为 50。手动设置较低上限仍会减少覆盖。旧的 5 条配置兼容接口最少请求 10 条，并使用全部返回记录，不再先截掉后 5 条。
- 服务器 CLI 认证在部署时配置；认证、超时或预算不足会保留 RSS 话题并记录缺失原因，不能视为已有完整热度数据。趋势只比较相同采样口径的有效观测；条数、排序及采样版本写入签名，旧采样与新采样不直接比较，需积累新的连续观测。
- 北京时间 08:00 启动当天的专用采集轮次，随后生成过去 24 小时的日报、检查质量、保存 HTML／JSON／纯文本，再发送邮件。实际到达时间包含采集、推理和 SMTP 耗时。启动时已过设定时间，会补执行当天任务；不补发之前日期。
- 每天的任务、日报版本与投递回执入库；重启不会再次发送已成功投递的版本。普通采集产生的草稿不会每轮自动发送。
- `DAILY_BRIEFING_ENABLED=true` 表示授权自动确认质量检查通过的每日版本；来源部分失败、待审核候选等警告仍显示在日报中。质量阻断、当天数据过旧或生成失败时不自动发送。人工审核候选继续排除在已接纳证据之外。
- `EMAIL_SCHEDULE_ENABLED=true` 控制定时投递。未配置邮箱时仍生成并存档，在后台显示 `awaiting_email`。只开启这个开关、未开启自动日报时，沿用第八周“发送当日人工确认版本”的行为。
- 明确投递失败最多重试 3 次，间隔递增；SMTP 接收结果未知时停止自动重试，后台核实后处理。“已投递”表示 SMTP 接收，不等于已阅读。
- 日志实时写入 `logs/YYYY-MM-DD/*.jsonl`，按 `Asia/Shanghai` 自然日分文件，默认保留 14 天。包含轮次、工具开始／完成、耗时、请求结果及工作进程输出；不写密钥或完整采集正文。Docker 自身的标准输出另按大小轮转（每服务 3 × 10 MB）。
- 原始业务记录暂不自动删除；一天试运行结束后先备份、再评估数据库保留策略。日志清理不会删除业务数据。

## 配置与启动

服务器需有 Docker Engine 与 Compose 插件；安装参考 [阿里云官方 Docker 文档](https://help.aliyun.com/zh/ecs/user-guide/install-and-use-docker)。本项目只运行一个后端实例／一个 Uvicorn worker。Compose 等待 RSSHub 健康检查，后端 `/ready` 检查数据库与调度线程；健康检查不代表上游采集全部成功。

准备仓库与模型目录后，在仓库根目录执行：

```bash
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
```

在服务器本地填写以下字段，不放入 Git：

| 配置 | 用途 |
| --- | --- |
| `ADMIN_TOKEN` | 至少 32 字符的随机后台口令 |
| `ZHIHU_ACCESS_SECRET` | 知乎接口凭据 |
| `SMTP_HOST`、`SMTP_USERNAME`、`SMTP_PASSWORD` | 邮箱服务器、账号与授权码 |
| `EMAIL_FROM` | 发件地址 |
| `EMAIL_RECIPIENTS` | 可选的初始收件地址 JSON 数组；首次初始化导入一次，之后在后台维护 |
| `EMAIL_SEND_TIME`、`BRIEFING_TIMEZONE` | 默认 `08:00`、`Asia/Shanghai` |
| `SCHEDULER_UNTIL` | 可选的试运行截止时间，必须包含时区 |

建议由邮箱服务支持的 SSL 465 或 STARTTLS 587 发信；阿里云对 TCP 25 有默认限制，参见 [ECS 常用端口](https://help.aliyun.com/en/ecs/user-guide/common-ports)。邮件账户和实际收件箱需要在服务器阶段验收。

将已经准备好的 `backend/data/models` 上传到服务器相同相对路径，或设置 Compose 变量 `MODEL_DIR` 为绝对路径。目录包含 `manifest.json`、`embedding` 和 `reranker`，现有模型约 1.51 GB。运行时仅从只读挂载加载，禁止自动下载；缺少模型时生产服务不会启动。准备脚本为 `backend/scripts/prepare_semantic_models.py`，需要下载时在单独的准备环境运行。

```bash
docker compose build backend
docker compose up -d --wait
docker compose ps
curl --fail http://127.0.0.1:8000/ready
```

后端绑定服务器 `127.0.0.1:8000`，RSSHub 不映射公网端口。在自己电脑建立隧道，再打开 `http://127.0.0.1:8000`：

```bash
ssh -i /path/to/private-key -L 8000:127.0.0.1:8000 USER@SERVER
```

SSH 密钥只在连接端使用，不拷入镜像或容器。首次连接核对服务器指纹；安全组仅向需要的来源地址开放 SSH。以后如需公开网页，再单独配置域名、HTTPS、代理头与 `ADMIN_COOKIE_SECURE=true`。

## 一天试运行

1. 固定代码、镜像和模型版本；设置 `SCHEDULER_UNTIL` 为开始时间后的 24 小时，例如 `2026-10-10T08:00:00+08:00`，以实际启动时间填写。到期不再创建自动采集／日报任务，已在途的任务仍在时限内收尾，后台继续可查看。手动任务不受此开关限制。
2. 启动后观察第一轮：知乎、微博及各官媒来源状态，实际请求数、分类降级、待审核事件，以及平台内趋势。首次运行缺乏历史点时，趋势不足会明确保留。
3. 在后台“邮件管理”添加并启用收件邮箱，再确认每日任务生成、质量检查和真实邮箱投递。该页同时显示收件人、每日任务状态与逐地址投递回执；日报页面可勾选收件人手动发送已确认版本。专用每日轮次可能额外增加一次采集，因此不要机械要求恰好 8 轮。
4. 满一天后导出审计汇总、日志和日报：

```bash
docker compose exec backend python -m app.deployment_cli \
  --output /app/artifacts/trial-audit.json
docker compose cp backend:/app/artifacts ./trial-artifacts
docker compose cp backend:/app/logs ./trial-logs
```

审计默认查看过去 24 小时，也可通过 `--since` 指定带时区的实际开始时间。汇总统计完整／部分／失败轮次、实际请求、邮件回执和悬挂任务；部分成功不会被当成全部通过。运行异常时先查 `/api/daily-jobs` 与任务页，再按 `run_id` 查当天日志。

## 本地隔离验证

`compose.test.yml` 增加 [Mailpit 本地测试邮箱](https://mailpit.axllent.org/docs/install/docker/)，不向真实邮箱中继。使用独立 Compose 项目和测试配置：

```powershell
$env:APP_ENV_FILE='./deploy/.env.test'
$env:APP_PORT='18000'
docker compose -p opinion-local-test -f compose.yml -f compose.test.yml up -d --build --wait
```

测试配置需要一个独立的随机 `ADMIN_TOKEN`。覆盖文件关闭自动采集／投递，邮箱指向 Mailpit；不能把该覆盖文件用于正式部署。管理页面在 `http://127.0.0.1:18000`，测试收件箱在 `http://127.0.0.1:18025`。

`scripts/container_smoke.py` 校验本地语义模型、使用预先导入的有效日报快照驱动每日流程、实际投递到 Mailpit 并重复执行以验证去重。它只允许测试收件地址。`scripts/profile_classification.py` 在独立数据库备份中回放指定分类任务，禁用 HTTP 请求，不改变原始业务库。

## 备份与更新

停后端后备份 SQLite，避免复制到不一致的文件；不需要删除持久卷：

```bash
docker compose stop backend
docker compose cp backend:/app/data/app.db ./app-backup.db
docker compose build backend
docker compose up -d --wait
```

新增表采用兼容创建，保留旧数据。不要用 `docker compose down -v` 更新，它会删除持久数据。语义分数缓存只复用相同模型、相同文本对的模型输出，事件身份、时间、阈值和归并护栏每次照常运行；缓存不包含分类决定。

本轮覆盖第九周部署、调度、日志和试运行准备；服务器真实 24 小时验收与真实邮箱收件尚待实践。Mode B 等其他计划内容不在本次范围。
