"""Create all sync tables in target schema with column comments — Dameng raw SQL."""

import dmPython

# ── Config ──────────────────────────────────────────────────
SCHEMA = 'BISHENG_FOR_AISHU'

DB_USER = 'SYSDBA'
DB_PASS = '6o+%s3z2NK7J'
DB_HOST = '192.168.107.9'
DB_PORT = 5236

# ── Connect ─────────────────────────────────────────────────
conn = dmPython.connect(user=DB_USER, password=DB_PASS,
                        server=DB_HOST, port=DB_PORT)
cur = conn.cursor()
schema_cur = conn.cursor()
schema_cur.execute(f'SET SCHEMA "{SCHEMA}"')
schema_cur.close()

# ── Table definitions: (name, ddl) ──────────────────────────
TABLES = {}

TABLES["ANYSHARE_SYNC_SCOPE_CONFIG"] = (
    '同步范围配置 — 定义哪些 AnyShare 文档库需要同步',
    """(
        ID INT IDENTITY(1,1), TENANT_ID INT DEFAULT 1, SOURCE_TYPE VARCHAR(32),
        SOURCE_ID VARCHAR(1024), SOURCE_NAME VARCHAR(256),
        ENABLED BIT DEFAULT 1, CREATED_AT TIMESTAMP DEFAULT SYSDATE,
        UPDATED_AT TIMESTAMP DEFAULT SYSDATE, PRIMARY KEY (ID))""",
    {
        1: '自增主键',
        2: '租户ID',
        3: '文档库类型 (knowledge_doc_lib | department_doc_lib | user_doc_lib)',
        4: 'AnyShare 文档库 GNS 路径',
        5: '文档库显示名称',
        6: '是否启用同步',
        7: '创建时间',
        8: '更新时间',
    })

TABLES["ANYSHARE_SYNC_SPACE_MAPPING"] = (
    '空间映射 — AnyShare 文档库 GNS 到 BISHENG 空间 ID',
    """(
        ID INT IDENTITY(1,1), TENANT_ID INT DEFAULT 1,
        SOURCE_DOC_LIB_ID VARCHAR(1024), SOURCE_DOC_LIB_NAME VARCHAR(256),
        SOURCE_TYPE VARCHAR(32), SOURCE_OWNER_ID VARCHAR(1024),
        TARGET_SPACE_ID INT, "STATUS" VARCHAR(32),
        AUTH_TYPE VARCHAR(16), CREATED_AT TIMESTAMP DEFAULT SYSDATE,
        UPDATED_AT TIMESTAMP DEFAULT SYSDATE,
        PRIMARY KEY (ID), UNIQUE(SOURCE_DOC_LIB_ID))""",
    {
        1: '自增主键',
        2: '租户ID',
        3: 'AnyShare 文档库 GNS 路径',
        4: '文档库名称',
        5: '文档库类型',
        6: '文档库所有者 ID',
        7: 'BISHENG 空间 ID',
        8: '状态 (pending | created | failed)',
        9: '空间授权类型',
        10: '创建时间',
        11: '更新时间',
    })

TABLES["ANYSHARE_SYNC_SCAN_RUN"] = (
    '扫描批次记录 — 每次同步的扫描元数据',
    """(
        ID INT IDENTITY(1,1), TENANT_ID INT DEFAULT 1, SCAN_TYPE VARCHAR(32),
        SCOPE_CONFIG_ID INT, "STATUS" VARCHAR(32),
        TOTAL_FOLDERS INT DEFAULT 0, TOTAL_FILES INT DEFAULT 0,
        NEW_FILES INT DEFAULT 0, UPDATED_FILES INT DEFAULT 0,
        DELETED_FILES INT DEFAULT 0, STARTED_AT TIMESTAMP DEFAULT SYSDATE,
        COMPLETED_AT TIMESTAMP, ERROR_MESSAGE VARCHAR(4096), PRIMARY KEY (ID))""",
    {
        1: '自增主键',
        2: '租户ID',
        3: '扫描类型 (manual | scheduled | incremental)',
        4: '关联 scope_config 的 ID',
        5: '状态 (running | completed | failed)',
        6: '扫描到的文件夹数',
        7: '扫描到的文件数',
        8: '新增文件数',
        9: '更新文件数',
        10: '删除文件数',
        11: '开始时间',
        12: '完成时间',
        13: '错误信息',
    })

