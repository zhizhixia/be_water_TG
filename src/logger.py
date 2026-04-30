import logging
import sys


def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """创建并配置一个日志记录器。

    Args:
        name: 日志记录器名称。
        level: 日志级别，默认为 INFO。

    Returns:
        配置好的 Logger 实例。
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger
