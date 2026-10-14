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
