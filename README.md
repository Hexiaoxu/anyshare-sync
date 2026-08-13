# AnyShare → BISHENG 文档同步工具

将 AnyShare 的知识库、部门文档库和个人文档库单向迁移到 BISHENG，并同步目录结构与 ACL 权限。项目支持 Docker 部署、首次全量迁移以及基于 AnyShare Console 操作日志的后续增量同步。

> 当前生产镜像：`anyshare-sync:latest`。镜像默认命令是 `python3 daemon.py`，默认工作目录是 `/app`。

## 1. 当前能力

| 能力 | 知识库 | 部门文档库 | 个人文档库 |
|---|---:|---:|---:|
| 递归扫描目录 | ✅ | ✅ | ✅ |
| 创建 BISHENG 目录 | ✅ | ✅ | ✅ |
| 下载并迁移文件 | ✅ | 可选 | ✅ |
| 同步 ACL | ✅ | ✅ | 仅授予个人 owner |
| 全量迁移 | ✅ | ✅ | ✅ |
| 按 `rev` 跳过未变化文件 | ✅ | ✅ | ✅ |
| Console 日志增量 | ✅ | ✅ | 部分支持 |

当前主流水线对可迁移的 AnyShare ACL 统一授予 BISHENG `viewer`：ACL 必须包含 `download`，并且不能包含 `deny`。个人库会额外把对应用户授予空间 `owner`。

## 2. 代码入口

```text
run.py                      统一命令入口
daemon.py                   Docker 默认入口，周期拉取 Console 日志
discover.py                 发现知识库、部门库和个人库
migrate_all.py              全量迁移所有库（调用专用迁移脚本）
sync_dept_lib.py            单个知识库/部门库迁移
sync_one_user.py            单个个人库迁移
app/sync_pipeline.py        核心同步流水线
app/tree_orchestrator.py    按 config.yaml 中的 trees 组织空间
app/services/log_scheduler.py
                            checkpoint 与日志增量调度
```

推荐使用以下两条主路径：

- 知识库和部门库首次迁移：`python3 run.py --tree`。
- 后续日志增量：`python3 daemon.py`。

`migrate_all.py`、`sync_dept_lib.py` 和 `sync_one_user.py` 是仍在使用的专用脚本，但部分行为与统一 `SyncPipeline` 不完全相同。

## 3. 配置

配置文件为 `config/config.yaml`，镜像运行时不会内置该文件，必须挂载到 `/app/config/config.yaml`。

```yaml
anyshare:
  base_url: "https://your-anyshare-host"
  timeout: 30
  client_id: "your-client-id"
  client_secret: "your-client-secret"
  admin_account: "admin-account"
  console_user_id: "console-user-id"

bisheng:
  base_url: "http://your-bisheng-host:3001"
  timeout: 30
  jwt_secret: "your-bisheng-jwt-secret"
  jwt_issuer: "bisheng"
  jwt_expire_seconds: 86400
  jwt_admin_user_id: 1
  jwt_admin_user_name: "admin"
  jwt_admin_tenant_id: 1
  jwt_admin_token_version: 1

database:
  type: "dameng"                # dameng | sqlite
  sqlite_path: "data/sync_state.db"
  host: "your-db-host"
  port: 5236
  user: "your-db-user"
  password: "your-db-password"
  schema: "BISHENG_FOR_AISHU"

sync:
  trees:
    - space_name: "知识库"
      type: "knowledge_doc_lib"
      no_root_perms: true
      items:
        - name: "公司资质"
          gns: "gns://YOUR_GNS_ID"

    - space_name: "部门文档库"
      type: "department_doc_lib"
      no_root_perms: true
      skip_download: true
      items:
        - name: "财务部"
          gns: "gns://YOUR_GNS_PATH"
```

配置含密码和 JWT 密钥，不要提交真实配置、打印完整配置或将其打入镜像。建议只提交 `config.example.yaml`，生产配置通过只读卷挂载。

### trees 的含义

- 一个 `tree` 对应一个 BISHENG 知识空间。
- `items` 中的每个 AnyShare 文档库作为该空间下的一棵子目录树。
- `no_root_perms: true` 表示不迁移文档库根权限，只处理子目录和已迁移文件。
- `skip_download: true` 表示只创建目录和同步目录权限，不下载文件。
- `ancestors` 可以用逗号分隔的字符串指定额外的上级目录。

## 4. Docker 镜像

### 4.1 构建和导入

在项目根目录构建：

```bash
docker build -t anyshare-sync:latest .
```

从离线包导入：

```bash
docker load -i anyshare-sync.tar.gz
```

验证镜像：

```bash
docker image inspect anyshare-sync:latest \
  --format 'image={{.Id}} created={{.Created}} cmd={{json .Config.Cmd}}'
```

