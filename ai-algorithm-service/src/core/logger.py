import json
import logging
import os
import sys
from datetime import datetime, timezone

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_JSON = os.getenv("LOG_JSON", "true").lower() in {"1", "true", "yes"}


def _service_name_from_env() -> str:
    role = os.getenv("SERVICE_ROLE", "").strip().lower()
    if role in {"runtime", "ops"}:
        return f"ai-{role}"
    return os.getenv("SERVICE_NAME", "ai-algorithm-service")


class JsonFormatter(logging.Formatter):
    reserved_attrs = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        log_record = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service_name": _service_name_from_env(),
            "service_role": os.getenv("SERVICE_ROLE", "all"),
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "pid": record.process,
            "thread": record.threadName,
        }

        for key, value in record.__dict__.items():
            if key not in self.reserved_attrs and not key.startswith("_"):
                log_record[key] = value

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
