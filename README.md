# AnyShare → BISHENG 文档同步中间件

## 1. 系统概述

将爱数 AnyShare 三种文档库（知识库、部门文档库、个人文档库）单向迁移到 BISHENG 知识空间，**保留完整目录结构和 ACL 权限**。

### 能力矩阵

| | 知识库 | 部门文档库 | 个人文档库 |
|---|---|---|---|
| 递归扫描 | ✅ | ✅ | ✅ |
| 目录创建 | ✅ | ✅ | ✅ |
| 文件下载+上传 | ✅ | ❌ (需管理Token) | ✅ |
| 权限采集+翻译 | ✅ | ✅ | ✅ |
| 逐资源授权 | ✅ | ✅ | ✅ |
| 用户 Owner 授权 | — | — | ✅ |
| 全量同步 | ✅ | ✅ | ✅ |
| 增量同步 (rev对比) | ✅ | ✅ | ✅ |
| 日志驱动增量 | ✅ | ✅ | ✅ |
| 守护进程（自动循环） | ✅ | ✅ | ✅ |
| 批量调度 | ✅ | ✅ | ✅ |
| 一键命令 | ✅ | ✅ | ✅ |

---

## 2. 架构

```
run_sync.py                     ← 统一 CLI 入口
  ├── --user <name>             ← 个人库一键迁移
  ├── --batch                   ← 批量（读 config.yaml）
  ├── --incremental             ← 增量模式
  ├── --sync-logs               ← Console 日志驱动增量
  ├── --daemon <ct>             ← 守护进程（每小时自动）
  └── <gns> <name>              ← 单库全量

app/
├── sync_pipeline.py            ← 核心管道：扫描→建目录→传输→映射→赋权
├── batch_orchestrator.py       ← 批量调度器
├── logger.py                   ← 日志引擎（trace ID + 文件滚动）
├── connectors/
│   ├── anyshare/               ← AnyShare API 封装
│   │   ├── auth.py             ← Token 管理 (client_credentials + user token)
│   │   ├── scanner.py          ← BFS 目录扫描 + 压缩包过滤
│   │   ├── downloader.py       ← 文件下载 (osdownload 协议)
│   │   ├── acl.py              ← ACL 权限采集 (perm2/get)
│   │   └── console.py          ← Console API (部门树、日志)
│   └── bisheng/                ← BISHENG API 封装
│       ├── client.py           ← HTTP 客户端 (Cookie 认证 + 业务错误处理)
│       ├── space.py            ← 空间 CRUD + 旧空间清理
│       ├── folder.py           ← 文件夹创建
│       ├── file_transfer.py    ← 文件上传 (MinIO) + 注册
│       └── permission.py       ← 权限授权 (OpenFGA ReBAC) + 批量+重试
├── services/
│   ├── principal_mapper.py     ← 用户/部门身份映射 (accessorname 解析)
│   ├── permission_translator.py← ACL → FGA 四级角色翻译
│   ├── permission_gate.py      ← 权限门禁检查 (deny/过期/外发)
│   ├── log_event_handler.py    ← Console 日志事件处理器 (8种操作类型)
│   └── log_scheduler.py        ← 日志驱动增量调度器 (定时循环)
└── models/
    └── base.py                 ← 数据库引擎 (SQLite / Dameng 双模式)
```

---

## 3. 权限模型

### AnyShare (操作位模型)

```
每个文件/文件夹有独立 ACL:
  accessortype: user | department
  accessorname: "5jqianw/**eisoo**/钱卫"   ← 用户名/**eisoo**/显示名
  allow: [display, preview, download, create, modify, delete, cache, internal_sharing]
  deny:  []                                  ← 有 deny 则跳过
  endtime: -1                                ← -1=永久, 否则跳过
  inherit: true                              ← 继承上级（BISHENG 无此概念）
```

### BISHENG (四级角色模型)

| 角色 | 权限范围 |
|---|---|
| viewer | 查看、预览、下载 |
| editor | viewer + 修改 + 新建 |
| manager | editor + 删除 + 内部共享 |
| owner | manager + 管理权限（管理员+创建者） |

### 翻译规则

| AnyShare allow 位组合 | → BISHENG |
|---|---|
| 缺 download | ⛔ 跳过 |
| 有 download | viewer |
| + modify + create | editor |
| + delete + internal_sharing | manager |

---

## 4. 快速开始

### 4.1 环境

```bash
pip install httpx sqlmodel pyyaml cryptography pymysql dmPython
```

### 4.2 配置

编辑 `config/config.yaml`：

