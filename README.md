# Label Studio 中英文适配

本仓库保存 Label Studio 的中英文定制源码补丁、可编辑翻译资源、Docker 构建配置和自动化验证。上游源码通过固定提交下载，`upstream/` 不重复提交；`patches/localization.patch` 是实际源码改动，不是浏览器翻译插件或 DOM 文本替换。

## 上游版本

- 基于 [HumanSignal/label-studio](https://github.com/HumanSignal/label-studio) 的 `develop`，提交 `80436a3f82675b816a8ce3d8b3e8cd0371bd119e`，版本 `1.24.0.dev0`。
- 该提交是 2026-11-01 开始升级时核对的最新开发分支；最新稳定版当时仍为 1.23.2。
- 国际化基础参考 [PR #9898](https://github.com/HumanSignal/label-studio/pull/9898)，提交 `f492bfc2a90c8cc1da2f25fd9121ef0de40ade7f`，并在新版本上修复、扩展。
- 运行时使用上游支持的 Python 3.12 与固定的 `uv.lock`，避免 Alpine 上大型原生依赖的现场编译。前端沿用上游 Bun/Vite 构建。

## 覆盖范围

- 登录、注册、错误页与服务器端表单。
- 首页、项目列表、新建项目、导入、导出和项目设置。
- 组织成员、账户资料、语言设置、访问令牌和快捷键。
- 数据管理的列、筛选、排序、任务操作、存储、模型连接与预测。
- 标注编辑器的提交、跳过、撤销、区域、关系、预标注，以及图像、音视频、文本、时间序列工具提示。
- Ant Design 的内置文案、数据管理日期选择器，以及中文本地提示。

任务原文、用户自定义标签、项目名称、接口字段、控件名称和导出值保持原样。第三方品牌、协议名、格式名及代码示例保留必要的原文。新上游版本新增的未翻译文案回退到英语。

账户 → 语言与地区可选择 English／简体中文；登录页也提供语言选择。账户切换会保存浏览器偏好、同步 Django 语言 Cookie，并刷新页面，使模块级菜单常量与服务器页面一并更新。

## 构建与启动

需要 Git、Python 3.11+、Docker Compose；本机 Windows 工作区使用已有 Anaconda base 运行准备脚本。

```sh
python tools/prepare.py
docker compose build label-studio
docker compose up -d --wait --wait-timeout 300
```

访问 http://localhost:8080 。首次使用创建本地账户。默认将 `./data`、`./local-files` 绑定挂载到容器，可在未提交的 `.env` 中设置 `LABEL_STUDIO_DATA_DIR`、`LABEL_STUDIO_LOCAL_FILES_DIR`、`LABEL_STUDIO_PORT`。

已存在的实例升级前应停止服务并备份整个数据目录。开发分支可能包含数据库迁移，回退时使用旧镜像与升级前备份，不能假设降级镜像能读取迁移后的数据库。

## 翻译维护

- `catalog/en.json`：英语自然语言源文案。
- `catalog/zh-CN.json`：运行时简体中文翻译。
- `catalog/reviewed.json`：重点流程、术语、动态文案的人工校订记录。
- `tools/extract-ui.cjs`：从 React/TypeScript 的可见文案位置提取字符串。
- `tools/extract-server.py`：提取 Django 模板和服务器端界面元数据。
- `tools/compile-catalogs.py`：生成前端资源及 Django `.po`/`.mo`。

占位符如 `{{value0}}`、`%(name)s` 必须原样保留。使用源码调用而非全局字符串替换，避免把数据内容或 API 标识当作界面文案翻译。

```sh
npm ci
npm test
python tools/compile-catalogs.py
```

更新上游时，修改 `upstream.lock.json`，在干净源码目录重新移植补丁、提取新增文案、审阅并补齐翻译，然后重新执行构建和浏览器测试。准备脚本会拒绝覆盖不匹配的提交或无关本地改动。

## 验证与 CI

GitHub Actions 包含中英文资源矩阵、占位符校验、真实前端国际化单元测试、完整 Docker 构建，以及 Chromium 登录／语言切换／刷新保留测试。发布与本地部署以最终提交 SHA 的所有已触发检查通过为准。

标注数据、账户数据库、访问令牌、模型权重、推理缓存和机器本地配置不纳入仓库。

## 许可证与来源

本项目沿用 Apache License 2.0。上游版权与许可声明保留；国际化基础的贡献者为 PR #9898 作者 wanxinlei1。详见 `LICENSE` 与 `NOTICE`。
