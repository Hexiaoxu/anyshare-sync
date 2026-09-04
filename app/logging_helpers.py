"""脚本级日志桥接 — 让 print 脚本也能统一落盘、带时间戳和级别。

设计目标：不改动旧脚本里上百处 print，只在脚本顶部调用一次
`init_script_logging()`，即可让所有输出：

  1. 仍保留在 stdout（终端 / docker logs 可见）；
  2. 同时写入 logs/sync.log（带时间戳、trace_id、模块名、行号）；
  3. 按消息内容智能分级：错误/失败 → ERROR，警告/跳过 → WARNING，其余 → INFO。

统一走 app.logger 的 setup_logging()，与 run.py / 增量子模块共用同一套
sync.log + error.log 输出，方便全量 + 增量在同一时间线上调试。
"""

from __future__ import annotations

import builtins
import logging
import re

from .logger import setup_logging, get_logger, set_trace_id

# 消息前缀命中即归类（小写匹配），顺序：先判错误，再判警告
_ERROR_HINTS = (
    "fail", "failed", "failure", "error", "exception", "traceback",
    "失败", "异常", "错误", "❌",
)
_WARN_HINTS = (
    "warn", "skip", "skipped", "跳过", "警告", "⚠",
)

# 零失败/零错误表述（如 "0 failed"、"0 失败"），不应被判为 ERROR。
_ZERO_NEG = re.compile(r'\b0\s+(?:failed|failures?|errors?|异常|错误|失败)\b')


def _classify(msg: str) -> int:
    low = msg.lower()
    # 先剔除“0 failed / 0 失败”这类零失败表述，避免误判为 ERROR
    probe = _ZERO_NEG.sub('', low)
    if any(h in probe for h in _ERROR_HINTS):
        return logging.ERROR
    if any(h in probe for h in _WARN_HINTS):
        return logging.WARNING
    return logging.INFO


def init_script_logging(name: str, level: str = "INFO") -> logging.Logger:
    """初始化日志并安装 tee-print。

    Args:
        name:  本脚本的 logger 名（如 "sync_dept_lib"）。
        level: 控制台日志级别，默认 INFO。

    Returns:
        配置好的 logger，供脚本需要显式记录结构化日志时使用。
    """
    setup_logging(level)
    set_trace_id()
    logger = get_logger(name)

    _orig_print = builtins.print

    def _tee_print(*args, sep=" ", end="\n", file=None, flush=False):
        # 保持原有 stdout 行为
        _orig_print(*args, sep=sep, end=end, file=file, flush=flush)
        # 非 stdout（file 指定）或进度刷新行（\r）不写日志文件，避免刷屏
        if file is not None or "\r" in str(end):
            return
        msg = sep.join(str(a) for a in args).strip()
        if not msg:
            return
        logger.log(_classify(msg), msg)

    builtins.print = _tee_print
    return logger