```yaml
anyshare:
  base_url: "https://5j-zsgl.powerchina.cn"
  client_id: "7b98e7b6-f35e-4613-aeed-5b13112b0ff8"
  client_secret: "Test123."

bisheng:
  base_url: "http://192.168.106.161:3001"
  timeout: 30

database:
  type: "sqlite"            # dev: sqlite | prod: dameng
  sqlite_path: "data/sync_state.db"
  # Dameng 配置 (type: dameng):
  # host: "192.168.107.9"
  # port: 5236
  # user: "SYSDBA"
  # password: "6o+%s3z2NK7J"
  # schema: "BISHENG_FOR_AISHU"

sync:
  max_depth: 20
  max_objects_per_scan: 500000
  scopes:
    - source_gns: "gns://..."
      space_name: "公司资质"
      source_type: "knowledge_doc_lib"
      enabled: true
```

### 4.3 Dameng 初始化（仅生产环境）

```bash
# 首次部署时建表
python init_dameng_sql.py
```

然后在 `config.yaml` 中设 `database.type: dameng`。

### 4.4 获取 Token

**AnyShare Browser Token**（知识库/部门库用）：
```javascript
// F12 Console → AnyShare 页面
document.cookie.match(/client\.oauth2_token=([^;]+)/)[1]
```

**AnyShare Console Token**（日志驱动增量 + 守护进程用）：
```javascript
// F12 Console → AnyShare 后台管理 /console/
document.cookie.match(/console\.oauth2_token=([^;]+)/)[1]
```

**BISHENG Cookie**：
```javascript
// F12 Console → BISHENG 页面
document.cookie.match(/access_token_cookie=([^;]+)/)[1]
```

---

## 5. 命令参考

### 5.1 知识库全量迁移

```bash
python run_sync.py "<as_token>" "<bs_cookie>" "<lib_gns>" "<space_name>"
```

### 5.2 部门文档库（只建目录+赋权）

```bash
python run_sync.py "<as_token>" "<bs_cookie>" "<lib_gns>" "<space_name>" \
    --type department_doc_lib \
    --ancestors "父目录1,父目录2" \
    --skip-download
```

### 5.3 个人库一键迁移 ⭐

```bash
# 自动获取 Token + 查找 GNS + 迁移 + grant owner
python run_sync.py "<bs_cookie>" --user 5jzhoujiajun

# 手动提供 Token（用户名匹配不上时）
python run_sync.py "<bs_cookie>" --user 程博 --token "ory_at_..."

# 手动指定 owner
python run_sync.py "<as_token>" "<bs_cookie>" "<gns>" "<name>" \
    --type user_doc_lib --grant-owner "程博"
```

### 5.4 增量同步（rev 对比）

```bash
python run_sync.py "<as_token>" "<bs_cookie>" "<lib_gns>" "<space_name>" --incremental
```

### 5.5 日志驱动增量

```bash
python run_sync.py "<as_token>" "<bs_cookie>" "<lib_gns>" "<space_name>" \
    --sync-logs "<console_token>" 1784476800000000 1784591999999000
```

### 5.6 守护进程（每小时自动增量）⭐

```bash
python run_sync.py "<as_token>" "<bs_cookie>" "<lib_gns>" "<space_name>" \
    --daemon "<console_token>" --interval 3600
```

首次运行先做一次全量同步建立 UUID→GNS 映射，之后每小时拉 Console 日志增量同步。Ctrl+C 退出。

### 5.7 批量调度

```bash
python run_sync.py "<as_token>" "<bs_cookie>" --batch
python run_sync.py "<as_token>" "<bs_cookie>" --batch --incremental
```

### 5.8 查看有哪些库

```bash
python run_sync.py "<as_token>" "<bs_cookie>" --list knowledge
python run_sync.py "<as_token>" "<bs_cookie>" --list department
python run_sync.py "<as_token>" "<bs_cookie>" --list personal
```

---

## 6. 工具脚本

| 脚本 | 用途 |
|---|---|
| `run_sync.py` | **主入口** — 三库统一命令（8 种模式） |
| `init_dameng_sql.py` | **Dameng 建表**（生产 deployment） |
| `scan_tree.py` | 扫描文档库树，打印结构 + ACL 统计 |
| `list_personal_libs.py` | 列出所有个人文档库及用户名 |
| `check_personal_libs.py` | 扫描所有个人库，找有内容的 |
| `search_user_lib.py` | 按用户名/显示名搜索个人库 GNS |
| `find_my_lib.py` | 获取用户Token + 查找其个人库内容 |
| `migrate_users.py` | 批量生成用户迁移命令 |
| `pull_logs.py` | 拉取 Console EACPLog 操作日志 |
| `test_app_token.py` | 测试 client_credentials Token 权限范围 |
| `test_personal_token.py` | 测试个人库 Token 获取和访问 |
| `test_dameng_crud.py` | Dameng CRUD 验证测试 |
| `test_untested.py` | 增量/批量/日志驱动集成测试 |
| `dm_schema.py` | 查看 Dameng 数据库结构 |
| `show_dameng.py` | 查看 Dameng 表和行数 |

