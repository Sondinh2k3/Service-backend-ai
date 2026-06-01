import json
import logging
import os
import sys
from datetime import datetime, timezone

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_JSON = os.getenv("LOG_JSON", "true").lower() in {"1", "true", "yes"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "pid": record.process,
            "thread": record.threadName,
        }

        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_record, ensure_ascii=False)

logger = logging.getLogger("ai_algo_service")
logger.setLevel(LOG_LEVEL)
logger.propagate = False

if not logger.hasHandlers():
    console_handler = logging.StreamHandler(sys.stdout)
    if LOG_JSON:
        console_handler.setFormatter(JsonFormatter())
    else:
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(console_handler)
