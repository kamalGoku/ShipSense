import sqlite3
import os
import json
import logging
from threading import Lock

logger = logging.getLogger(__name__)

class OrderDB:
    _instance = None
    _lock = Lock()
    
    def __new__(cls, db_path="freight/orders_data.db"):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(OrderDB, cls).__new__(cls)
                cls._instance.db_path = db_path
                cls._instance._init_db()
        return cls._instance

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
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.commit()

    def get_last_synced_timestamp(self, platform="amazon") -> str:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM sync_state WHERE key = ?", (f"last_synced_{platform}",))
            row = cursor.fetchone()
            return row[0] if row else None

    def set_last_synced_timestamp(self, timestamp: str, platform="amazon"):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sync_state (key, value)
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