TABLES["ANYSHARE_SYNC_DOCUMENT_MAPPING"] = (
    '文档映射 — 每个文件从 AnyShare 到 BISHENG 的映射（增量核心）',
    """(
        ID INT IDENTITY(1,1), TENANT_ID INT DEFAULT 1, SPACE_MAPPING_ID INT,
        FOLDER_MAPPING_ID INT, SOURCE_DOC_ID VARCHAR(1024),
        SOURCE_REV VARCHAR(256), SOURCE_NAME VARCHAR(512),
        SOURCE_SIZE INT DEFAULT 0, CONTENT_VERSION VARCHAR(256),
        METADATA_HASH VARCHAR(256), POLICY_HASH VARCHAR(256),
        TARGET_FILE_ID INT, TARGET_DOCUMENT_ID INT, TARGET_VERSION_ID INT,
        TARGET_UPLOAD_REF VARCHAR(4096), IDEMPOTENCY_KEY VARCHAR(256),
        CURRENT_ACTION VARCHAR(32), "STATUS" VARCHAR(32),
        NEXT_CHECK_AT TIMESTAMP, LAST_SEEN_SCAN_ID INT,
        MISSING_COUNT INT DEFAULT 0, RETRY_COUNT INT DEFAULT 0,
        CREATED_AT TIMESTAMP DEFAULT SYSDATE,
        UPDATED_AT TIMESTAMP DEFAULT SYSDATE,
        PRIMARY KEY (ID), UNIQUE(SOURCE_DOC_ID))""",
    {
        1: '自增主键',
        2: '租户ID',
        3: '关联 space_mapping 的 ID',
        4: '关联 folder_mapping 的 ID',
        5: 'AnyShare 文档 GNS（唯一）',
        6: 'AnyShare 文档版本号（增量对比关键字段）',
        7: '文档显示名称',
        8: '文件大小（字节）',
        9: '内容版本标识',
        10: '元数据哈希',
        11: '策略/权限哈希',
        12: 'BISHENG 文件 ID',
        13: 'BISHENG 文档 ID',
        14: 'BISHENG 版本 ID',
        15: 'MinIO 上传引用路径',
        16: '幂等键（防重复上传）',
        17: '当前动作',
        18: '状态 (discovered | pending | running | succeeded | failed | deleted)',
        19: '下次检查时间',
        20: '最后被扫描批次 ID',
        21: '连续未发现的次数（用于检测被删除）',
        22: '重试次数',
        23: '创建时间',
        24: '更新时间',
    })

TABLES["ANYSHARE_SYNC_FOLDER_MAPPING"] = (
    '文件夹映射 — AnyShare 文件夹到 BISHENG 文件夹（预留）',
    """(
        ID INT IDENTITY(1,1), TENANT_ID INT DEFAULT 1, SPACE_MAPPING_ID INT,
        SOURCE_FOLDER_ID VARCHAR(1024), SOURCE_PARENT_ID VARCHAR(1024),
        SOURCE_NAME VARCHAR(256), SOURCE_REV VARCHAR(256),
        SOURCE_PATH VARCHAR(4096), TARGET_SPACE_ID INT, TARGET_FOLDER_ID INT,
        TARGET_PARENT_ID INT, METADATA_HASH VARCHAR(256),
        POLICY_HASH VARCHAR(256), "LEVEL" INT DEFAULT 0,
        LAST_SEEN_SCAN_ID INT, MISSING_COUNT INT DEFAULT 0,
        "STATUS" VARCHAR(32), CREATED_AT TIMESTAMP DEFAULT SYSDATE,
        UPDATED_AT TIMESTAMP DEFAULT SYSDATE, PRIMARY KEY (ID))""",
    {
        1: '自增主键',
        2: '租户ID',
        3: '关联 space_mapping 的 ID',
        4: 'AnyShare 文件夹 GNS',
        5: 'AnyShare 父文件夹 GNS',
        6: '文件夹名称',
        7: '版本号',
        8: '文件夹完整路径',
        9: 'BISHENG 空间 ID',
        10: 'BISHENG 文件夹 ID',
        11: 'BISHENG 父文件夹 ID',
        12: '元数据哈希',
        13: '策略/权限哈希',
        14: '层级深度',
        15: '最后被扫描批次 ID',
        16: '连续未发现次数',
        17: '状态 (active | deleted)',
        18: '创建时间',
        19: '更新时间',
    })

