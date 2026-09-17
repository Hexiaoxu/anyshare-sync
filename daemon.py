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
from app.logger import setup_logging, get_logger, set_trace_id

# 统一日志体系（console + sync.log + error.log，带 trace_id），
# 与 run.py / 增量子模块共用同一套，便于全量+增量在同一时间线调试。
setup_logging(cfg.log_level)
set_trace_id()

# 额外保留 daemon.log（运维习惯：tail -f logs/daemon.log），追加到 root。
log_dir = Path('logs')
log_dir.mkdir(exist_ok=True)
_dh = logging.FileHandler('logs/daemon.log', encoding='utf-8')
_dh.setFormatter(logging.Formatter('%(asctime)s %(name)s: %(message)s'))
logging.getLogger().addHandler(_dh)

logger = get_logger('daemon')

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

try:
    logger.info("[启动] 导入依赖模块...")
    from app.connectors.anyshare.auth import AnyShareAuth
    from app.connectors.bisheng.token_generator import generate_bs_token
    from app.sync_pipeline import SyncPipeline
    from app.services.log_scheduler import LogSyncScheduler
    logger.info("[启动] 模块导入完成")

    logger.info(f"[启动] 正在获取 AnyShare 用户 token (account={ADMIN_ACCOUNT}, base={AS_BASE})...")
    auth = AnyShareAuth(AS_BASE, AS_APP_ID, AS_SECRET)
    console_token = auth.get_user_token(ADMIN_ACCOUNT)
    logger.info("[启动] AnyShare 用户 token 获取成功")

    logger.info("[启动] 正在生成 BISHENG cookie...")
    bs_cookie = generate_bs_token()
    logger.info("[启动] BISHENG cookie 生成成功")

    logger.info(f"[启动] 正在初始化同步 pipeline (bs_base={BS_BASE})...")
    pipeline = SyncPipeline(BS_BASE, bs_cookie, AS_BASE, console_token,
                            as_auth=auth, as_account=ADMIN_ACCOUNT)
    logger.info("[启动] pipeline 初始化完成，正在恢复状态（连接数据库）...")
    pipeline.restore_state()
    logger.info("[启动] 状态恢复完成")

    scheduler = LogSyncScheduler(pipeline, console_token, bs_cookie, interval=interval)
    logger.info("[启动] 调度器已创建，准备进入主循环")
except Exception:
    logger.exception("[启动] 守护进程初始化失败")
    raise

if once_mode:
    result = scheduler.run_once()
    logger.info(f"单次运行完成: {result}")
else:
    try:
        scheduler.run_forever()
    except KeyboardInterrupt:
        scheduler.stop()
        logger.info("守护进程已停止")
