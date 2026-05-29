"""Ad-hoc migrations cho MVP.

Khong dung Alembic — giu setup don gian. Logic: kiem tra column ton tai chua,
ALTER TABLE ADD COLUMN neu thieu. Chap nhan SQLite + MySQL/Postgres voi syntax
co ban (default value bat buoc la literal).

Khi schema phuc tap hon (constraint thay doi, type thay doi), migrate sang
Alembic.
"""

from __future__ import annotations

from typing import Tuple

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from src.core.logger import logger


# (table, column, ddl_fragment)
# ddl_fragment phai khop voi syntax cua MySQL + SQLite (text & default).
_PENDING_COLUMNS: Tuple[Tuple[str, str, str], ...] = (
    ("area_registry", "tenant_id", "VARCHAR(64) NOT NULL DEFAULT 'default'"),
    ("area_registry", "network_id", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("inference_audit", "bundle_id", "VARCHAR(128) NULL"),
    ("inference_audit", "guardrail_triggered", "BOOLEAN NOT NULL DEFAULT 0"),
    ("model_bundle", "bundle_kind", "VARCHAR(16) NOT NULL DEFAULT 'runtime'"),
    ("model_bundle", "parent_bundle_id", "VARCHAR(128) NULL"),
)

_PENDING_TABLES: Tuple[Tuple[str, str], ...] = (
    (
        "real_network_snapshot",
        """
        CREATE TABLE real_network_snapshot (
            area_id INTEGER PRIMARY KEY,
            tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
            network_id VARCHAR(128) NOT NULL,
            schema_version VARCHAR(32) NOT NULL DEFAULT 'real-network/v1',
            source_version VARCHAR(128) NULL,
            payload_json TEXT NOT NULL,
            checksum VARCHAR(128) NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ),
)


def _existing_columns(engine: Engine, table: str) -> set[str]:
    insp = inspect(engine)
    if not insp.has_table(table):
        return set()
    return {col["name"] for col in insp.get_columns(table)}


def apply_simple_migrations(engine: Engine) -> None:
    """Tao bang/cot con thieu cho DB da ton tai."""
    insp = inspect(engine)
    for table, ddl in _PENDING_TABLES:
        if insp.has_table(table):
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info(f"[migration] Created table {table}")
        except Exception as e:
            logger.error(f"[migration] Failed creating {table}: {e}")

    for table, column, ddl in _PENDING_COLUMNS:
        cols = _existing_columns(engine, table)
        if not cols:
            # Bang chua ton tai (create_all se tao moi voi day du cot).
            continue
        if column in cols:
            continue
        sql = f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
            logger.info(f"[migration] Added {table}.{column}")
        except Exception as e:
            logger.error(f"[migration] Failed {sql}: {e}")


def backfill_area_network_ids(engine: Engine) -> None:
    """Set network_id = 'area_<id>' cho row co network_id rong (legacy)."""
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE area_registry SET network_id = 'area_' || area_id "
                "WHERE network_id = '' OR network_id IS NULL"
            ))
    except Exception:
        # MySQL khong ho tro || cho concat, dung CONCAT.
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE area_registry SET network_id = CONCAT('area_', area_id) "
                    "WHERE network_id = '' OR network_id IS NULL"
                ))
        except Exception as e:
            logger.warning(f"[migration] backfill network_id skipped: {e}")
