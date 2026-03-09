"""Logging configuration and utilities."""
import logging
import sys
import time
from pathlib import Path
from typing import Optional

from pythonjsonlogger import jsonlogger


class BeijingTimeJsonFormatter(jsonlogger.JsonFormatter):
    """使用北京时间的 JSON 格式化器"""
    
    # 使用 time.localtime 替代默认的 time.gmtime，输出本地时间
    converter = time.localtime


def setup_logger(
    name: str,
    log_level: str = "INFO",
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Set up a logger with JSON formatting.
    
    Args:
        name: Logger name (empty string for root logger)
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional path to log file
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # For root logger, ensure it doesn't propagate
    if name == "":
        logger.propagate = False
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    # JSON formatter with Chinese support and Beijing timezone
    formatter = BeijingTimeJsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        timestamp=True,
        json_ensure_ascii=False  # 支持中文显示
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level.upper()))
    logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        file_handler.setLevel(getattr(logging, log_level.upper()))
        logger.addHandler(file_handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get an existing logger or create a new one.
    
    This will inherit from root logger if it's been set up.
    
    Args:
        name: Logger name
        
    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)
    
    # If root logger has handlers, let this logger propagate to it
    # Don't add duplicate handlers
    if logging.root.handlers:
        # Root logger is configured, use propagation (default behavior)
        # Don't add any handlers to avoid duplicate output
        logger.propagate = True
        return logger
    
    # No root logger, set up a basic handler only if this logger has none
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = BeijingTimeJsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            timestamp=True,
            json_ensure_ascii=False
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # Don't propagate if we have our own handler
    
    return logger

