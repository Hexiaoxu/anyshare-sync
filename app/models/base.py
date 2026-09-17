"""Database engine — SQLite (dev) or Dameng (prod), configured via config.yaml."""

import logging
import os
import time
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.yaml"

def _load_db_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("database", {})

_db = _load_db_config()
_db_type = os.environ.get("SYNC_DB_TYPE", _db.get("type", "sqlite")).lower()

# ── Build engine ─────────────────────────────────────────────

if _db_type == "dameng":
    import urllib.parse, dmPython
    engine = None  # not used — we use raw dmPython

    def _get_dm_conn():
        """Connect to Dameng.

        dmPython's own login_timeout/connection_timeout have been observed to
        NOT actually bound how long connect() can block — under packet loss
        it hangs forever with no exception and no log line, which looks like
        the whole process silently died. We can't fix the driver, but we can
        force our own hard ceiling around the call: on Linux (the only
        target here — this only runs inside the Docker image), SIGALRM
        interrupts connect() even if the driver itself never times out, so a
        stuck connection becomes a loud, logged TimeoutError instead of an
        indistinguishable hang.

        Default floor is 30s, not the old 10s: BISHENG's own Dameng connection
        layer (core/database/connection.py) hit the same class of failure —
        their dmAsync default (login_timeout=5s) occasionally wasn't enough
        for the DM login handshake under load, aborting with
        "[CODE:-70028]Create SOCKET connection failure" on ~4% of connects.
        Raising both timeouts to 30s eliminated it in their production. This
        is config.yaml's database.connect_timeout, so still overridable.
        """
        timeout_s = int(_db.get("connect_timeout", 30))
        host, port = _db.get("host", "127.0.0.1"), _db.get("port", 5236)
        logger.info(f"[dameng] connecting to {host}:{port} (timeout={timeout_s}s) ...")
        t0 = time.monotonic()

        import signal
        has_alarm = hasattr(signal, "SIGALRM")
        if has_alarm:
            def _on_alarm(signum, frame):
                raise TimeoutError(
                    f"dmPython.connect() to {host}:{port} did not return within "
                    f"{timeout_s + 5}s (driver's own login_timeout/"
                    f"connection_timeout did not bound it) — check network "
                    f"reachability / firewall to the Dameng host, not just "
                    f"that the port answers a plain TCP connect.")
            old_handler = signal.signal(signal.SIGALRM, _on_alarm)
            signal.alarm(timeout_s + 5)  # small margin over the driver's own timeout

        try:
            conn = dmPython.connect(
                user=_db.get("user", "SYSDBA"),
                password=_db.get("password", "SYSDBA"),
                # host=, not server= — BISHENG's own (working) dmSQLAlchemy
                # connection layer uses host=/port=/dsn=, never server=. The
                # driver's compiled string table shows host and server are
                # two distinct, mutually-exclusive code paths internally
                # ("host or server can only set one" / "invalid server") —
                # server= is the less-exercised one and the prime suspect
                # for the SIGFPE crash we hit that BISHENG never sees.
                host=host,
                port=port,
                local_code=1,      # UTF-8 (避免 GBK 编码错误)
                # 无超时时网络丢包会导致连接无限期挂起（无异常无日志）；
                # 显式设超时，连不上时快速失败而不是静默卡死。
                login_timeout=timeout_s,
                connection_timeout=timeout_s,
            )
            logger.info(f"[dameng] connected in {time.monotonic() - t0:.1f}s")
            return conn
        finally:
            if has_alarm:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

    def _full_table(table_name: str) -> str:
        s = _db.get("schema", "")
        # Dameng is case-sensitive for quoted identifiers
        name = table_name.upper()
        if s:
            return f'"{s}"."{name}"'
        return f'"{name}"'

else:
    from sqlmodel import create_engine
    sqlite_path = os.environ.get(
        "SYNC_SQLITE_PATH", _db.get("sqlite_path", "data/sync_state.db"))
    db_path = Path(__file__).parent.parent.parent / sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)


# ── Public API ───────────────────────────────────────────────

