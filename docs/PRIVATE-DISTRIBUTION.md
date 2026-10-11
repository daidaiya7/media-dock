# 私有仓库与镜像分发

## 推荐方式

源码仅保存在 GitHub Private 仓库。向朋友提供 `compose.yaml`、`.env.example`、必要使用文档和私有容器镜像的读取权限，每个人独立部署自己的数据卷、凭据、媒体服务和域名。这样不需要把当前站点的数据库、订单或 Token 发给别人。

朋友仍然可以提取镜像中的文件。本版本使用 Python，容器内包含可读取的程序文件；如果“朋友不能拿到实现”是硬性要求，应选择由你托管服务，或把真正需要保密的逻辑保留在你控制的 API 中。前端压缩、关闭 source map、编译 Python 或代码混淆都无法提供绝对防逆向保证。

## 首次保存到 GitHub

先在 GitHub 创建一个**空的 Private 仓库**，名称建议 `media-dock`。在本整理目录运行：

```sh
git init -b main
git add .
git diff --cached --stat
git diff --cached
git commit -m "Prepare MediaDock private distribution"
git remote add origin git@github.com:YOUR_ACCOUNT/media-dock.git
git push -u origin main
```

将 `YOUR_ACCOUNT` 替换为自己的账号。提交前检查待提交文件，不要加入 `.env`、备份、Docker 卷、运行日志或任何真实凭据。此流程使用全新历史；原项目的 `.git` 不应复制进来。

## 构建朋友可用的镜像

在支持 Docker Buildx 的构建机上，登录私有镜像仓库后发布明确版本：

```sh
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag ghcr.io/YOUR_ACCOUNT/media-dock:1.0.0 \
  --push .
```

需要构建器支持对应架构；两种架构应分别完成启动和业务验证后再宣布支持。镜像推送后确认 GHCR 包可见性为 Private，并仅为朋友授予包读取权限。GHCR 权限可能默认继承关联仓库；若要只给镜像访问而不开放源码，应在包设置中检查并调整继承关系与访问权限。

不要给朋友你的个人管理 Token。每位朋友使用自己的 GitHub 账号及最小范围的读取凭据登录，或改用支持独立拉取凭据的私有镜像仓库。登录凭据交给 Docker 凭据存储，不放进 Compose、README 或 Git。

## 朋友部署

只需提供部署文件和文档，不必克隆源码。朋友登录镜像仓库后，将 `.env` 中的镜像名改为你发布的版本：

```dotenv
MEDIADOCK_IMAGE=ghcr.io/YOUR_ACCOUNT/media-dock:1.0.0
```

```sh
cp .env.example .env
# 编辑 .env，填写镜像名；先保留本机访问设置完成初始化。
docker compose pull
docker compose up -d
docker compose ps
```

先通过 `http://127.0.0.1:59189/admin` 设置密码，再按 README 配置 HTTPS 入口和自己的上游服务。正式使用建议固定镜像摘要，避免同名标签被覆盖后未经评估升级。

无法提供镜像仓库时，也可以用 `docker save` 导出某个架构的镜像，朋友用 `docker load` 导入，再通过 Compose 部署。离线镜像仍包含实现文件，同样不能保密。

## 当前保护边界

- 私有仓库限制源码的直接获取范围；管理好协作者、访问令牌和构建日志。
- 镜像不嵌入真实凭据，数据与密钥挂载到独立卷；不同实例不共用生产 Token。
- 容器使用非 root 用户、只读根文件系统、移除 capabilities，并默认只监听宿主机回环端口。
- 当前没有商业许可证校验、设备绑定、授权服务器或自动停用功能。隐藏后台路径也不能代替身份认证。
- 任何上游许可证权利仍然有效，不能用私有仓库设置或新增条款取消第三方已有授权。

如未来需要正式商业分发，可再设计版本签名、授权服务和升级流程。应先确定在线依赖、离线宽限期、隐私范围与故障行为，避免授权服务故障影响正常使用。
