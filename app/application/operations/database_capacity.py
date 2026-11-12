"""Database size and connection headroom for the admin operations summary.

Values come from the PostgreSQL catalog of the connected database. They do not measure
provider-side compute or billing usage (for example Neon compute hours), which stays NOT_MEASURED.
"""
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.documents.usage import storage_alert_level
from app.config.settings import get_settings

CONNECTIONS_WARNING_PERCENT = 80
CONNECTIONS_CRITICAL_PERCENT = 95


def connection_alert_level(in_use: int, maximum: int) -> str:
    if maximum <= 0:
        return "UNKNOWN"
    percent = in_use * 100 / maximum
    if percent >= CONNECTIONS_CRITICAL_PERCENT:
        return "CRITICAL"
    if percent >= CONNECTIONS_WARNING_PERCENT:
        return "WARNING"
    return "OK"


def database_capacity_snapshot(db: Session) -> dict:
    settings = get_settings()
    try:
        with db.begin_nested():
            size_bytes = int(db.scalar(text("select pg_database_size(current_database())")) or 0)
            in_use = int(db.scalar(text(
                "select count(*) from pg_stat_activity where datname = current_database()"
            )) or 0)
            maximum = int(db.scalar(text("select current_setting('max_connections')::int")) or 0)
    except SQLAlchemyError:
        return {"available": False, "reason": "DATABASE_CAPACITY_QUERY_FAILED"}
    warning = settings.database_size_warning_bytes
    critical = settings.database_size_critical_bytes
    return {
        "available": True,
        "basis": "POSTGRES_CATALOG",
        "provider_compute_usage": "NOT_MEASURED",
        "size": {
            "bytes": size_bytes,
            "alert": {
                "level": storage_alert_level(size_bytes, warning, critical),
                "warning_bytes": warning,
                "critical_bytes": critical,
            },
        },
        "connections": {
            "in_use": in_use,
            "max": maximum,
            "utilization_percent": round(in_use * 100 / maximum, 1) if maximum > 0 else None,
            "level": connection_alert_level(in_use, maximum),
        },
    }
