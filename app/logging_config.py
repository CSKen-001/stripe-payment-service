from __future__ import annotations
import logging
import json
import sys
from typing import Dict, Any
from datetime import datetime

class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname", 
                "filename", "module", "lineno", "funcName", "created", 
                "msecs", "relativeCreated", "thread", "threadName", 
                "processName", "process", "getMessage", "exc_info", "exc_text", 
                "stack_info", "message"
            }:
                log_entry[key] = value
        
        return json.dumps(log_entry, default=str)

def setup_logging(log_level: str = "INFO", log_format: str = "json", log_file: str = "webhook.log") -> None:
    """Setup application logging configuration"""
    
    # Clear existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    
    # Set log level
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Create formatters
    if log_format == "json":
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s"
        )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # File handler
    try:
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except (OSError, PermissionError) as e:
        # Log to console if file logging fails
        logging.warning(f"Failed to setup file logging: {e}")

def get_logger_with_context(name: str, **context) -> logging.Logger:
    """Get logger with additional context"""
    logger = logging.getLogger(name)
    
    # Create adapter to add context to all log records
    class ContextAdapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            return msg, kwargs
        
        def _log(self, level, msg, args, **kwargs):
            if self.isEnabledFor(level):
                record = self.logger.makeRecord(
                    self.logger.name, level, "(unknown file)", 0,
                    msg, args, None, func=self._get_caller_info()
                )
                
                # Add context to record
                for key, value in self.extra.items():
                    setattr(record, key, value)
                
                self.logger.handle(record)
        
        def _get_caller_info(self):
            import inspect
            frame = inspect.currentframe()
            try:
                # Go up the stack to find the actual caller
                for _ in range(4):  # Skip adapter frames
                    frame = frame.f_back
                    if frame is None:
                        break
                return frame.f_code.co_name if frame else "unknown"
            finally:
                del frame
    
    return ContextAdapter(logger, context)