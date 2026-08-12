"""
增量同步守护进程 — 每小时拉取 AnyShare 操作日志，同步到 BISHENG

用法:
    python daemon.py              # 每小时执行一次
    python daemon.py --interval 1800  # 每30分钟执行一次
    python daemon.py --once       # 只跑一次（测试用）

部署为系统服务（Linux）:
    nohup python daemon.py > logs/daemon.log 2>&1 &
"""
import sys, os, logging
from pathlib import Path

# 确保工作目录正确
os.chdir(Path(__file__).parent)
sys.path.insert(0, '.')

from app.config import cfg

# 日志配置
log_dir = Path('logs')
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=getattr(logging, cfg.log_level, logging.INFO),
    format='%(asctime)s %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('logs/daemon.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger('daemon')

# 从配置文件读取
AS_BASE       = cfg.as_base
BS_BASE       = cfg.bs_base
AS_APP_ID     = cfg.as_client_id
AS_SECRET     = cfg.as_client_secret
ADMIN_ACCOUNT = cfg.as_admin_account

args = sys.argv[1:]
once_mode = '--once' in args
interval = cfg.scheduler_interval
for i, a in enumerate(args):
    if a == '--interval' and i+1 < len(args):
        try: interval = int(args[i+1])
        except: pass

logger.info(f"增量同步守护进程启动 ({'单次模式' if once_mode else f'每{interval}秒'})")

from app.connectors.anyshare.auth import AnyShareAuth
from app.connectors.bisheng.token_generator import generate_bs_token
from app.sync_pipeline import SyncPipeline
from app.services.log_scheduler import LogSyncScheduler

auth = AnyShareAuth(AS_BASE, AS_APP_ID, AS_SECRET)
console_token = auth.get_user_token(ADMIN_ACCOUNT)
bs_cookie = generate_bs_token()

pipeline = SyncPipeline(BS_BASE, bs_cookie, AS_BASE, console_token,
                        as_auth=auth, as_account=ADMIN_ACCOUNT)
scheduler = LogSyncScheduler(pipeline, console_token, bs_cookie, interval=interval)

if once_mode:
    result = scheduler.run_once()
    logger.info(f"单次运行完成: {result}")
else:
    try:
        scheduler.run_forever()
    except KeyboardInterrupt:
        scheduler.stop()
        logger.info("守护进程已停止")
