"""清空达梦数据库中的同步状态表（全新 BISHENG 环境部署前使用）。

用法:
    python3 reset_dameng.py          # 清空全部 9 张状态表
    python3 reset_dameng.py --dry-run  # 只统计每张表行数，不删除

注意:
    - 会清空全部映射表（空间/文件夹/文件/用户映射、权限快照、扫描/任务/审计记录）。
    - 表结构保留，仅清空数据。全新 BISHENG 环境需要干净的状态表，否则会复用
      指向旧空间 ID 的映射，导致空间复用和增量同步错乱。
    - 仅需在全新 BISHENG 环境首次部署前执行一次。
"""
import sys
from app.models.base import _get_dm_conn, _full_table

# 与 app/models/__init__.py 中的 9 张表保持一致
TABLES = [
    "anyshare_sync_audit_event",
    "anyshare_sync_space_mapping",
    "anyshare_sync_principal_mapping",
    "anyshare_sync_document_mapping",
    "anyshare_sync_folder_mapping",
    "anyshare_sync_task",
    "anyshare_sync_scope_config",
    "anyshare_sync_permission_snapshot",
    "anyshare_sync_scan_run",
]


def main():
    dry_run = "--dry-run" in sys.argv
    conn = _get_dm_conn()
    cur = conn.cursor()

    total = 0
    for t in TABLES:
        full = _full_table(t)
        try:
            cur.execute(f"SELECT COUNT(*) FROM {full}")
            before = cur.fetchone()[0]
        except Exception as e:
            print(f"[SKIP] {t}: 表不存在或不可读 ({e})")
            continue

        if dry_run:
            print(f"[INFO] {t}: {before} 行")
            total += before
            continue

        try:
            cur.execute(f"DELETE FROM {full}")
            conn.commit()
            print(f"[OK] {t}: 清空 {before} 行")
            total += before
        except Exception as e:
            conn.rollback()
            print(f"[FAIL] {t}: {e}")

    cur.close()
    conn.close()

    if dry_run:
        print(f"\n共 {total} 行（未删除，dry-run 模式）。")
    else:
        print(f"\n共清空 {total} 行。")


if __name__ == "__main__":
    main()