TABLES["ANYSHARE_SYNC_PRINCIPAL_MAPPING"] = (
    '身份映射 — AnyShare 用户/部门名到 BISHENG 内部 ID',
    """(
        ID INT IDENTITY(1,1), TENANT_ID INT DEFAULT 1,
        SOURCE_ID VARCHAR(1024), SOURCE_TYPE VARCHAR(32),
        SOURCE_NAME VARCHAR(256), TARGET_ID INT, "STATUS" VARCHAR(32),
        MATCH_METHOD VARCHAR(32), EXTRA VARCHAR(4096),
        CREATED_AT TIMESTAMP DEFAULT SYSDATE,
        UPDATED_AT TIMESTAMP DEFAULT SYSDATE, PRIMARY KEY (ID))""",
    {
        1: '自增主键',
        2: '租户ID',
        3: 'AnyShare 用户或部门 ID（UUID）',
        4: '类型 (user | department | group)',
        5: 'AnyShare 显示名称（accessorname）',
        6: 'BISHENG 内部 user_id / dept_id',
        7: '状态 (mapped | identity_pending | conflict | disabled)',
        8: '匹配方式 (external_id | display_name | api | manual)',
        9: '扩展信息 JSON',
        10: '创建时间',
        11: '更新时间',
    })

TABLES["ANYSHARE_SYNC_PERMISSION_SNAPSHOT"] = (
    '权限快照 — 每次 ACL 翻译到 FGA grants 的审计记录',
    """(
        ID INT IDENTITY(1,1), TENANT_ID INT DEFAULT 1,
        SPACE_MAPPING_ID INT, DOCUMENT_MAPPING_ID INT,
        RESOURCE_TYPE VARCHAR(32), RESOURCE_ID VARCHAR(1024),
        SOURCE_ACL_RAW CLOB, TARGET_FGA_TUPLES CLOB,
        POLICY_HASH VARCHAR(256), IS_BLOCKED BIT DEFAULT 0,
        BLOCK_REASON VARCHAR(1024), CREATED_AT TIMESTAMP DEFAULT SYSDATE,
        PRIMARY KEY (ID))""",
    {
        1: '自增主键',
        2: '租户ID',
        3: '关联 space_mapping',
        4: '关联 document_mapping',
        5: 'BISHENG 资源类型 (knowledge_space | folder | knowledge_file)',
        6: 'BISHENG 资源 ID',
        7: 'AnyShare ACL 原始 JSON',
        8: '翻译后 BISHENG FGA grants JSON',
        9: '策略哈希（用于快速比较变更）',
        10: '是否被权限门禁拦截',
        11: '拦截原因',
        12: '创建时间',
    })

TABLES["ANYSHARE_SYNC_AUDIT_EVENT"] = (
    '审计事件 — 每次同步操作日志',
    """(
        ID INT IDENTITY(1,1), TENANT_ID INT DEFAULT 1, TRACE_ID VARCHAR(128),
        "ACTION" VARCHAR(64), SOURCE_TYPE VARCHAR(32), SOURCE_ID VARCHAR(1024),
        SOURCE_REV VARCHAR(256), TARGET_TYPE VARCHAR(32), TARGET_ID INT,
        OPERATOR VARCHAR(128), "RESULT" VARCHAR(32),
        DETAIL CLOB, POLICY_HASH VARCHAR(256),
        CREATED_AT TIMESTAMP DEFAULT SYSDATE, PRIMARY KEY (ID))""",
    {
        1: '自增主键',
        2: '租户ID',
        3: '追踪 ID（关联同一次同步的所有事件）',
        4: '操作类型 (sync | incremental | log_sync | cleanup)',
        5: '源类型 (knowledge_doc_lib | department_doc_lib | user_doc_lib)',
        6: '源 GNS',
        7: '源版本号',
        8: '目标类型 (knowledge_space)',
        9: 'BISHENG 目标 ID',
        10: '操作人 (system | batch | 用户名)',
        11: '结果 (success | partial | failed)',
        12: '详细信息 JSON',
        13: '策略哈希',
        14: '创建时间',
    })