def init_db():
    """Create tables. Dameng: use init_dameng_sql.py. SQLite: auto-create."""
    if _db_type != "dameng":
        from sqlmodel import SQLModel
        SQLModel.metadata.create_all(engine)


def get_session():
    """Return session. Dameng: DmSession. SQLite: SQLModel Session."""
    if _db_type == "dameng":
        return DmSession()
    else:
        from sqlmodel import Session
        return Session(engine)


class Base:
    """Stub base class."""
    pass


# ── Dameng helpers ────────────────────────────────────────────

class DmRecord(dict):
    """Dict+object hybrid that tracks changes for UPDATE."""
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self._table = None
        self._dirty = set()

    def __getattr__(self, name):
        if name.startswith('_'):
            return super().__getattribute__(name)
        if name == '__tablename__' and '_table' in self.__dict__:
            return self.__dict__['_table']
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith('_') or name in ('_table', '_dirty', '__tablename__'):
            super().__setattr__(name, value)
        else:
            self[name] = value
            if '_dirty' in self.__dict__:
                self.__dict__['_dirty'].add(name)


class DmResult:
    def __init__(self, rows, cursor=None):
        self._rows = rows
        if cursor and rows:
            cols = [d[0].lower() for d in cursor.description]
            self._rows = [DmRecord(zip(cols, row)) for row in rows]

    def first(self):
        return self._rows[0] if self._rows else None

    def one(self):
        if len(self._rows) == 1 and isinstance(self._rows[0], DmRecord):
            return list(self._rows[0].values())[0]
        if len(self._rows) != 1:
            raise ValueError(f"Expected 1 row, got {len(self._rows)}")
        return self._rows[0]

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


# ── Dameng Session (raw dmPython) ────────────────────────────