---

## 7. 数据库

### 7.1 双模式

| 模式 | 引擎 | 用途 |
|---|---|---|
| `sqlite` | SQLModel ORM | 本地开发 |
| `dameng` | dmPython 直连 | 生产环境 |

切换方式：修改 `config.yaml` 中 `database.type`。

### 7.2 映射表

| 表 | 内容 | 增量依赖 |
|---|---|---|
| `anyshare_sync_scope_config` | 同步范围配置 | — |
| `anyshare_sync_scan_run` | 每次扫描批次 | — |
| `anyshare_sync_space_mapping` | 文档库 GNS → BISHENG 空间 ID | — |
| `anyshare_sync_document_mapping` | 文件 GNS/rev/BISHENG ID/状态 | ✅ rev 对比 |
| `anyshare_sync_permission_snapshot` | ACL → FGA grants 快照 | ✅ 审计追溯 |
| `anyshare_sync_audit_event` | 操作审计日志 | — |
| `anyshare_sync_principal_mapping` | 用户/部门身份映射 | — |
| `anyshare_sync_folder_mapping` | 文件夹映射（预留） | — |
| `anyshare_sync_task` | 任务队列（预留） | — |

### 7.3 Dameng 建表

```bash
python init_dameng_sql.py
```

---

## 8. 日志驱动增量

### 8.1 事件类型

Console API `EACPLog/GetPageLog` 返回的操作事件：

| logType | opType | 含义 | BISHENG 动作 |
|---------|--------|------|-------------|
| 12 | 2 | 上传文件 | 下载→上传→注册→赋权 |
| 12 | 11 | 权限变更 | perm2/get → authorize |
| 12 | 19 | 文件修改 | 重新下载 |
| 12 | 22 | 新建文件夹 | 创建目录+赋权 |
| 12 | 3 | 删除文件 | 标记 status=deleted |
| 12 | 24 | 自动删除 | 标记 status=deleted |
| 11 | 1 | 新建部门 | 创建部门 |
| 11 | 3 | 新建/修改用户 | 同步用户 |
| 11 | 6 | 移动用户 | 更新用户-部门关系 |
| 11 | 7 | 移除用户 | 更新用户-部门关系 |

### 8.2 守护进程

```bash
python run_sync.py "AS_Token" "BS_Cookie" "gns://..." "公司资质" \
    --daemon "Console_Token" --interval 3600
```

流程：首次全量同步建 UUID→GNS 映射 → 每小时拉日志 → `LogEventHandler` 分发 8 种 handler → 增量同步 → 更新 checkpoint。

Checkpoint 文件：`data/log_sync_checkpoint.json`

---

## 9. 日志系统

### 9.1 输出

| 目标 | 级别 | 路径 |
|---|---|---|
| 控制台 | INFO | stdout |
| 全量日志 | DEBUG | `logs/sync.log` (10MB×5) |
| 错误日志 | ERROR | `logs/error.log` (5MB×3) |

### 9.2 格式

```
# 控制台
[2026-07-21 10:30:15] [INFO   ] Sync start: 公司资质 full

# 文件（含 trace ID）
[2026-07-21 10:30:15] [DEBUG  ] [a1b2c3d4e5f6] sync_pipeline:89 - Scanned 5 dirs, 9 files
```

每次同步自动生成 12 位 trace ID，贯穿所有日志，方便排查。

---

## 10. 已知限制

| 限制 | 说明 |
|---|---|
| 部门库文件下载 | Browser Token 返回 404/403，需要管理Token |
| 个人库跨用户批量 | 需每个用户设置密码 + `authentication/v1/access_token` |
| Token 有效期 | AnyShare ~1h，BISHENG ~24h，需手动刷新（守护进程需定期更新） |
| 部门 include_children | MySQL "Too many connections"，已默认关闭 |
| 个人库空库率 | 8902 用户中绝大部分为空 |
| 扫描深度限制 | 默认 6 层、500 目录、2000 文件 |

---

## 11. 部署

```bash
# 1. 安装依赖
pip install httpx sqlmodel pyyaml cryptography pymysql dmPython

# 2. 修改配置
vim config/config.yaml  # 填入 client_id/secret, BISHENG 地址, 数据库

# 3. 初始化数据库（仅 Dameng 生产）
python init_dameng_sql.py

# 4. 首次批量全量同步
python run_sync.py "<as_token>" "<bs_cookie>" --batch

# 5. 启动守护进程（可选）
python run_sync.py "<as_token>" "<bs_cookie>" "<gns>" "<space>" \
    --daemon "<console_token>" --interval 3600
```
