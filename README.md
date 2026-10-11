# 影视服 · 观影服务平台

**MediaDock — Self-hosted Media Service Portal**

影视服是面向自托管媒体服务的观影门户，把影视库浏览、影片搜索、求片申请、观影套餐、订单管理、卡密交付、使用教程和线路状态展示集中在一个入口。平台采用黑金主题，可连接 Emby、NextEmby 和 NextFind，并通过管理后台维护套餐、公告与服务配置。

它适合已经拥有媒体服务、希望为用户提供统一入口的运营者。影视服不附带影片、媒体服务器账号、商业 CDN 或支付渠道；实际可用内容与播放能力取决于所连接的服务。

本仓库按**私有维护、镜像分发**方式整理。当前为独立脱敏版本，不包含生产数据库、客户订单、站点凭据或原部署记录。

## 主要功能

- **影视库**：媒体库封面、影片搜索、海报与详情展示。
- **求片订阅**：访客提交想看申请，管理员审核；支持连接兼容的 NextFind 服务。
- **观影套餐**：套餐展示、购买提示和订单登记。
- **订单与交付**：后台核对订单、调用 NextEmby 发卡、生成卡密领取链接。
- **站点内容**：首页公告、简介、教程和热门影片展示。
- **服务状态**：服务端探测和浏览器侧 HTTPS 响应时间测量。响应时间不等于下载带宽，也不保证实际播放体验。
- **服务端凭据管理**：连接密钥保存于服务端，订单卡密加密存储。

默认套餐是禁用的示例配置，集成服务尚未连接。请完成初始化与配置后再开放给用户。

## Docker 快速启动

准备支持 Docker Compose v2 的 Linux 主机或 Docker Desktop。维护者从本私有源码目录构建镜像：

```sh
cp .env.example .env
docker build -t mediadock:local .
docker compose up -d
docker compose ps
docker compose logs --tail=100 mediadock
```

浏览器访问 `http://127.0.0.1:59189/admin`，首次进入设置管理员密码；随后使用环境变量 `MEDIADOCK_ADMIN_USERNAME` 指定的用户名登录，默认是 `admin`。**先完成初始化，再开放公网入口。** 默认端口只绑定本机回环地址。

如果 Docker 在远程服务器上，在自己的电脑建立 SSH 隧道后访问同一地址：

```sh
ssh -L 59189:127.0.0.1:59189 user@your-server
```

公开访问时，在 `.env` 设置实际域名、HTTPS 要求和反向代理 IP，再重建容器：

```dotenv
MEDIADOCK_PUBLIC_BASE_URL=https://media.example.com
MEDIADOCK_REQUIRE_HTTPS=1
MEDIADOCK_TRUSTED_PROXIES=172.18.0.1
```

上面的代理 IP 是示例，必须换成容器实际看到的代理连接 IP。宿主机 Nginx 经 Docker 映射端口连接时通常是该 Docker 网络的网关，可用 `docker inspect` 查看容器的 `NetworkSettings.Networks` 中的 `Gateway`，并结合实际网络验证。不要设置为 `*`。否则可能出现 HTTPS Cookie、跨站来源检查或请求限速来源错误。

```sh
docker compose up -d
```

反向代理需要保留 `Host`、正确设置 `X-Forwarded-Proto`，并覆盖客户端提供的 `X-Forwarded-For`。参见 [Nginx 示例](deploy/nginx.conf.example)。公开入口必须配置有效 TLS 证书；域名必须与 `MEDIADOCK_PUBLIC_BASE_URL` 一致。

## 配置与使用

1. 在管理后台填写本站链接、公告标题和简介；更换演示文案。
2. 在 `.env` 配置自己的 `MEDIADOCK_NEXTEMBY_URL`，重建容器；后台录入独立 API 凭据并检查连接。
3. 配置 Emby 影视库、NextFind、TMDB 与监测项目，只启用需要的集成。
4. 核对套餐模板、有效天数、价格、购买入口和交付方式，再启用套餐。默认套餐 ID 与模板存在业务校验，修改结构前应阅读 `portal/core.py`。
5. 用自己的测试订单走完登记、核对、发卡和领取流程，再正式使用。

公开页面：`/` 首页、`/library` 影视库、`/discover` 求片、`/plans` 套餐、`/guide` 教程、`/status` 状态、`/claim` 领取。后台为 `/admin`。

各项服务和凭据的填写位置见 [配置清单](docs/CONFIGURATION.md)。使用说明、备份和故障排查见 [使用指南](docs/USAGE.md)。面向朋友分发镜像见 [私有发行说明](docs/PRIVATE-DISTRIBUTION.md)。

## 文件与数据

| 位置 | 用途 |
| --- | --- |
| `portal/` | Python 服务与静态前端 |
| `product-mapping.json` | 初始套餐模板 |
| `compose.yaml` / `.env.example` | 容器部署与环境配置 |
| Docker 卷 `mediadock-data` | 订单数据库、站点配置和缓存 |
| Docker 卷 `mediadock-secrets` | 连接凭据与卡密加密密钥 |

Compose 通常为卷名称添加项目名前缀。迁移时同时保留两个卷；仅复制数据库会导致已有卡密无法解密。容器使用 UID/GID `10001:10001`；凭据目录必须归该用户所有且权限为 `0700`，凭据文件为 `0600`。建议使用默认命名卷。不要执行 `docker compose down -v`，该命令会删除业务数据和密钥。

## 私有发布与代码保护

建议源码保存在 GitHub **Private** 仓库，朋友只拿部署文件和私有镜像读取权限。Docker 解决部署与依赖一致性问题，不是加密或防逆向机制。本版本镜像包含 Python 程序文件；有镜像或服务器管理权限的人可以提取分析。浏览器必须下载的前端代码同样可查看。

需要高度保护的专有逻辑应留在你控制的服务器 API；编译或混淆只能提高分析成本，无法保证“AI 无法逆向”或“绝对不能破解”。本版本没有添加离线授权、远程激活、许可证服务器或自动更新检查。

首次推送时只对本整理目录建立新的 Git 仓库，避免带入旧历史；不要从原运行目录直接推送。 `.gitignore` 只保护未跟踪文件，不能清除已经提交过的凭据。上传步骤见私有发行说明。

## 许可与来源

保留了 [Apache License 2.0](LICENSE) 和 [上游声明](NOTICE)。历史项目涉及 [Christ9038/Ydisks-Xianyu-Helper](https://github.com/Christ9038/Ydisks-Xianyu-Helper)，本整理版保留闲鱼与小红书渠道适配代码，但不包含原账号、商品、代理、凭据或生产部署。依赖软件各自的许可证继续适用。

私有仓库不代表可以取消上游已有的许可证权利。公开分发或签署商业授权前，应明确自己新增代码的授权范围，并核实随镜像分发的依赖许可；此处不把上游代码宣称为独占私有代码。