class DmSession:
    """SQLModel-compatible session backed by raw dmPython for Dameng."""

    def __init__(self):
        self.conn = _get_dm_conn()
        self._cur = None
        self._objects = []  # INSERT/DELETE pending
        self._loaded = []   # DmRecord objects from exec() for UPDATE tracking
        # Set schema on this connection
        schema_name = _db.get("schema", "")
        if schema_name:
            c = self.conn.cursor()
            c.execute(f'SET SCHEMA "{schema_name}"')
            c.close()

    @property
    def cur(self):
        if self._cur is None:
            self._cur = self.conn.cursor()
        return self._cur

    def exec(self, statement):
        """Execute a SQLModel SELECT statement. Returns DmResult."""
        # Compile the statement to get WHERE clause
        from sqlalchemy import sql as sa
        compiled = statement.compile(compile_kwargs={"literal_binds": True})
        sql_str = str(compiled)

        # Parse SQLModel SELECT → Dameng SQL
        import re as _re
        sql_flat = _re.sub(r'\s+', ' ', sql_str)

        # Handle COUNT(*) queries
        if 'count(' in sql_flat.lower():
            m = _re.search(r'(?i)FROM\s+(\w+)', sql_flat)
            if not m:
                raise ValueError("Cannot parse COUNT: " + sql_flat[:100])
            full_table = _full_table(m.group(1))
            self.cur.execute(f"SELECT COUNT(*) AS cnt FROM {full_table}")
            return DmResult(self.cur.fetchall(), self.cur)

        # Regular SELECT: FROM table_name [alias] WHERE ...
        m = _re.search(r'(?i)FROM\s+(\w+)(?:\s+\w+)?\s*(WHERE\s+.+)?$', sql_flat)
        if not m:
            raise ValueError("Cannot parse SELECT: " + sql_flat[:100])
        table_name = m.group(1)
        where_clause = m.group(2) or ""
        full_table = _full_table(table_name)

        sql = f"SELECT * FROM {full_table} {where_clause}"
        self.cur.execute(sql)
        result = DmResult(self.cur.fetchall(), self.cur)
        # Track loaded records for UPDATE detection
        for row in result:
            if isinstance(row, DmRecord):
                row._table = table_name
                self._loaded.append(row)
        return result

    def add(self, obj):
        """Track object for INSERT on commit. Updates if object already has an ID."""
        oid = getattr(obj, 'id', None) or (obj['id'] if isinstance(obj, dict) and 'id' in obj else None)
        if oid:
            # Existing record — track for update (via DmRecord._dirty)
            if isinstance(obj, DmRecord) and obj._dirty:
                self._loaded.append(obj)
            # Also handle SQLModel objects with existing ID
            elif hasattr(obj, '__tablename__'):
                self._loaded.append(obj)
        else:
            self._objects.append(('insert', obj))

    def delete(self, obj):
        """Track object for DELETE on commit. Works with SQLModel and DmRecord."""
        self._objects.append(('delete', obj))

    def commit(self):
        """Flush all pending inserts/updates/deletes."""
        for action, obj in self._objects:
            if action == 'insert':
                self._do_insert(obj)
            elif action == 'delete':
                self._do_delete(obj)
        self._objects.clear()
        # UPDATE dirty DmRecord objects
        for rec in self._loaded:
            if rec._dirty and 'id' in rec:
                self._do_update(rec)
                rec._dirty.clear()
        self._loaded.clear()
        self.conn.commit()

    def _do_update(self, rec: DmRecord):
        """Generate UPDATE SQL from dirty attributes."""
        table_name = rec._table or rec.get('__tablename__', 'unknown')
        full_table = _full_table(table_name)
        sets = []
        vals = {}
        i = 1
        for col in rec._dirty:
            if col == 'id':
                continue
            sets.append(f'"{col.upper()}" = :{i}')
            vals[str(i)] = rec[col]
            i += 1
        if not sets:
            return
        vals[str(i)] = rec['id']
        sql = f'UPDATE {full_table} SET {", ".join(sets)} WHERE "ID" = :{i}'
        cur = self.conn.cursor()
        try:
            cur.execute(sql, vals)
        finally:
            cur.close()

    def _do_insert(self, obj):
        # Support both SQLModel objects and DmRecord/dicts
        table_name = getattr(obj, '__tablename__', None)
        if table_name is None and isinstance(obj, dict):
            table_name = obj.get('_table') or obj.get('__tablename__', 'unknown')
        if table_name is None:
            table_name = 'unknown'
        full_table = _full_table(table_name)

        cols = []
        vals = []
        # Works for both SQLModel objects (__dict__) and DmRecord (dict items)
        items = obj.__dict__.items() if hasattr(obj, '__dict__') else (obj.items() if isinstance(obj, dict) else [])
        for col_name, col_val in items:
            if col_name.startswith('_') or col_name == 'id':
                continue
            if col_val is not None:
                cols.append(f'"{col_name.upper()}"')
                vals.append(col_val)

        if not cols:
            return

        placeholders = ','.join([f':{i+1}' for i in range(len(vals))])
        col_str = ','.join(cols)
        sql = f'INSERT INTO {full_table} ({col_str}) VALUES ({placeholders})'

        cur = self.conn.cursor()
        try:
            cur.execute(sql, dict(zip([str(i+1) for i in range(len(vals))], vals)))
            cur.execute('SELECT @@IDENTITY')
            row = cur.fetchone()
            if row and row[0] is not None:
                obj.id = int(row[0])
        finally:
            cur.close()

    def _do_delete(self, obj):
        if not obj.id and not (isinstance(obj, dict) and obj.get('id')):
            return
        # Support both SQLModel objects and DmRecord dicts
        oid = obj.id if hasattr(obj, 'id') else obj.get('id', 0)
        table_name = getattr(obj, '__tablename__', None) or getattr(obj, '_table', None) or 'unknown'
        full_table = _full_table(table_name)
        cur = self.conn.cursor()
        try:
            cur.execute(f'DELETE FROM {full_table} WHERE "ID" = :1', {'1': oid})
        finally:
            cur.close()

    def rollback(self):
        self._objects.clear()
        self.conn.rollback()

    def close(self):
        if self._cur:
            self._cur.close()
            self._cur = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