当前 Dockerfile 基于 Python 3.11，并包含 Linux amd64 版达梦驱动，因此该镜像应运行在 `linux/amd64` 环境。若目标服务器是 ARM64，需要准备对应架构的达梦驱动并重新构建镜像。

### 4.2 目录挂载

| 宿主机目录 | 容器目录 | 用途 |
|---|---|---|
| `./config` | `/app/config` | 配置，Compose 中只读 |
| `./data` | `/app/data` | checkpoint、SQLite、发现结果 |
| `./logs` | `/app/logs` | 守护进程日志 |
| `/tmp/anyshare-sync` | `/tmp/anyshare-sync` | 临时目录 |

启动前创建持久化目录：

```bash
mkdir -p data logs /tmp/anyshare-sync
```

## 5. 首次部署流程

首次部署必须先完成组织和全量数据准备，再启动增量 daemon。

### 5.1 检查配置和连通性

确认以下服务可从容器访问：

- AnyShare API。
- BISHENG API。
- 达梦数据库（使用 `database.type: dameng` 时）。
- AnyShare 管理员账号具有扫描、下载、ACL 和 Console 日志权限。
- BISHENG 已配置创建知识空间所需的模型。

如果使用达梦，表结构必须提前由部署方初始化。当前镜像不包含 README 旧版本提到的 `init_dameng_sql.py`，不能通过该命令建表。

### 5.2 发现文档库

先只读预览，不修改配置：

```bash
docker compose --profile job run --rm sync-job \
  python3 discover.py --dry-run
```

`discover.py` 非 dry-run 模式会重写 `config/config.yaml` 的 `sync.trees`，但 Compose 将配置目录挂载为只读，因此不能直接通过上述服务写回配置。需要写回时，可在备份配置后临时使用可写挂载：

```bash
docker run --rm \
  -v "$(pwd)/config:/app/config" \
  -v "$(pwd)/data:/app/data" \
  anyshare-sync:latest python3 discover.py
```

该命令会把知识库和部门库写入 `config.yaml`，把个人库写入 `data/personal_libs.json`。

### 5.3 组织导入

从 Excel 导入部门和用户时，需要额外挂载 Excel 文件：

```bash
docker run --rm \
  -v "$(pwd)/config:/app/config:ro" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/users.xlsx:/input/users.xlsx:ro" \
  anyshare-sync:latest \
  python3 run.py --import-org /input/users.xlsx
```

注意：当前导入逻辑将 `users_import.json` 写到容器 `/app`，容器删除后文件不会保留。如果后续要批量迁移个人库，应单独持久化或导出该文件。

### 5.4 首次全量迁移

按 `config.yaml` 中的 `sync.trees` 迁移知识库和部门库：

```bash
docker compose --profile job run --rm sync-job \
  python3 run.py --tree
```

当前配置中部门库设置了 `skip_download: true`，所以部门库只迁移目录和权限。需要迁移文件时，确认管理员 Token 有下载权限后，将该值改为 `false`。

全量模式会删除 BISHENG 中同名的旧空间并重新创建。生产执行前应确认空间名称和影响范围。

### 5.5 启动增量守护进程

```bash
docker compose up -d sync-daemon
docker compose ps
docker compose logs -f --tail=200 sync-daemon
```

停止：

```bash
docker compose down
```

仅执行一轮增量检查：

```bash
docker compose --profile job run --rm sync-job \
  python3 daemon.py --once
```

第一次运行且不存在 `data/log_sync_checkpoint.json` 时，daemon 只把 checkpoint 初始化为当前时间，不会补拉历史日志。因此顺序必须是：先全量迁移，再立即启动 daemon。

## 6. 常用命令

### 单个文档库

```bash
docker compose --profile job run --rm sync-job \
  python3 run.py "gns://SOURCE_ID" "目标空间名"
```

按数据库中的 `source_rev` 跳过未变化文件：

```bash
docker compose --profile job run --rm sync-job \
  python3 run.py "gns://SOURCE_ID" "目标空间名" --incremental
```

### 单个部门库

默认只同步目录和权限：

```bash
docker compose --profile job run --rm sync-job \
  python3 run.py --sync-dept "财务部" "gns://SOURCE_ID"
```

同时迁移文件：

```bash
docker compose --profile job run --rm sync-job \
  python3 run.py --sync-dept "财务部" "gns://SOURCE_ID" --with-files
```

### 单个个人库

```bash
docker compose --profile job run --rm sync-job \
  python3 run.py --user USER_ACCOUNT
```

程序会自动获取用户 Token、查找个人库 GNS、创建空间并授予该用户 `owner`。

### 查看 AnyShare 文档库

```bash
docker compose --profile job run --rm sync-job python3 run.py --list knowledge
docker compose --profile job run --rm sync-job python3 run.py --list department
docker compose --profile job run --rm sync-job python3 run.py --list personal
```

### 全库专用脚本

