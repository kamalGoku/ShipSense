import sqlite3
import os
import json
from threading import Lock

import config
from logger import get_logger

logger = get_logger(__name__)


class OrderDB:
    # Singleton-per-path: one instance per resolved database path, so passing
    # a different db_path no longer silently returns a DB bound elsewhere.
    _instances = {}
    _lock = Lock()

    def __new__(cls, db_path=None):
        db_path = db_path or config.ORDERS_DB_PATH
        resolved = os.path.abspath(db_path)
        with cls._lock:
            instance = cls._instances.get(resolved)
            if instance is None:
                instance = super(OrderDB, cls).__new__(cls)
                instance.db_path = resolved
                instance._init_db()
                cls._instances[resolved] = instance
        return instance

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    platform TEXT,
                    status TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    items_json TEXT,
                    finances_json TEXT
                )
            """)

            # Migration: the metadata table used to be called "sync_state",
            # which collided with sync_state.json. Rename it to "sync_meta".
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('sync_state', 'sync_meta')"
            )
            existing_tables = {row[0] for row in cursor.fetchall()}
            if "sync_state" in existing_tables and "sync_meta" not in existing_tables:
                logger.info("Migrating SQLite table sync_state -> sync_meta")
                cursor.execute("ALTER TABLE sync_state RENAME TO sync_meta")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.commit()

    def get_last_synced_timestamp(self, platform="amazon") -> str:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM sync_meta WHERE key = ?", (f"last_synced_{platform}",))
            row = cursor.fetchone()
            return row[0] if row else None

    def set_last_synced_timestamp(self, timestamp: str, platform="amazon"):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sync_meta (key, value)
                VALUES (?, ?)
            """, (f"last_synced_{platform}", timestamp))
            conn.commit()

    def get_order(self, order_id: str) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT order_id, platform, status, created_at, updated_at, items_json, finances_json FROM orders WHERE order_id = ?", (order_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "order_id": row[0],
                    "platform": row[1],
                    "status": row[2],
                    "created_at": row[3],
                    "updated_at": row[4],
                    "items": json.loads(row[5]) if row[5] else None,
                    "finances": json.loads(row[6]) if row[6] else None
                }
            return None

    def upsert_order(self, order_id: str, platform: str, status: str, created_at: str, updated_at: str, items: list = None, finances: dict = None):
        # Note: `finances` is stored verbatim as JSON, so arbitrary keys such
        # as the dashboard's "fetched": True flag round-trip unchanged.
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Fetch existing to avoid overwriting items_json if not provided
            cursor.execute("SELECT items_json, finances_json FROM orders WHERE order_id = ?", (order_id,))
            row = cursor.fetchone()

            items_json = json.dumps(items) if items is not None else (row[0] if row else None)
            finances_json = json.dumps(finances) if finances is not None else (row[1] if row else None)

            cursor.execute("""
                INSERT OR REPLACE INTO orders (order_id, platform, status, created_at, updated_at, items_json, finances_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (order_id, platform, status, created_at, updated_at, items_json, finances_json))
            conn.commit()

    def get_all_orders(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT order_id, platform, status, created_at, updated_at, items_json, finances_json FROM orders")
            rows = cursor.fetchall()

            result = []
            for row in rows:
                result.append({
                    "order_id": row[0],
                    "platform": row[1],
                    "status": row[2],
                    "created_at": row[3],
                    "updated_at": row[4],
                    "items": json.loads(row[5]) if row[5] else None,
                    "finances": json.loads(row[6]) if row[6] else None
                })
            return result
