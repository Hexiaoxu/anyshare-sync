FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装达梦数据库驱动
COPY drivers/dmPython.cpython-311-x86_64-linux-gnu.so /usr/local/lib/python3.11/site-packages/
COPY drivers/dmpython.libs /usr/local/lib/python3.11/site-packages/dmpython.libs/
COPY drivers/dmSQLAlchemy /usr/local/lib/python3.11/site-packages/dmSQLAlchemy/
COPY drivers/dmssl /usr/local/lib/python3.11/site-packages/dmssl/
COPY drivers/dmAsync /usr/local/lib/python3.11/site-packages/dmAsync/

# 达梦 SSL 库路径
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/dmpython.libs:/usr/local/lib/python3.11/site-packages/dmssl

# 复制代码
COPY app/          ./app/
COPY daemon.py     ./
COPY import_org.py ./
COPY batch_sync_personal.py ./
COPY sync_dept_lib.py ./
COPY sync_one_user.py ./
COPY run.py        ./
COPY discover.py   ./
COPY migrate_all.py ./

# 创建必要目录（config 和 data 由外部挂载）
RUN mkdir -p /app/logs /app/data /tmp/anyshare-sync

# 默认启动增量守护进程
CMD ["python3", "daemon.py"]