TABLES["ANYSHARE_SYNC_TASK"] = (
    '任务队列 — 待处理的文件传输任务（预留）',
    """(
        ID INT IDENTITY(1,1), TENANT_ID INT DEFAULT 1, SCAN_RUN_ID INT,
        IDEMPOTENCY_KEY VARCHAR(256), "ACTION" VARCHAR(32),
        SOURCE_DOC_ID VARCHAR(1024), SOURCE_REV VARCHAR(256),
        TARGET_SPACE_ID INT, TARGET_FILE_ID INT, "STATUS" VARCHAR(32),
        RETRY_COUNT INT DEFAULT 0, MAX_RETRIES INT DEFAULT 6,
        NEXT_RETRY_AT TIMESTAMP, LEASE_HOLDER VARCHAR(128),
        LEASE_EXPIRES_AT TIMESTAMP, ERROR_MESSAGE VARCHAR(4096),
        CREATED_AT TIMESTAMP DEFAULT SYSDATE,
        UPDATED_AT TIMESTAMP DEFAULT SYSDATE,
        PRIMARY KEY (ID), UNIQUE(IDEMPOTENCY_KEY))""",
    {
        1: '自增主键',
        2: '租户ID',
        3: '关联 scan_run',
        4: '幂等键（全局唯一）',
        5: '操作类型 (create | update | delete)',
        6: 'AnyShare 文档 GNS',
        7: '文档版本号',
        8: 'BISHENG 目标空间 ID',
        9: 'BISHENG 目标文件 ID',
        10: '状态 (pending | running | completed | failed | dead_letter)',
        11: '重试次数',
        12: '最大重试次数',
        13: '下次重试时间',
        14: '租约持有者（worker ID）',
        15: '租约过期时间',
        16: '错误信息',
        17: '创建时间',
        18: '更新时间',
    })

# ═══════════════════════════════════════════════════════════
# Drop + Create + Comment
# ═══════════════════════════════════════════════════════════

for name, (table_desc, ddl, col_comments) in TABLES.items():
    # Drop
    try:
        cur.execute(f'DROP TABLE "{SCHEMA}"."{name}" CASCADE')
    except Exception:
        pass

    # Create
    sql = f'CREATE TABLE "{SCHEMA}"."{name}" {ddl}'
    try:
        cur.execute(sql)
        print(f'  OK: {name}')
    except Exception as e:
        print(f'  ERR: {name} — {e}')
        continue

    # Table comment
    try:
        cur.execute(f'COMMENT ON TABLE "{SCHEMA}"."{name}" IS \'{table_desc}\'')
    except Exception:
        pass

    # Column comments (by column position, not COLUMN_ID)
    cur2 = conn.cursor()
    try:
        cur2.execute(f"SELECT COLUMN_NAME FROM DBA_TAB_COLUMNS WHERE OWNER='{SCHEMA}' AND TABLE_NAME='{name}' ORDER BY COLUMN_ID")
        cols = [r[0] for r in cur2.fetchall()]
        for col_id, col_desc in col_comments.items():
            if col_id <= len(cols):
                col_name = cols[col_id - 1]
                cur.execute(f'COMMENT ON COLUMN "{SCHEMA}"."{name}"."{col_name}" IS \'{col_desc}\'')
    except Exception:
        pass
    finally:
        cur2.close()

# ── Verify ──────────────────────────────────────────────────
print(f'\n=== Tables in {SCHEMA} ===')
cur.execute(f"SELECT table_name FROM dba_tables WHERE owner='{SCHEMA}' ORDER BY table_name")
tables = [r[0] for r in cur.fetchall()]
for t in tables:
    cur2 = conn.cursor()
    cur2.execute(f"SELECT comments FROM dba_tab_comments WHERE owner='{SCHEMA}' AND table_name='{t}'")
    tdesc = cur2.fetchone()
    cur2.close()
    desc = tdesc[0] if tdesc and tdesc[0] else ''
    print(f'  {t:45s}  {desc}')
    # Show column count
    cur3 = conn.cursor()
    cur3.execute(f"SELECT COUNT(*) FROM dba_tab_columns WHERE owner='{SCHEMA}' AND table_name='{t}'")
    ccnt = cur3.fetchone()[0]
    cur3.close()
    cur4 = conn.cursor()
    cur4.execute(f"SELECT COUNT(*) FROM dba_col_comments WHERE owner='{SCHEMA}' AND table_name='{t}' AND comments IS NOT NULL")
    cc = cur4.fetchone()[0]
    cur4.close()
    print(f'    {" ":45s}  {cc}/{ccnt} columns commented')

conn.close()
print('\n=== Done ===')
