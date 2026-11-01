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

### 标注文本翻译

标注页右侧的「翻译」标签提供 Azure Translator、Google Cloud Translation NMT 和百度通用翻译。默认原文语言为自动识别、目标语言为简体中文，支持泰语等短信语言及手动切换。只在点击「翻译当前文本」时发送当前任务的原文，同一文本和语言组合复用缓存；切换任务或语言会清除旧译文。原文、实体位置和人工标注不受影响。

组织所有者在面板「API 设置」中输入相应 Key；Azure 可填区域，百度需 App ID 和密钥。Key 在服务器按组织加密保存，响应不回显，浏览器不持久化密钥。空字段保留原值，「移除已保存的密钥」清除该引擎配置。翻译数据和配置保存在本地数据卷的 `translation-cache.sqlite3` 中，备份及升级时应保留整个数据卷与 Django Secret Key；更换 Secret Key 后须重新配置密钥。

未手动选择引擎时，优先使用已配置且本地每月字符上限最高的引擎；都未配置时默认显示 Azure。初始本地上限为 Azure 200 万、Google 50 万、百度标准版 5 万字符；百度高级版用户可自行调整至 100 万。额度按 UTC 自然月计数，请求失败也保守计入，达到上限停止新调用。该上限不等于服务商账单，应用外使用量、套餐资格和收费由各服务商账户决定。

官方套餐说明：[Azure](https://azure.microsoft.com/en-us/pricing/details/translator/)、[Google](https://cloud.google.com/products/translate/pricing)、[百度](https://fanyi-api.baidu.com/product/112)。必须先开通相应官方 API，界面不提供共享密钥。

### 本地 Transformers 主动学习

项目模型后台可配置 Hugging Face 缓存 ID 或已训练模型本地目录，分类与实体识别分别选择；支持人工标注训练、版本保存、候选池刷新与低置信度优先采样。需要启动本地推理服务及 Compose 中的主动学习 worker。详见 [配置、模型格式与边界说明](ml_backend/README.md)。

### 预标注模型选择

在「项目设置 → 标注 → 预标注」选择连接的模型或已存储预测版本，点击保存。重新打开任务时，后端调用所选模型；已有其他模型的预测不会阻止获取当前模型结果，同一模型已缓存结果会复用。人工标注和草稿保留。分类与 NER 若需要单独选择，应分别添加对应 ML 服务连接。

短信规则/Qwen3/GLiNER 服务支持普通预测，连接时须关闭「交互式预标注」；新版上游会跳过交互式模型的批量/普通预标注。可将 `tools/configure-sms-backends.py` 复制到容器，并以 `--project <项目ID> --service-url http://host.docker.internal:9091` 执行，创建合并、分类、NER 三个连接并修正此设置。该脚本仅适用于已运行且提供 `/classification`、`/ner` 路由的本地短信服务，不改变当前模型选择或人工结果。

CI 另包含翻译权限、密钥加密、不回显、缓存、额度及三个官方请求契约测试，以及模型实际选择回归。云端请求使用模拟响应，真实密钥下的翻译质量和账户额度须在配置后验证。

SQLite 百万级任务项目若列表长时间加载，可在停止服务并备份后运行 `python tools/optimize-sqlite-listing.py --database <数据目录>/label_studio.sqlite3`，随后重新启动服务。此工具增加项目、标注数、预测数、任务 ID 的覆盖索引，使分页统计避免扫描短信正文；只增加索引，不改写任务或标注。CI 在合成数据库上执行该工具后再运行接口和浏览器回归。

### 界面语言资源

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
