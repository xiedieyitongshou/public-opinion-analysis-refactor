# 阿里云服务器环境准备记录

[返回项目首页](../README.md) · [部署指南](../docs/deployment.md) · [邮箱配置](../docs/email-delivery.md)

## 范围与结果

2026-10-14，通过已核验主机指纹的 SSH 配置 Alibaba Cloud Linux 4.0.3 x86_64 服务器。本次完成宿主机环境准备，尚未完成应用镜像构建或业务部署；没有启动采集、发送邮件或开始 24 小时试运行。

| 组件 | 实测结果 |
| --- | --- |
| Docker 兼容引擎 | 系统原生 Moby 29.5.1，服务 active、开机自启 enabled |
| Compose | 2.26.1，项目 `docker compose config --quiet` 通过 |
| Buildx | 0.38.0，默认 docker driver 状态 running，支持 linux/amd64 |
| Git | 2.47.3 |
| 资源 | 2 核、总内存 1674 MiB，安装后可用约 1273 MiB；无 Swap；磁盘剩余约 34 GB |

引擎、Compose、Git 从系统源安装。系统源没有 Buildx 包，GitHub 发布附件下载过慢超时，改用阿里云 Docker CE 软件包镜像源，验证 Docker Release GPG 签名后安装插件。采用[阿里云官方安装说明](https://help.aliyun.com/zh/ecs/user-guide/install-and-use-docker)中的原生 Moby 路径，未禁用签名或 TLS 验证。

## 文件与配置

- 源码及构建配置：服务器 `/opt/public-opinion-analysis/app`，来自当前本地工作区白名单打包，不是 Git checkout，也不代表已推送 GitHub。
- 构建包：`/opt/public-opinion-analysis/setup/server-source.tar.gz`，SHA256 为 `7eca15d9eddd185006fc4db7e4a56bd36ce20736e2e9f7953cd7b82c492ccdd9`。包含应用、脚本、Dockerfile、依赖声明和 Compose，不含本地 `.env`、SSH 密钥、数据库或模型。
- 服务器部署环境文件：`/opt/public-opinion-analysis/app/deploy/.env`，权限 `600`。服务器本机随机生成独立 `ADMIN_TOKEN` 与 `EMAIL_SETTINGS_KEY`，没有打印或写入报告。
- `SCHEDULER_ENABLED`、`DAILY_BRIEFING_ENABLED`、`EMAIL_SCHEDULE_ENABLED` 均设为 `false`；模型线程数为 2，微博 CLI 开关保持启用。
- 知乎、微博 CLI 和 SMTP 凭据尚未填写，模型尚未上传；没有修改本地实际 `.env`。

## 阻塞与后续

Docker Hub 的 `registry-1.docker.io` 连接超时，实际 `docker pull python:3.11-slim-bookworm` 失败。PyPI、PyTorch CPU wheel 索引和 npm 注册表在此前网络探测中返回 HTTP 200，但这不代表完整依赖下载或构建已经通过。

需要先解决基础镜像和 RSSHub 镜像获取，可使用可访问且可信的镜像仓库，或在具有 Docker 的联网环境构建 Linux 镜像后上传导入。未配置未经核验的公共镜像代理。

镜像可用后，再按 Dockerfile 安装 Python 3.11、Node 22、CPU PyTorch、锁定依赖及微博 CLI；这些属于容器运行环境，本次未在宿主机另装全套 Python/Node 依赖。随后上传模型、配置来源认证，验证内存峰值与真实 SMTP 收件，最后开启限时试运行。当前没有业务容器。

## 当日国内镜像重试

DaoCloud 最初能返回清单，但下载镜像层出现 502；轩辕免费入口对相同 Python 摘要返回 403。用户要求再次尝试后，通过 `m.daocloud.io/docker.io/` 显式前缀成功下载以下镜像：

| 镜像 | 已下载摘要 |
| --- | --- |
| Python 3.11 slim bookworm（linux/amd64） | `sha256:8a00ecb83ed9d0864dfd102a1bdb2067fda907ceb30bb86d09da9ee558b19590` |
| Node 22 bookworm slim | `sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392` |

RSSHub 仍使用项目固定摘要 `sha256:3c5f0b37a54223c4735b5d6428d2886d97ae24e04297a619fb211a2bc9e174aa`，本次有多个镜像层下载并解包，但在 240 秒限时尝试内未获得完整拉取成功结果。不能认定 RSSHub 已就绪。没有修改 Docker 全局镜像源、启动业务容器或执行应用构建。

## 当日晚间 RSSHub 部署完成

继续尝试原摘要后，国内代理部分 blob 下载仍卡住，镜像配置文件的跳转目标也返回 403。改用 RSSHub 官方 GHCR 的 Chromium 镜像，完整下载并锁定为 `ghcr.io/diygod/rsshub@sha256:f4ff60ff6cb19290ba7a3cfa0c81abe657b778c17fac5303457970edf3fc52a4`。这是不同于原摘要的新版本，源码 revision 为 `66411e5503c001ebcfe7e25e9d5ec2d01bbf0dfa`，构建时间为 `2026-10-14T13:36:36.130Z`。

服务器使用[GHCR 覆盖配置](../deploy/compose.rsshub-ghcr.yml)，与根 `compose.yml` 一起执行：

```bash
cd /opt/public-opinion-analysis/app
docker compose -f compose.yml -f deploy/compose.rsshub-ghcr.yml up -d --no-deps --pull never --wait --wait-timeout 90 rsshub
```

- 容器 `opinion-rsshub-1` 启动成功，状态 `healthy`，`/healthz` 返回 HTTP 200。
- 容器内请求 `/weibo/search/hot` 返回 HTTP 200、XML 和 52 条 RSS item，响应约 24 KB。项目仍按自身配置限制采样条数，不因上游返回 52 条而扩大采样预算。
- 此次观测内存约 389.2 MiB，仅为 RSSHub 路由验证时的值，不代表整个项目的峰值内存。
- 未发布公网端口；后端使用 Docker 内部地址 `http://rsshub:1200`。Docker 日志按 Compose 中的大小策略轮转。
- 根 Compose 的默认镜像保持原值；后续服务器启动／更新也必须带上 GHCR 覆盖文件，避免切回不可下载的原仓库。
- 后端镜像尚未构建，微博 CLI 尚未授权，自动采集和邮件发送尚未开启。已停止本任务原先卡住的镜像拉取。

## 微博 CLI 授权与搜索验证

后续构建专用授权镜像 `opinion-weibo-auth:0.9.1`，固定 CLI 版本为项目使用的 0.9.1。用户在微博官方页面确认 `aliyun-opinion-server` 设备会话后，CLI 登录成功。设备码、访问令牌和刷新令牌不写入报告。

- 新建临时容器执行 `auth whoami` 与 `doctor`，确认登录、开发者认证和正式服务均就绪。
- 登录凭据保存在 `opinion_cli-home` 卷；容器 UID 10001、家目录 `/home/app` 与后端一致，新建容器可复用凭据。未导出令牌到 `.env`。
- 执行一次 `search statuses/limited --q 人工智能 --type 1 --sort time --count 10 --output json`，返回有效 JSON 和 10 条 `statuses`。仅此一次搜索，未执行全话题增强。
- 原始测试响应位于服务器 `/opt/public-opinion-analysis/setup/weibo-cli-search-smoke.json`，受 `umask 077` 保护，不提交 Git，也不展示原始微博正文。
- 专用 Compose 使用独立项目名 `opinion-cli-auth` 和外部凭据卷，不影响正在运行的 RSSHub。登录容器退出即删除，凭据卷保留。
- 后端镜像构建、模型上传、完整业务采集及邮件联调仍待完成；CLI 授权和单次搜索成功不等于完整分析链路已部署。