```bash
docker compose --profile job run --rm sync-job python3 migrate_all.py
docker compose --profile job run --rm sync-job python3 migrate_all.py --knowledge
docker compose --profile job run --rm sync-job python3 migrate_all.py --dept
docker compose --profile job run --rm sync-job python3 migrate_all.py --personal
docker compose --profile job run --rm sync-job python3 migrate_all.py --with-files
```

迁移个人库前需要准备 `data/personal_libs.json`；如需显示名到账号的精确映射，还需要让容器内可以读取 `/app/users_import.json`。

## 7. 全量同步流水线

单个文档库由 `SyncPipeline.run()` 按以下顺序处理：

1. 全量模式删除同名空间并新建；增量模式复用同名空间。
2. 创建配置指定的上级目录。
3. BFS 扫描 AnyShare 目录和文件，跳过常见压缩包。
4. 按父子关系创建 BISHENG 文件夹。
5. 从 AnyShare 下载文件，上传到 BISHENG MinIO 并注册知识文件。
6. 在数据库中写入扫描批次、空间映射、文件版本和审计记录。
7. 获取每个资源的 AnyShare ACL，映射 BISHENG 用户/部门并授权。
8. 写入权限快照并输出本次汇总。

单文件失败会记录警告并继续处理其他文件；整库发生未捕获异常时，返回 `error` 并写错误日志。

## 8. 日志增量流程

`daemon.py` 周期执行：

1. 从 `data/log_sync_checkpoint.json` 读取上次成功处理到的微秒时间戳。
2. 刷新 AnyShare 管理员 Token。
3. 拉取 Console 中组织类和文档类操作日志。
4. 根据事件类型创建、更新或删除文件/目录，刷新 ACL，或同步组织变化。
5. 全部事件处理成功后把当前时间写回 checkpoint；任何事件失败则保留旧 checkpoint，下一轮重试。
6. 等待下一轮，默认间隔 3600 秒。

删除事件的 `opType` 在不同 AnyShare 环境可能不同。必须先从本环境 Console 日志确认编号，再配置：

```yaml
sync:
  console_delete_op_types: []  # 例如确认后填写 [实际编号]
```

保持空数组时不会删除 BISHENG 资源，这是默认的安全行为。

可通过命令行覆盖间隔：

```bash
docker compose --profile job run --rm sync-job \
  python3 daemon.py --interval 1800
```

## 9. 数据和日志

主要状态包括：

| 状态 | 位置 |
|---|---|
| 增量时间点 | `data/log_sync_checkpoint.json` |
| 个人库发现结果 | `data/personal_libs.json` |
| SQLite 状态库 | `data/sync_state.db` |
| daemon 日志 | `logs/daemon.log` |
| 同步日志 | `logs/sync.log`、`logs/error.log` |
| 达梦映射数据 | 配置指定的达梦 schema |

排查命令：

```bash
docker compose ps
docker compose logs --tail=300 sync-daemon
docker inspect anyshare-sync-daemon
```

## 10. 当前已知限制

- 当前镜像为 `linux/amd64`，其中的达梦驱动不能直接用于 ARM64。
- 部门库没有可用下载权限时，只能设置 `skip_download: true`。
- BISHENG 管理员 JWT 会在到期前 5 分钟自动刷新；遇到 HTTP 或业务状态 401/403 时也会刷新并重试一次。
- daemon 会从空间、文件夹和文件映射表恢复增量上下文；首次启用本版本前应先执行一次全量或 rev 增量，让旧数据补齐目标 ID。
- 删除事件默认关闭，必须确认本环境 Console 的删除 `opType` 后配置 `console_delete_op_types`。
- `PermissionGate` 尚未接入主流水线；当前行为是上传资源后逐条跳过无法安全转换的 ACL。
- 权限翻译当前统一为 `viewer`，尚未按 AnyShare 的修改、删除等操作位映射成 `editor` 或 `manager`。
- `scheduler.daily_scan_time`、`daily_housekeeping_time` 等配置尚未接入当前 daemon；实际使用 `scheduler.interval_seconds`，缺省为 3600 秒。
- `docker-compose.yml` 中的顶层 `version` 字段对新版 Docker Compose 已过时，会产生警告，但不影响解析和运行。
- 测试套件当前缺少 `app.connectors.anyshare.mock`，执行 `pytest` 会在收集 `tests/unit/test_auth.py` 时失败。

## 11. 更新镜像

```bash
docker compose down
docker build --no-cache -t anyshare-sync:latest .
docker compose up -d sync-daemon
docker compose logs -f --tail=200 sync-daemon
```

离线交付：

```bash
docker save -o anyshare-sync.tar.gz anyshare-sync:latest
```

更新镜像不会替代持久化目录中的配置、checkpoint 和日志。升级前仍建议备份 `config/`、`data/` 以及达梦同步表。
