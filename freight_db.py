import sqlite3
import os
import threading
from logger import get_logger

logger = get_logger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "freight", "freight_data.db")

class FreightDB:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(FreightDB, cls).__new__(cls)
                cls._instance._init_db()
            return cls._instance
            
    def _init_db(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shipments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_order_id TEXT,
                    shiprocket_order_id TEXT,
                    awb_number TEXT,
                    freight_amount REAL,
                    charged_weight REAL,
                    zone TEXT,
                    source TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(awb_number),
                    UNIQUE(channel_order_id)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel_order_id ON shipments(channel_order_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_awb_number ON shipments(awb_number)")
            conn.commit()
            
    def insert_freight(self, channel_order_id, shiprocket_order_id, awb_number, freight_amount, charged_weight=None, zone=None, source='api'):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO shipments 
                    (channel_order_id, shiprocket_order_id, awb_number, freight_amount, charged_weight, zone, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (channel_order_id, shiprocket_order_id, awb_number, freight_amount, charged_weight, zone, source))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to insert freight data into DB: {e}")

    def get_all_freight_costs(self):
        """Returns a dict mapping awb_number -> freight_amount AND channel_order_id -> freight_amount for quick lookup"""
        costs = {}
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT channel_order_id, awb_number, freight_amount FROM shipments")
                for channel_order_id, awb_number, freight_amount in cursor.fetchall():
                    if awb_number:
                        costs[awb_number] = freight_amount
                    if channel_order_id:
                        costs[channel_order_id] = freight_amount
        except Exception as e:
            logger.error(f"Failed to fetch freight data from DB: {e}")
        return costs

    def is_order_in_db(self, channel_order_id=None, awb_number=None):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                if channel_order_id:
                    cursor.execute("SELECT 1 FROM shipments WHERE channel_order_id = ?", (channel_order_id,))
                    if cursor.fetchone():
                        return True
                if awb_number:
                    cursor.execute("SELECT 1 FROM shipments WHERE awb_number = ?", (awb_number,))
                    if cursor.fetchone():
                        return True
        except Exception as e:
            logger.error(f"DB lookup error: {e}")
        return False
