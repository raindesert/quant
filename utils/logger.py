"""工具模块"""
import logging
import sys


def setup_logger(name: str = "quant") -> logging.Logger:
    """设置日志"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        # Windows 终端需要手动指定 UTF-8 编码
        if sys.platform == "win32":
            handler.stream.reconfigure(encoding="utf-8")
        logger.addHandler(handler)

    return logger
