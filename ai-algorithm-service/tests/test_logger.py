import json
import logging

from src.core.config import Settings
from src.core.logger import JsonFormatter
from src.core.structured_logging import log_event


def test_json_formatter_includes_extra_fields():
    record = logging.LogRecord(
        name="ai_algo_service",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="request completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "elk-test-001"
    record.http_method = "GET"
    record.http_path = "/health"
    record.http_status = 200
    record.latency_ms = 12

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "request completed"
    assert payload["request_id"] == "elk-test-001"
    assert payload["http_method"] == "GET"
    assert payload["http_path"] == "/health"
    assert payload["http_status"] == 200
    assert payload["latency_ms"] == 12
    assert "service_name" in payload
    assert "service_role" in payload


def test_log_event_emits_structured_fields(monkeypatch):
    emitted = {}

    def fake_info(message, extra=None):
        emitted["message"] = message
        emitted["extra"] = extra

    monkeypatch.setattr("src.core.structured_logging.logger.info", fake_info)

    log_event(
        "runtime.inference.completed",
        request_id="trace-001",
        status="completed",
        area_id=1,
        bundle_id="bundle-1",
    )

    assert emitted["message"] == "runtime.inference.completed"
    assert emitted["extra"]["event"] == "runtime.inference.completed"
    assert emitted["extra"]["request_id"] == "trace-001"
    assert emitted["extra"]["event_status"] == "completed"
    assert emitted["extra"]["area_id"] == 1
    assert emitted["extra"]["bundle_id"] == "bundle-1"


def test_telemetry_excluded_paths_are_configurable():
    settings = Settings(telemetry_excluded_paths="/metrics, /health,/ready")

    assert settings.telemetry_excluded_path_set == {"/metrics", "/health", "/ready"}
