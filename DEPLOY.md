# AnyShare → BISHENG 数据迁移与同步部署说明书

## 目录

1. [项目概述](#1-项目概述)
2. [目录结构](#2-目录结构)
3. [前提条件](#3-前提条件)
4. [配置说明](#4-配置说明)
5. [部署步骤](#5-部署步骤)
6. [全量迁移操作](#6-全量迁移操作)
7. [增量守护进程部署](#7-增量守护进程部署)
8. [增量同步说明](#8-增量同步说明)
9. [日常运维](#9-日常运维)
10. [常见问题处理](#10-常见问题处理)
11. [注意事项](#11-注意事项)

---

## 1. 项目概述

本工具集用于将 AnyShare 文档管理系统的数据迁移到 BISHENG 知识管理平台，分为两个阶段：

- **全量迁移**：一次性迁移组织架构（部门+用户）、文档库（个人库/部门库/知识库）、权限配置（ACL）。
- **增量同步**：全量完成后，启动守护进程持续跟踪 AnyShare 操作日志，将新增或变更自动同步到 BISHENG。

---

## 2. 目录结构

```
anyshare-sync/
├── daemon.py                      # 增量同步守护进程入口
├── import_org.py                  # 组织架构全量导入（部门+用户）
├── batch_sync_personal.py         # 个人文档库批量迁移
├── sync_dept_lib.py               # 部门库/知识库迁移
├── sync_one_user.py               # 单个用户库迁移（调试用）
├── run.py                         # 主入口（兼容旧调用方式）
├── requirements.txt               # Python 依赖列表
├── app/
│   ├── connectors/
│   │   ├── anyshare/              # AnyShare API 客户端
│   │   └── bisheng/
│   │       └── token_generator.py # BISHENG JWT Token 自动生成
│   ├── services/
│   │   ├── log_scheduler.py       # 增量日志拉取调度
│   │   └── log_event_handler.py   # 增量事件处理逻辑
│   └── models/                    # 达梦数据库 ORM 模型
│       └── __init__.py            # 数据库连接配置 & init_db()
├── data/
│   └── users.xlsx                 # 用户组织架构 Excel（全量导入用）
└── logs/                          # 运行日志目录（需提前创建）
```

---

## 3. 前提条件

| 条件 | 说明 |
|------|------|
| Python 版本 | 3.10 或以上 |
| 达梦数据库 | 已部署，网络可达，有建表权限 |
| AnyShare 账号 | 管理员账号，需有 Console 管理权限 |
| BISHENG 账号 | 管理员账号，可访问 BISHENG API |
| 服务器 | 推荐部署在 BISHENG 所在服务器或同网段机器 |

---

## 4. 配置说明

全量迁移和增量同步的配置分散在各脚本中，部署前需逐一修改。以下列出所有需要修改的位置。

### 4.1 AnyShare 连接配置

以下文件头部均含 AnyShare 连接参数，按实际环境填写：

| 文件 | 需修改的变量 |
|------|-------------|
| `import_org.py` | `AS_BASE`、`APP_ID`、`SECRET` |
| `batch_sync_personal.py` | `AS_BASE`、`APP_ID`、`SECRET` |
| `sync_dept_lib.py` | `AS_BASE` |
| `daemon.py` | `AS_BASE`、`AS_APP_ID`、`AS_SECRET`、`ADMIN_ACCOUNT` |
| `app/services/log_scheduler.py` | `AS_BASE`、`USER_ID`（Console 管理员用户 ID） |

示例：

```python
AS_BASE = 'https://your-anyshare-domain'   # AnyShare 服务地址（含 https）
APP_ID  = 'your-app-id'                    # 从 AnyShare 管理后台获取
SECRET  = 'your-app-secret'                # 从 AnyShare 管理后台获取
```

**获取 APP_ID 和 SECRET：**
登录 AnyShare 管理后台 → 开放平台 → 应用管理 → 查看对应应用的 App ID 和 App Secret。

**获取 Console 管理员用户 ID（USER_ID）：**
登录 AnyShare 管理后台 → 用户管理 → 找到有 Console 权限的管理员 → 查看用户 ID（通常是形如 `user_xxxxxxxx` 的字符串）。

### 4.2 BISHENG 连接配置

| 文件 | 需修改内容 |
|------|-----------|
| `app/connectors/bisheng/token_generator.py` | BISHENG 服务器地址、JWT Secret |
| 各迁移脚本 | `BS_BASE` 变量 |

示例：

```python
BS_BASE = 'http://your-bisheng-ip:3001'    # BISHENG 服务地址
```

**获取 BISHENG JWT Secret：**

在 BISHENG 服务器上执行以下命令，找到 `jwt` 字段下的 `secret` 值：

```bash
grep -A5 "jwt" /home/hexiaoxu_test/bisheng/docker/bisheng/config.yaml
```

将查到的 secret 填入 `token_generator.py` 对应位置。Token 由脚本在运行时自动生成，有效期 24 小时，每次运行自动刷新，无需手动维护。

### 4.3 达梦数据库配置

编辑 `app/models/__init__.py`，修改数据库连接字符串：

```python
# 示例格式
DATABASE_URL = 'dm+dmPython://用户名:密码@达梦IP:5236/数据库名'
```

按实际的达梦数据库地址、端口、用户名、密码填写。

---

## 5. 部署步骤

### 第一步：传代码到服务器

```bash
scp -r /path/to/anyshare-sync root@<BISHENG服务器IP>:/opt/anyshare-sync
```

### 第二步：安装 Python 依赖

```bash
cd /opt/anyshare-sync
pip3 install -r requirements.txt
```

如果机器无法访问公网，可在有网络的机器上提前打包：

```bash
pip3 download -r requirements.txt -d ./wheels
# 传到目标机器后执行
pip3 install --no-index --find-links=./wheels -r requirements.txt
```

### 第三步：修改配置

按照第 4 节的说明，依次修改以下文件中的连接参数：

- `import_org.py`
- `batch_sync_personal.py`
- `sync_dept_lib.py`
- `daemon.py`
- `app/services/log_scheduler.py`
- `app/connectors/bisheng/token_generator.py`
- `app/models/__init__.py`

### 第四步：初始化达梦数据库

运行以下命令创建所需数据表（仅首次部署时执行）：

```bash
cd /opt/anyshare-sync
python3 -c "from app.models import init_db; init_db(); print('数据库初始化完成')"
```

执行成功后，达梦数据库中会创建迁移状态表（SyncSpaceMapping 等）。

### 第五步：创建日志目录

```bash
mkdir -p /opt/anyshare-sync/logs
```

---

## 6. 全量迁移操作

> **重要：全量迁移期间不得同时运行增量守护进程，否则会导致数据库连接数超限。**

全量迁移按以下顺序依次执行，每一步完成后再执行下一步。

### 6.1 导入组织架构

将 AnyShare 的部门和用户同步到 BISHENG：

```bash
cd /opt/anyshare-sync
python3 import_org.py
```

执行完成后，BISHENG 中会创建对应的部门结构和用户账号。

### 6.2 批量迁移个人文档库

将所有用户的个人文档库迁移到 BISHENG 个人知识空间：

```bash
cd /opt/anyshare-sync
python3 batch_sync_personal.py
```

此步骤耗时较长，可通过 `logs/` 目录下的日志观察进度。

### 6.3 迁移部门库/知识库

每个文档库单独执行，通过环境变量传入文档库名称和 GNS 路径：

```bash
cd /opt/anyshare-sync

# 仅同步目录结构和权限，不迁移文件内容
DEPT_NAME="公司资质" DEPT_GNS="gns://xxxxxxxxxxxxxxxx" python3 sync_dept_lib.py
DEPT_NAME="管理办法" DEPT_GNS="gns://xxxxxxxxxxxxxxxx" python3 sync_dept_lib.py

# 同步目录结构、权限，并下载迁移文件内容（加 SYNC_FILES=1）
DEPT_NAME="技术规范" DEPT_GNS="gns://xxxxxxxxxxxxxxxx" SYNC_FILES=1 python3 sync_dept_lib.py
```

**如何获取文档库 GNS 路径：**
登录 AnyShare 管理后台 → 文档库管理 → 点击目标文档库 → 查看属性，GNS 路径格式为 `gns://xxxxxxxxxxxxxxxx`。

### 6.4 迁移单个用户库（可选，调试用）

如需单独迁移某个用户的文档库：

```bash
cd /opt/anyshare-sync
python3 sync_one_user.py --user <用户账号>
```

---

## 7. 增量守护进程部署

全量迁移完成后，配置 systemd 服务使增量同步在后台持续运行。

### 7.1 创建 systemd 服务文件

```bash
cat > /etc/systemd/system/bisheng-sync.service << 'EOF'
[Unit]
Description=BishengSync Incremental Daemon
After=network.target docker.service

[Service]
Type=simple
WorkingDirectory=/opt/anyshare-sync
ExecStart=/usr/bin/python3 /opt/anyshare-sync/daemon.py
Restart=always
RestartSec=30
StandardOutput=append:/opt/anyshare-sync/logs/daemon.log
StandardError=append:/opt/anyshare-sync/logs/daemon.log

[Install]
WantedBy=multi-user.target
EOF
```

### 7.2 启用并启动服务

```bash
systemctl daemon-reload
systemctl enable bisheng-sync
systemctl start bisheng-sync
```

### 7.3 验证服务状态

```bash
systemctl status bisheng-sync
```

输出中看到 `Active: active (running)` 表示服务正常运行。

---

## 8. 增量同步说明

增量守护进程默认每小时执行一次，从 AnyShare 拉取操作日志并处理以下事件类型：

| 事件类型 | 处理逻辑 |
|---------|---------|
| 文件上传 / 秒传 | 自动下载文件并同步到对应 BISHENG 知识空间 |
| 文件重命名 | 重新同步该文件 |
| 权限变更（ACL） | 将最新权限同步到 BISHENG 对应空间 |
| 新建文件夹 | 在 BISHENG 对应空间创建同名文件夹 |
| 新建用户 | 在 BISHENG 创建对应用户账号 |
| 用户部门变更 | 更新 BISHENG 用户所属部门 |

**增量同步的前提条件：**

- 目标文档库必须已完成全量迁移，即 `SyncSpaceMapping` 表中存在对应的映射记录。
- 尚未全量迁移的文档库中产生的文件操作会被自动忽略，并在日志中记录警告信息，不会报错中断。

---

## 9. 日常运维

### 查看增量同步运行状态

```bash
# 查看服务状态
systemctl status bisheng-sync

# 实时跟踪日志
tail -f /opt/anyshare-sync/logs/daemon.log
```

### 手动触发一次增量同步（不启动守护进程）

```bash
cd /opt/anyshare-sync
python3 daemon.py --once
```

### 重启增量守护进程

```bash
systemctl restart bisheng-sync
```

### 停止增量守护进程（全量迁移前必须执行）

```bash
systemctl stop bisheng-sync
```

### 重跑某个文档库的全量迁移

重跑不会删除 BISHENG 中已有数据，只新增差异部分，可放心执行：

```bash
cd /opt/anyshare-sync
DEPT_NAME="xxx" DEPT_GNS="gns://xxxxxxxxxxxxxxxx" python3 sync_dept_lib.py
```

---

## 10. 常见问题处理

### 问题一：MySQL Too many connections

**现象：** 脚本运行报错 `Too many connections`，通常发生在全量迁移和增量守护进程同时运行时。

**解决：**

1. 确认已停止增量守护进程：`systemctl stop bisheng-sync`
2. 重启 BISHENG 相关容器：

```bash
# 在 BISHENG 服务器上执行
docker compose restart mysql
sleep 15
docker compose restart backend backend_worker
```

3. 等待服务恢复后重新执行迁移脚本。

### 问题二：特殊权限文档库无法访问

**现象：** 某些文档库（如财务部）迁移时报权限不足或无法列举文件。

**解决：** 在 AnyShare 管理后台，将迁移使用的管理员账号手动添加为该文档库的访问成员，授予读取权限后重新执行迁移。

### 问题三：增量同步日志提示"未找到空间映射"

**现象：** 日志中出现类似 `WARNING: no space mapping found for gns://xxx` 的记录。

**原因：** 该文档库尚未完成全量迁移，`SyncSpaceMapping` 表中没有对应记录。

**解决：** 先对该文档库执行全量迁移（参考第 6.3 节），完成后增量同步会自动处理后续事件。

### 问题四：BISHENG Token 失效

**现象：** 脚本报 401 或 Token 相关错误。

**解决：** Token 由 `app/connectors/bisheng/token_generator.py` 自动生成，有效期 24 小时。通常重新运行脚本即可刷新 Token。如持续失败，检查 `token_generator.py` 中的 JWT Secret 是否与服务器 `config.yaml` 一致。

### 问题五：达梦数据库连接失败

**现象：** 启动时报 `Cannot connect to database` 或类似错误。

**排查：**

1. 确认达梦数据库服务正在运行，端口（默认 5236）可达。
2. 检查 `app/models/__init__.py` 中的连接字符串格式和账号密码是否正确。
3. 确认达梦用户有对应 schema 的建表和读写权限。

---

## 11. 注意事项

1. **全量迁移和增量守护进程不能同时运行**，两者并发会导致 BISHENG MySQL 连接数耗尽，引发迁移失败。启动增量守护进程前，务必确认全量迁移已全部完成。

2. **重跑全量迁移是安全的**，脚本会复用 `SyncSpaceMapping` 表中已有的映射记录，不会重复创建空间，也不会删除 BISHENG 中已有数据，只新增差异部分。

3. **需要特殊权限的文档库**（如财务、法务等敏感部门），迁移账号需在 AnyShare 中提前获得授权，否则无法读取文件列表。

4. **AnyShare Console 管理员用户 ID** 配置在 `app/services/log_scheduler.py` 的 `USER_ID` 中，必须使用拥有 Console 管理权限的用户，普通用户无法拉取操作日志，增量同步将无法工作。

5. **BISHENG JWT Secret** 需从 BISHENG 服务器的 `config.yaml` 中读取，不可随意填写，否则生成的 Token 无效。

6. **日志目录** `/opt/anyshare-sync/logs/` 需提前手动创建，守护进程不会自动创建该目录。

7. **数据库初始化**（`init_db()`）仅需在首次部署时执行一次。重复执行不会破坏数据，但若表已存在会跳过建表操作。
