"""
Tiny, idempotent schema upgrade for databases created BEFORE the rain-sensor change.

SQLAlchemy's create_all() creates missing tables but never alters existing ones, so
an old fieldnode.db would crash with "no such column: sensor_readings.rain_detected".
This adds the new columns if they're missing. Safe to run on every startup, and a
no-op on fresh databases. (Use Alembic if the schema keeps evolving.)

The old `sensor_readings.tank_level` column is left in place — it's nullable and
simply no longer read or written.
"""
import logging

from sqlalchemy import inspect, text

logger = logging.getLogger("fieldnode.migrate")

_NEW_COLUMNS = [
    ("sensor_readings", "rain_detected", "BOOLEAN"),
    ("sensor_readings", "rain_intensity", "FLOAT"),
    ("devices", "pump_flow_rate_lpm", "FLOAT DEFAULT 1.2"),
    ("zones", "sunlight_threshold", "FLOAT DEFAULT 30.0"),
    ("crop_presets", "sunlight_threshold", "FLOAT DEFAULT 30.0"),
]


def run_light_migrations(engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _NEW_COLUMNS:
            if table in tables and column not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                logger.info("Added column %s.%s", table, column)
        if "notifications" in tables:
            # LOW_TANK no longer exists as a type; reading such a row would raise.
            conn.execute(text("UPDATE notifications SET type = 'INFO' WHERE type = 'LOW_TANK'"))
        if "devices" in tables:
            conn.execute(text("UPDATE devices SET pump_flow_rate_lpm = 1.2 WHERE pump_flow_rate_lpm IS NULL"))
