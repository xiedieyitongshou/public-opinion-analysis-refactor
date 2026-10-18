# 每日简报与后台使用指南

[返回项目首页](../README.md) · [产品与展示](product-design.md) · [Docker 部署](deployment.md)

完整系统由 `app.main:app` 提供后台页面与接口，`app.briefing_cli` 支持命令行操作。一轮任务先执行九步分析，再生成、检查和保存日报。网页、HTML 邮件、纯文本邮件和 JSON 导出读取同一份已保存版本。

## 本地启动

需要 Python 3.11+；以下 PowerShell 命令从仓库根目录开始。已有根目录 `.venv` 时直接复用。

```powershell
# 仅首次安装时创建环境
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "./backend[dev]"
if (-not (Test-Path backend\.env)) { Copy-Item backend\.env.example backend\.env }
cd backend
..\.venv\Scripts\python.exe -m app.briefing_cli init
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。`init` 在尚未配置时生成随机 `ADMIN_TOKEN`，保存在 `backend/.env`，不会打印到日志；用该值登录。已有的来源密钥和配置不会被覆盖。修改环境变量后重启服务。

人工复核的“查看对比依据”展示匹配分数和实体、动作、时间对照，区分缺少信息与明确冲突。原始 JSON 收在“技术详情”中。

简报列表每个采集轮次只展示当前版本，审核后的更新回到同一个轮次卡片；点击“查看历史版本”可查看此前草稿及已确认、已投递版本。底层仍保留修订记录，不覆盖已确认或已发送内容；不同采集轮次分别展示。

页面使用 HttpOnly 会话 cookie；`/api/*` 和 `/ops/*` 也接受 `Authorization: Bearer <ADMIN_TOKEN>`。未配置管理员令牌时接口关闭访问。浏览器修改操作校验同源请求；令牌不存入 localStorage。部署到 HTTPS 时设置 `ADMIN_COOKIE_SECURE=true`。本阶段按 SQLite、单服务进程使用，不开多个 Uvicorn worker。

来源配置沿用 `.env.example`：知乎需要 `ZHIHU_ACCESS_SECRET`，微博需要本地 RSSHub；官媒 RSS 自动接入。基础配置的微博 CLI 默认关闭，Docker 部署模板则开启；启用后默认覆盖本轮全部入选 RSS 话题，配置、预算和降级见[数据源说明](data-sources.md)。本地语义匹配需要先[准备模型](event-matching.md#模型安装与运行)，也可显式使用 `MATCHING_PROFILE=rules`。

```powershell
docker compose -f ..\docker-compose.weibo.yml up -d rsshub
```

微博路由需要浏览器初始化访客会话，Compose 改用 `diygod/rsshub:chromium-bundled`；这是 [RSSHub 官方 Compose](https://github.com/DIYgod/RSSHub/blob/master/docker-compose.yml) 提供的部署方式。本次普通镜像曾因缺少浏览器返回 503，更换后路由返回 200。镜像依赖、上游连接与授权故障仍会按来源失败记录。

## 可配置采集与简报窗口

首页“采集任务”可以选择运行时长、采集间隔和结束后动作。保留夜间8小时／每小时一次预设，也支持留空时长持续运行，直到手动停止。不自动发送邮件。

“根据已有数据生成简报”支持本次运行、过去24小时和自定义时间范围。审核后保持原窗口和采样配置，结果入口显示当前版本。操作步骤、归并规则、兼容说明与限制见[采集窗口与归并说明](collection-windows-and-merging.md)。

## 生成、采样与导出

后台点击“采集新一期”，或在 `backend` 目录执行：

```powershell
..\.venv\Scripts\python.exe -m app.briefing_cli run
..\.venv\Scripts\python.exe -m app.briefing_cli status
..\.venv\Scripts\python.exe -m app.briefing_cli export 1 --directory data/briefing-mvp/export
```

将 `1` 替换为实际日报版本号，导出 `briefing.json`、`briefing.html`、`briefing.txt`。这些文件、运行数据库及本地截图均被 Git 忽略。

持续使用可设置 `SCHEDULER_ENABLED=true`，`SAMPLING_INTERVAL_MINUTES=180`（允许 120–180）。API 启动时运行协调器；默认关闭自动采样，但仍处理手动任务。数据库队列、唯一活动任务约束和租约控制重复触发；默认任务上限 900 秒，超时结束子进程并记录失败。失败任务保留原轮次，下一次采样使用新 ID；同一轮次回放不会重新请求来源。

只进行一次有限观察时，保持 `SCHEDULER_ENABLED=false`，执行：

```powershell
..\.venv\Scripts\python.exe -u -m app.briefing_cli observe --hours 24 --record data/briefing-mvp/observation.json
```

正常情况下在第 0、3、6……24 小时采样，边界轮次完成后退出。记录中的 `completed` 仅表示观察程序执行完毕，仍需检查每轮状态、采样间隔和来源可用性。电脑休眠造成的缺口不会补造成历史观测。不要同时启动多个观察程序；已有 API 时它们共用同一任务队列。前台运行可用 Ctrl+C 结束；后台运行记录进程 ID，停止前核对该进程的命令确实为本项目观察入口。

服务更新或进程重启后，在原 24 小时窗口尚未结束时可以加 `--resume` 继续同一记录，保持原截止时间和下一个采样时刻，并从数据库补齐日报版本号。新观察需使用新的 `--record` 路径，避免覆盖既有记录。

## 日报口径与审核

- 常规日报窗口为 `(截止时间 - 24 小时, 截止时间]`，默认以 `Asia/Shanghai` 显示；不用生成／审核时间伪造上榜时间。
- 三个平台栏目展示知乎、微博、官媒关注的事件，另有 A–F／unknown 来源分类视图。统一事件 ID 去重，同一事件可出现在多个栏目。
- 知乎显示真实榜位和可比排名变化；微博显示平台内分数和可比变化。RSS 顺序不是官方排名，RSS-only 或历史不足显示“趋势待确认”。官媒只展示报道和来源覆盖。
- 当前离榜事件在 24 小时关注窗口内仍可展示。合法短榜／空榜可证明离榜；失败、已知不完整和过期观测保留未知。官媒旧报道、无可靠发布时间的报道不会作为当天新热点。
- 知乎热榜触发本平台最多三个问题的搜索增强；微博 CLI 仅增强自身 RSS 话题。不从一个平台触发另一个平台搜索，不强行形成跨平台类别。
- 仅已接纳的关联参与分类。后台展示候选与目标事件、来源和匹配／冲突信息，支持归并、单独建事件、忽略、拒绝。搜索增强不能自行创建热点。

审核后按原始采集时间重算分类和趋势，并另存更正草稿。官媒搜索复用已有缓存，审核本身不新增外部查询。审核决定已保存但重算失败的任务继续留在队列，可点击重试。已发布版本的内容不随之后的采集或审核改变。

质量检查阻止缺引用、非法链接、窗口混用等版本发布；来源部分失败、候选待审核等警告会保留在正文。已接纳的单平台热点可能具有较低的**分类置信度**，仍可进入 E 类；它与“尚未确认的事件合并候选”是两个概念。整个轮次没有成功来源时生成受阻断的结果，不作为正常日报发送。

## 邮件配置与发送

发件服务和日报调度在“邮件设置”中配置，收件人在“收件人管理”中新增、编辑、启用或停用。页面设置保存后无需重启，授权码在数据库中加密存储。日报通过质量检查并确认版本后，可以选择收件人手动发送；也可开启每日自动生成与投递。

完整的 SMTP 准备条件、环境变量示例、配置生效方式、首次发送、定时开关、名单规则及失败排查，统一见[邮箱配置与日报投递](email-delivery.md)。没有配置 SMTP 时仍可采集、审核、预览和导出。

## 用量、存储与验证

“运行与用量”显示过去 24 小时实际客户端 HTTP 请求／CLI 调用，分别统计次数、成功率、耗时和失败类型；每次重试、重定向单独计数。Tool 次数单列，不能当作 API 次数。统计涵盖 MVP 任务入口，不追溯旧脚本，也不包含 RSSHub 容器内部向微博发出的请求。

`REQUEST_LIMITS` 是每轮每源调用上限；知乎搜索每轮最多三个 query，官媒 query 每轮默认最多三个事件，各站点还有请求上限。`QUOTA_PROBE_ENABLED=true` 可启用知乎实际额度查询；查询字段注明来源和时间。其他来源余额、单次费用无法取得时显示未知，不根据请求次数估算余额或费用。

默认数据库为 `backend/data/app.db`，相对 SQLite 路径也以 `backend` 为基准。启动时增量创建分析轮次、日报版本、请求记录、额度快照、任务租约和邮件投递表及索引；既有业务数据保留。维护或备份时先停止采集服务，再备份数据库。第九周已补齐 Docker、按天日志和试运行准备，服务器实践见 [部署说明](deployment.md)。

```powershell
# backend 目录，离线回归
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check app tests scripts/verify_briefing_ui.py
# 可选：已有 Edge 与运行中的本地服务；只读检查，不确认、不发信
..\.venv\Scripts\python.exe -m pip install -e ".[ui]"
..\.venv\Scripts\python.exe scripts/verify_briefing_ui.py
```

版本验收范围、真实采集结果和仍待完成的实测见 [briefing-v0.1](../reports/briefing-acceptance-v0.1.md)。
