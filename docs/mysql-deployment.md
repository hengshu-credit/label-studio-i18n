# MySQL 与单一服务栈

应用、主动学习 worker 与专用 MySQL 放在同一个 `jianzhi-label-studio` Compose 项目中。应用仍使用本机 8080；MySQL 只开放服务栈内部网络，不占用其他项目的 3306。迁移工具按需运行且使用 `--rm`，不保留额外的预览、测试应用。

使用 MySQL 8.4.13、mysqlclient 2.3.0、utf8mb4/utf8mb4_0900_bin、严格写入模式与 READ COMMITTED。连接凭据从 Docker secrets 文件读取，保留原数据目录中的 Django Secret Key、模型文件和加密配置。翻译缓存是独立的本地缓存文件，继续由原数据卷保存。

## 配置与兼容性

- `compose.mysql.yaml` 提供可复用配置；现有本机实例继续使用父目录的 `compose.yaml`。
- 通过 `tools/create-mysql-secrets.py --directory ../.secrets` 生成受限的本地密码文件，已有值不会覆盖，不应提交该目录。
- 普通列表优先读取配置和当前页，完整总数随后刷新。完整标注流仍获取准确的个人队列。
- MySQL 使用独立的 JSON 数组聚合、兼容的 JSON 文本筛选和批量插入主键恢复；长标签保留完整内容，用 SHA-256 表达式唯一索引处理原有 2048 字符标题约束。
- PostgreSQL 专用迁移只在 PostgreSQL 上执行；MySQL 使用标准表结构及对应的 B-tree 索引。
- 索引覆盖项目总计、按更新时间查询、标注者任务去重、模型版本筛选和最新状态查询。索引名称与列可在 `tasks/0063_mysql_query_indexes.py` 和 `fsm/0005_mysql_state_index.py` 查看。

Django 对超过 255 字符的唯一 CharField 会给出保守的 MySQL 检查提示。组织邀请 token 的索引在 MySQL 8.4 的 3072 字节限制内，实际建表与唯一约束保留；未通过关闭检查来绕过限制。

## 一致性迁移

1. 先在空 MySQL 中执行全部 Django migrations，并通过兼容测试。
2. 等待正在运行的学习任务完成，停止应用与 worker 的写入，制作 SQLite 一致性备份并记录 SHA256。原 SQLite 文件和配置保留用于回退。
3. 在一次性迁移容器中运行 `tools/migrate-sqlite-to-mysql.py`，显式指定快照路径、SHA256、目标数据库和 checkpoint。目标须已建好表且没有任务、项目或用户数据。
4. 工具分批复制所有业务表，保留主键和自增高水位；中断后可从 checkpoint 恢复。Django migration 历史使用 MySQL 实际建表记录，不用 SQLite 的记录覆盖。
5. 对每张表核对行数，逐字段比较全部记录，检查外键。JSON 比较保留布尔、整数、浮点和 SQL NULL/JSON null 的区别；进度文件不记录 token/session 主键。
6. 仅在 checkpoint 显示 `phase: verified` 后切换应用与 worker 到 `DJANGO_DB=mysql`，检查登录、项目列表、筛选、标注、预标注和学习任务。

当前应用设置：`MYSQL_HOST=mysql`、`MYSQL_PORT=3306`、`MYSQL_NAME=labelstudio`、`MYSQL_USER=labelstudio`、`MYSQL_PASSWORD_FILE=/run/secrets/mysql-password`。

## 回退边界

切换前可直接恢复原 SQLite Compose 配置。切换后如果已产生新的标注或任务，应先保存 MySQL 备份并迁回新增数据，不能直接启动旧 SQLite 丢弃新写入。应用、worker、MySQL 的数据卷和原模型目录均应保留。

验证记录写入本地 `artifacts` 和父目录的 `LATEST-VERIFICATION.json`；这些记录与数据库备份不提交到公开仓库。

参考：[Django MySQL 数据库说明](https://docs.djangoproject.com/en/5.2/ref/databases/#mysql-notes)、[MySQL 8.4.13 发布说明](https://dev.mysql.com/doc/relnotes/mysql/8.4/en/news-8-4-13.html)。
