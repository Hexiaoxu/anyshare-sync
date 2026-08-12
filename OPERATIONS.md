# AnyShare → BISHENG 数据迁移工具 操作手册

> 版本：1.0 | 最后更新：2026-07-29
>
> 本手册覆盖从本地打包镜像、服务器部署、全量迁移、增量同步到日常运维的完整操作流程。

---

## 目录

1. [项目概述](#1-项目概述)
2. [前置条件检查](#2-前置条件检查)
3. [配置文件说明](#3-配置文件说明)
4. [本地打包镜像（Windows）](#4-本地打包镜像windows)
5. [服务器部署](#5-服务器部署)
6. [全量迁移（按顺序执行）](#6-全量迁移按顺序执行)
7. [增量同步守护进程](#7-增量同步守护进程)
8. [日常运维操作](#8-日常运维操作)
9. [更新镜像流程](#9-更新镜像流程)
10. [故障排查](#10-故障排查)
11. [附录：增量同步事件类型说明](#11-附录增量同步事件类型说明)

---

## 1. 项目概述

本工具将 **AnyShare 文档管理系统**的数据迁移到 **BISHENG 知识管理平台**，以 Docker 镜像方式部署，支持全量迁移和增量实时同步两种模式。

### 环境信息

| 项目 | 值 |
|------|-----|
| 本地开发环境 | Windows |
| 部署服务器 | Linux，IP: `192.168.106.161` |
| BISHENG 部署目录 | `/home/hexiaoxu_test/bisheng/docker` |
| 工具部署目录 | `/home/hexiaoxu_test/anyshare-sync` |
| Docker 镜像名 | `anyshare-sync:latest` |

### 项目文件结构

```
anyshare-sync/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── config/
│   └── config.yaml          # 唯一需要修改的配置文件
├── data/                    # 运行时数据（挂载到容器）
│   ├── log_sync_checkpoint.json  # 增量同步 checkpoint
│   └── personal_libs.json   # discover.py 生成的个人库列表
├── logs/                    # 运行日志（挂载到容器）
├── drivers/                 # 达梦数据库驱动（已打包进镜像）
├── app/                     # 核心代码
├── discover.py              # 自动发现 AnyShare 所有文档库
├── migrate_all.py           # 全量迁移所有库
├── daemon.py                # 增量同步守护进程
├── import_org.py            # 组织架构导入
├── sync_dept_lib.py         # 单个部门/知识库迁移
├── sync_one_user.py         # 单个用户个人库迁移
└── users_import.json        # 用户数据（组织架构导入用）
```

---

## 2. 前置条件检查

在开始操作之前，确认以下条件均已满足：

### 2.1 本地（Windows）
- [ ] Docker Desktop 已安装并启动
- [ ] 能访问 AnyShare 服务地址
- [ ] 能通过 SSH 连接到服务器 `192.168.106.161`

### 2.2 服务器端
- [ ] Docker 已安装（`docker --version`）
- [ ] Docker Compose 已安装（`docker compose version`）
- [ ] BISHENG 服务已正常运行
- [ ] BISHENG 已配置 Embedding 模型（**必须先配置，否则无法创建知识空间**）
- [ ] 达梦数据库可访问
- [ ] AnyShare Console 管理员账号具有 Console 权限（用于拉取操作日志）

---

## 3. 配置文件说明

配置文件路径：`config/config.yaml`（本工具**唯一需要手动修改**的文件）

```yaml
anyshare:
  base_url: "https://anyshare地址"       # AnyShare 服务地址
  client_id: "应用ID"                    # AnyShare 应用 ID
  client_secret: "应用密钥"             # AnyShare 应用密钥
  admin_account: "管理员账号"            # AnyShare 管理员账号
  console_user_id: "Console管理员用户ID" # 用于拉取操作日志的 Console 管理员用户 ID

bisheng:
  base_url: "http://BISHENG地址:3001"    # BISHENG 服务地址
  jwt_secret: "从BISHENG服务器获取"      # 见下方获取方法

database:
  type: "dameng"
  host: "达梦数据库IP"
  port: 5236
  user: "SYSDBA"
  password: "数据库密码"
  schema: "BISHENG_FOR_AISHU"

sync:
  dept_lib_mode: "single"   # single=整个组织文档库作为一个知识空间
                             # per_dept=每个部门单独一个知识空间
```

### 获取 BISHENG jwt_secret

在服务器上执行：

```bash
grep "secret" /home/hexiaoxu_test/bisheng/docker/bisheng/config.yaml
```

将输出的 `secret` 值填入 `config.yaml` 的 `bisheng.jwt_secret` 字段。

---

## 4. 本地打包镜像（Windows）

### 4.1 配置 Docker 镜像加速（可选，网络较慢时使用）

打开 Docker Desktop → Settings → Docker Engine，添加如下配置：

```json
{
  "registry-mirrors": ["https://docker.m.daocloud.io"]
}
```

点击 "Apply & restart" 重启 Docker。

### 4.2 构建 Docker 镜像

在项目根目录（`D:\aishu\code\anyshare-sync`）打开终端执行：

```bash
# 构建镜像，耗时约 3~10 分钟（取决于网络速度）
docker build -t anyshare-sync:latest .
```

构建完成后验证：

```bash
docker images | grep anyshare-sync
# 预期输出：anyshare-sync   latest   <image_id>   <时间>   <大小>
```

### 4.3 导出镜像为压缩包

```bash
# 导出并压缩，生成 anyshare-sync.tar.gz（约 500MB~1GB）
docker save anyshare-sync:latest | gzip > anyshare-sync.tar.gz
```

### 4.4 传输文件到服务器

```bash
# 传输镜像压缩包
scp anyshare-sync.tar.gz root@192.168.106.161:/home/hexiaoxu_test/

# 传输项目代码（包含配置文件）
scp -r D:\aishu\code\anyshare-sync root@192.168.106.161:/home/hexiaoxu_test/anyshare-sync
```

> **说明：** 如果网络不稳定，可先在本地将项目打包为 zip，再上传：
> ```bash
> # Windows PowerShell
> Compress-Archive -Path D:\aishu\code\anyshare-sync -DestinationPath anyshare-sync-code.zip
> scp anyshare-sync-code.zip root@192.168.106.161:/home/hexiaoxu_test/
> # 服务器上解压
> unzip anyshare-sync-code.zip -d /home/hexiaoxu_test/
> ```

---

## 5. 服务器部署

以下操作均在服务器（`192.168.106.161`）上执行，通过 SSH 登录后操作：

```bash
ssh root@192.168.106.161
```

### 5.1 导入 Docker 镜像

```bash
# 导入镜像（耗时约 1~3 分钟）
docker load < /home/hexiaoxu_test/anyshare-sync.tar.gz
```

### 5.2 验证镜像导入成功

```bash
docker images | grep anyshare-sync
# 预期输出：anyshare-sync   latest   <image_id>   ...
```

### 5.3 修改配置文件

```bash
vi /home/hexiaoxu_test/anyshare-sync/config/config.yaml
```

按照[第 3 节](#3-配置文件说明)的说明，填入所有必要配置项，保存退出（`:wq`）。

### 5.4 验证配置是否正确

```bash
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  anyshare-sync:latest \
  python3 -c "from app.config import cfg; print('AnyShare:', cfg.as_base); print('BISHENG:', cfg.bs_base)"
```

预期输出（显示实际配置的地址）：
```
AnyShare: https://anyshare地址
BISHENG: http://BISHENG地址:3001
```

### 5.5 初始化数据库

```bash
# 创建必要的数据库表结构（首次部署时执行一次即可）
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  anyshare-sync:latest \
  python3 -c "from app.models import init_db; init_db(); print('DB OK')"
```

预期输出：`DB OK`

---

## 6. 全量迁移（按顺序执行）

> **重要：全量迁移期间，请勿同时运行增量同步守护进程，否则会导致数据库连接数耗尽（Too many connections）。**

全量迁移共分 5 个步骤，必须按以下顺序执行：

```
步骤1：导入组织架构
    ↓
步骤2：发现所有文档库
    ↓
步骤3：迁移知识库（文件夹+权限）
    ↓
步骤4：迁移部门文档库
    ↓
步骤5：迁移个人文档库
```

### 步骤 1：导入组织架构

将 AnyShare 的部门和用户信息导入 BISHENG。

```bash
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  -v /home/hexiaoxu_test/anyshare-sync/users_import.json:/app/users_import.json:ro \
  anyshare-sync:latest python3 import_org.py
```

**预期输出：**
```
部门: 新建=xxxx 跳过=0 失败=0 / 用户: 成功=xxxx 失败=0
```

> 如果出现"跳过"说明部门或用户已存在，属于正常情况（重跑时会跳过已有数据）。

### 步骤 2：自动发现所有文档库

扫描 AnyShare 中的所有文档库，生成库列表，供后续迁移步骤使用。

```bash
# 第一步：先用 --dry-run 预览，确认发现结果符合预期
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  anyshare-sync:latest python3 discover.py --dry-run
```

仔细核对输出中的库列表，确认无误后执行正式写入：

```bash
# 第二步：确认无误后，正式写入配置
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  anyshare-sync:latest python3 discover.py
```

> **注意：** `discover.py` 写入时会覆盖 `config.yaml` 中的 `trees` 配置段，但不影响其他配置项。执行后 `data/personal_libs.json` 会被更新。

### 步骤 3：迁移知识库（含文件内容 + 权限）

```bash
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  -v /tmp/anyshare-sync:/tmp/anyshare-sync \
  anyshare-sync:latest python3 migrate_all.py --knowledge --with-files
```

> `/tmp/anyshare-sync` 为文件下载临时目录，迁移完成后可手动清理：`rm -rf /tmp/anyshare-sync`
>
> 如果只需迁移文件夹结构和权限（不含文件内容），去掉 `--with-files` 参数即可。

### 步骤 4：迁移部门文档库（含文件内容 + 权限）

根据 `config.yaml` 中 `sync.dept_lib_mode` 的配置决定迁移模式：

```bash
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  -v /tmp/anyshare-sync:/tmp/anyshare-sync \
  anyshare-sync:latest python3 migrate_all.py --dept --with-files
```

> **`dept_lib_mode` 说明：**
> - `single`（默认）：整个组织文档库作为一个知识空间，所有部门文档在一起
> - `per_dept`：每个部门单独一个知识空间
>
> 修改模式：`vi /home/hexiaoxu_test/anyshare-sync/config/config.yaml`，将 `dept_lib_mode` 改为对应值后重新执行。

### 步骤 5：迁移个人文档库（含文件内容 + 权限）

> **注意：** 个人库数量可能达到数千个，建议放在晚上或业务低峰期执行，耗时较长。

```bash
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  -v /home/hexiaoxu_test/anyshare-sync/users_import.json:/app/users_import.json:ro \
  -v /tmp/anyshare-sync:/tmp/anyshare-sync \
  anyshare-sync:latest python3 migrate_all.py --personal

### 一键迁移所有（谨慎使用）

以下命令会按顺序执行所有迁移步骤，适合对流程已熟悉、环境稳定时使用：

```bash
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  -v /home/hexiaoxu_test/anyshare-sync/users_import.json:/app/users_import.json:ro \
  -v /tmp/anyshare-sync:/tmp/anyshare-sync \
  anyshare-sync:latest python3 migrate_all.py --with-files
```

> **注意：** 重跑全量迁移不会删除已有数据，会复用已有知识空间，因此可以安全地重复执行。

---

## 7. 增量同步守护进程

增量同步守护进程持续监听 AnyShare 的操作日志，将新增/修改/权限变更等事件实时同步到 BISHENG。

> **重要：全量迁移完成后再启动增量守护进程。全量迁移与增量守护进程不能同时运行。**

### 7.1 测试单次运行（推荐首次使用前执行）

```bash
# --once 表示只运行一次后退出，用于验证配置正确性
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  -v /home/hexiaoxu_test/anyshare-sync/logs:/app/logs \
  anyshare-sync:latest python3 daemon.py --once
```

确认输出无报错后，再启动常驻服务。

### 7.2 启动常驻守护进程

使用 docker-compose 管理守护进程（推荐，支持自动重启）：

```bash
cd /home/hexiaoxu_test/anyshare-sync
docker compose up -d sync-daemon
```

### 7.3 查看运行状态

```bash
# 查看容器状态（是否在运行）
docker compose ps

# 实时查看容器日志
docker compose logs -f sync-daemon

# 查看写入到本地的日志文件
tail -f /home/hexiaoxu_test/anyshare-sync/logs/daemon.log
```

### 7.4 停止守护进程

```bash
cd /home/hexiaoxu_test/anyshare-sync
docker compose stop sync-daemon
```

### 7.5 查看增量同步进度

```bash
# 查看当前 checkpoint（记录已处理到的操作日志位置）
cat /home/hexiaoxu_test/anyshare-sync/data/log_sync_checkpoint.json
```

---

## 8. 日常运维操作

### 8.1 重跑单个知识库迁移

当某个知识库迁移失败或需要重新同步时，可单独重跑：

```bash
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  -v /tmp/anyshare-sync:/tmp/anyshare-sync \
  -e DEPT_NAME="公司资质" \
  -e DEPT_GNS="gns://1A71734693F8464A9B8C1980D4AFBB44" \
  anyshare-sync:latest python3 sync_dept_lib.py --with-files
```

> 将 `DEPT_NAME` 和 `DEPT_GNS` 替换为实际的库名称和 GNS 路径。去掉 `--with-files` 则只同步文件夹结构和权限。

### 8.2 重跑单个用户个人库迁移

```bash
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  -e TARGET_USER="username" \
  anyshare-sync:latest python3 sync_one_user.py
```

### 8.3 重置增量同步（从头重新同步）

> **警告：此操作会删除 checkpoint，导致守护进程从操作日志最开始重新处理，可能产生重复数据。仅在确认需要重新同步时执行。**

```bash
# 先停止守护进程
cd /home/hexiaoxu_test/anyshare-sync
docker compose stop sync-daemon

# 删除 checkpoint 文件
rm /home/hexiaoxu_test/anyshare-sync/data/log_sync_checkpoint.json

# 重新启动守护进程
docker compose up -d sync-daemon
```

### 8.4 查看所有日志文件

```bash
ls -la /home/hexiaoxu_test/anyshare-sync/logs/

# 查看最新日志
tail -n 100 /home/hexiaoxu_test/anyshare-sync/logs/daemon.log
```

---

## 9. 更新镜像流程

当代码有更新时，按以下步骤更新部署：

### 9.1 本地操作（Windows）

```bash
# 1. 修改代码后，重新构建镜像
docker build -t anyshare-sync:latest .

# 2. 导出新镜像
docker save anyshare-sync:latest | gzip > anyshare-sync.tar.gz

# 3. 传输到服务器
scp anyshare-sync.tar.gz root@192.168.106.161:/home/hexiaoxu_test/
```

### 9.2 服务器操作

```bash
# 4. 停止正在运行的守护进程
cd /home/hexiaoxu_test/anyshare-sync
docker compose stop sync-daemon

# 5. 删除旧镜像
docker rmi anyshare-sync:latest

# 6. 导入新镜像
docker load < /home/hexiaoxu_test/anyshare-sync.tar.gz

# 7. 验证新镜像
docker images | grep anyshare-sync

# 8. 重启守护进程
docker compose up -d sync-daemon

# 9. 确认守护进程正常运行
docker compose logs -f sync-daemon
```

> **注意：** 更新镜像不需要重新部署配置文件，`config/config.yaml` 保持不变。

---

## 10. 故障排查

### 10.1 数据库连接数耗尽（Too many connections）

**现象：** 日志中出现 `Too many connections` 错误

**原因：** 全量迁移和增量守护进程同时运行，或多个迁移进程并发执行

**解决方法：**

```bash
# 1. 停止所有迁移容器（按 Ctrl+C 或以下命令）
docker compose stop sync-daemon

# 2. 重启 BISHENG MySQL（会中断所有现有连接）
cd /home/hexiaoxu_test/bisheng/docker
docker compose restart mysql

# 3. 等待 MySQL 完全启动（约 15 秒）
sleep 15

# 4. 重启 BISHENG 后端服务
docker compose restart backend backend_worker

# 5. 确认 BISHENG 服务恢复正常后，再重启守护进程
cd /home/hexiaoxu_test/anyshare-sync
docker compose up -d sync-daemon
```

### 10.2 配置验证失败

**现象：** 验证配置命令输出为空或报错

**排查步骤：**

```bash
# 检查配置文件格式（YAML 缩进必须使用空格，不能用 Tab）
cat /home/hexiaoxu_test/anyshare-sync/config/config.yaml

# 进入容器交互式调试
docker run -it --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  anyshare-sync:latest bash

# 在容器内执行
python3 -c "import yaml; yaml.safe_load(open('/app/config/config.yaml'))"
```

### 10.3 无法创建知识空间

**现象：** 迁移过程中报错 `无法创建知识空间` 或 `Embedding model not configured`

**原因：** BISHENG 未配置 Embedding 模型

**解决方法：** 登录 BISHENG 管理后台，在模型管理中配置并启用一个 Embedding 模型，再重新执行迁移命令。

### 10.4 组织架构导入失败

**现象：** `import_org.py` 报错或用户/部门创建失败

**排查步骤：**

```bash
# 检查 users_import.json 格式是否正确
python3 -c "import json; data=json.load(open('/home/hexiaoxu_test/anyshare-sync/users_import.json')); print(f'共 {len(data)} 条记录')"

# 检查 AnyShare 连接
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  anyshare-sync:latest \
  python3 -c "from app.anyshare_client import AnyShareClient; c=AnyShareClient(); print(c.ping())"
```

### 10.5 增量同步不工作

**现象：** AnyShare 有文件变更，但 BISHENG 中没有同步

**排查步骤：**

```bash
# 1. 确认守护进程在运行
docker compose ps

# 2. 查看日志是否有报错
tail -n 200 /home/hexiaoxu_test/anyshare-sync/logs/daemon.log

# 3. 检查 checkpoint 是否正常
cat /home/hexiaoxu_test/anyshare-sync/data/log_sync_checkpoint.json

# 4. 手动触发一次同步测试
docker run --rm --network host \
  -v /home/hexiaoxu_test/anyshare-sync/config:/app/config:ro \
  -v /home/hexiaoxu_test/anyshare-sync/data:/app/data \
  -v /home/hexiaoxu_test/anyshare-sync/logs:/app/logs \
  anyshare-sync:latest python3 daemon.py --once
```

### 10.6 镜像导入失败

**现象：** `docker load` 命令报错

**排查步骤：**

```bash
# 检查文件是否完整（对比本地和服务器上文件大小）
ls -lh /home/hexiaoxu_test/anyshare-sync.tar.gz

# 检查磁盘空间是否充足
df -h /home/hexiaoxu_test/
df -h /var/lib/docker/

# 如果磁盘空间不足，清理旧的 Docker 资源
docker system prune -f
```

### 10.7 discover.py 发现的库不完整

**现象：** `--dry-run` 输出的库列表缺少预期的文档库

**解决方法：**

1. 确认 `config.yaml` 中的 `admin_account` 有权限访问所有文档库
2. 确认 AnyShare 中相关库的权限设置
3. 查看 `discover.py` 的详细日志输出，确认是否有请求报错

---

## 11. 附录：增量同步事件类型说明

守护进程处理来自 AnyShare 操作日志的以下事件类型：

| 事件类型 | logType | opType | 处理方式 |
|----------|---------|--------|----------|
| 文件上传 | 12 | 2 | 下载文件并同步到 BISHENG |
| 秒传/修改 | 12 | 4 | 重新下载并同步文件 |
| 权限变更 | 12 | 11 | 同步 ACL 到 BISHENG |
| 重命名 | 12 | 19 | 重新同步文件 |
| 新建文件夹 | 12 | 22 | 在 BISHENG 创建对应文件夹 |
| 复制文件 | 12 | 24 | 同步副本到 BISHENG |
| 新建用户 | 11 | 1/3/8 | 在 BISHENG 创建用户 |
| 用户换部门 | 11 | 6/7 | 更新 BISHENG 用户所属部门 |
| 下载/预览 | 12 | 3/1/28 | 忽略（不触发同步） |

---

## 重要注意事项汇总

> **操作前必读：**

1. **全量迁移和增量守护进程不能同时运行**，否则会导致 MySQL Too many connections 错误。
2. **BISHENG 必须先配置 Embedding 模型**，否则无法创建知识空间，迁移将失败。
3. **AnyShare Console 管理员账号必须有 Console 权限**，才能拉取操作日志供增量同步使用。
4. **重跑全量迁移是安全的**，不会删除已有数据，会复用已有知识空间。
5. **`discover.py` 会覆盖 `config.yaml` 中的 `trees` 段**，但不影响其他配置项。
6. **个人库迁移依赖 `users_import.json`**，确保该文件与组织架构导入时使用的文件一致。
7. **删除 checkpoint 文件会触发全量重新同步**，谨慎操作。
8. **配置文件中的 YAML 缩进必须使用空格**，不能使用 Tab，否则解析会失败。
