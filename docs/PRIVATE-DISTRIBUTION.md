# 源码与镜像发行

源码保存在公开的 `daidaiya7/media-dock` 仓库；`daidaiya7/media-dock-release` 提供面向部署者的发行资料。两个仓库都不包含生产数据库、实例凭据或业务数据；每位部署者使用自己的数据卷、凭据、账号及媒体服务。

## 1.1.0 发行内容

- 容器地址：`ghcr.io/daidaiya7/mediadock:1.1.0`，包含 Linux AMD64、ARM64 架构。
- 公开发行页附带同版本离线镜像，可用 `docker load` 导入。
- `Dockerfile.release` 在构建阶段用 Cython 将后端模块编译为本机扩展；最终阶段只接收编译产物、必要静态资源、运行依赖及许可声明。
- `scheduler.py` 与 `save_secret.py` 仅是加载编译模块的简短入口，业务逻辑不以应用 `.py` 明文分发。
- 现有 `Dockerfile` 用于源码开发和本地构建；公开发行镜像使用 `Dockerfile.release`。

镜像和前端程序仍可被分析，编译不代表加密或绝对防逆向。真正必须保密的逻辑应保留在维护者控制的服务端。

## 构建验证

```sh
docker build -f Dockerfile.release \
  --build-arg VERSION=1.1.0 --build-arg REVISION="$(git rev-parse HEAD)" \
  -t mediadock:release-1.1.0 .
```

独立的 `Dockerfile.release.dockerignore` 控制构建上下文。构建时检查最终应用文件清单，并验证编译模块导入、管理员初始化/登录、访问限制、外观与教程配置、图片上传和定时任务入口。

GitHub Actions 的 `Build release images` 工作流在两个架构的原生 runner 上构建，使用非 root / 只读容器复验并启动独立 Compose 环境，成功后推送架构镜像并组合多架构清单。离线镜像与校验值作为短期构建产物保存，检查后上传公开发行页。

不要发布 `compile` 构建阶段，或向公开缓存导出 `mode=max`：它们可能包含源文件。公开镜像与发行包不得加入实例 `.env`、订单、缓存、日志、秘密目录或生产图片。

## 镜像与源码权限

首次创建 GHCR 包后检查包可见性。匿名分发镜像需要将容器包设为 Public；镜像中不存储拉取 Token 或共享管理凭据。发布完成后，以未登录环境执行拉取或检查清单，并测试公开发行页下载的真实安装包。

## 升级和回滚

为每次发行保留明确版本及镜像摘要，不依赖 `latest` 追踪生产更新。升级前备份数据卷与秘密卷，保留原 Compose 项目名和 `.env`。不要执行 `docker compose down -v`。

回滚通常只需要恢复之前的 `MEDIADOCK_IMAGE` 并重新启动；若后续版本包含数据库迁移，应按该版本说明恢复配套备份。1.1.0 的新增外观和教程配置兼容原有数据，不迁移或删除订单。

## 许可与边界

保留上游 Apache 2.0 许可证与来源声明，以及运行依赖的许可证。公开源码不能取消上游已经授予的权利。当前没有设备绑定、在线激活、商业许可证校验或自动停用服务。
