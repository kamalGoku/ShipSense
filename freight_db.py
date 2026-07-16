import sqlite3
import os
import threading

import config
from logger import get_logger

logger = get_logger(__name__)

# Kept for backward compatibility with older imports; prefer config.FREIGHT_DB_PATH.
DB_PATH = config.FREIGHT_DB_PATH


class FreightDB:
    # Singleton-per-path (mirrors OrderDB): one instance per resolved db path.
    _instances = {}
    _lock = threading.Lock()

    def __new__(cls, db_path=None):
        db_path = db_path or config.FREIGHT_DB_PATH
        resolved = os.path.abspath(db_path)
        with cls._lock:
            instance = cls._instances.get(resolved)
            if instance is None:
                instance = super(FreightDB, cls).__new__(cls)
                instance.db_path = resolved
                instance._init_db()
                cls._instances[resolved] = instance
            return instance

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
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
        """Explicit upsert keyed on awb_number, then channel_order_id.

        INSERT OR REPLACE is intentionally NOT used: with two UNIQUE
        constraints it can silently delete/merge two distinct rows that each
        conflict on a different key. Instead we locate the existing row (AWB
        match preferred) and UPDATE it, or INSERT a new one.

        Every row must carry at least one lookup key (channel_order_id or
        awb_number); otherwise the row could never be found again and the
        freight would be re-fetched every run (e.g. zero-freight lookups for
        cancelled orders). Such calls are rejected with an error log.
        """
        # Normalize empty strings to None so the key checks are meaningful
        channel_order_id = channel_order_id or None
        awb_number = awb_number or None

        if channel_order_id is None and awb_number is None:
            logger.error(
                "Refusing to insert freight row with neither channel_order_id "
                "nor awb_number (SR#%s, amount=%s): it could never be looked "
                "up again. Pass the channel_order_id used for the lookup.",
                shiprocket_order_id, freight_amount,
            )
            return

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                row_by_awb = None
                if awb_number is not None:
                    cursor.execute("SELECT id FROM shipments WHERE awb_number = ?", (awb_number,))
                    r = cursor.fetchone()
                    row_by_awb = r[0] if r else None

                row_by_order = None
                if channel_order_id is not None:
                    cursor.execute("SELECT id FROM shipments WHERE channel_order_id = ?", (channel_order_id,))
                    r = cursor.fetchone()
                    row_by_order = r[0] if r else None

                if (row_by_awb is not None and row_by_order is not None
                        and row_by_awb != row_by_order):
                    logger.error(
                        "Freight upsert conflict: AWB %s matches row %s but "
                        "order %s matches row %s. Preferring the AWB match; "
                        "row %s is left untouched.",
                        awb_number, row_by_awb, channel_order_id, row_by_order, row_by_order,
                    )
                    target_id = row_by_awb
                    # Don't steal the channel_order_id from the other row —
                    # that would violate the UNIQUE constraint.
                    channel_order_id = None
                else:
                    target_id = row_by_awb if row_by_awb is not None else row_by_order

                if target_id is not None:
                    cursor.execute("""
                        UPDATE shipments
                        SET channel_order_id = COALESCE(?, channel_order_id),
                            shiprocket_order_id = COALESCE(?, shiprocket_order_id),
                            awb_number = COALESCE(?, awb_number),
                            freight_amount = ?,
                            charged_weight = ?,
                            zone = ?,
                            source = ?
                        WHERE id = ?
                    """, (channel_order_id, shiprocket_order_id, awb_number,
                          freight_amount, charged_weight, zone, source, target_id))
                else:
                    cursor.execute("""
                        INSERT INTO shipments
                        (channel_order_id, shiprocket_order_id, awb_number, freight_amount, charged_weight, zone, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (channel_order_id, shiprocket_order_id, awb_number,
                          freight_amount, charged_weight, zone, source))
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to insert freight data into DB: {e}")

    def get_freight_maps(self):
        """Return freight costs split by key type.

        Returns {"by_awb": {awb_number: freight_amount},
                 "by_order_id": {channel_order_id: freight_amount}}.
        Prefer this over get_all_freight_costs(), whose flat dict can collide
        AWB keys with order-id keys.
        """
        maps = {"by_awb": {}, "by_order_id": {}}
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT channel_order_id, awb_number, freight_amount FROM shipments")
                for channel_order_id, awb_number, freight_amount in cursor.fetchall():
                    if awb_number:
                        maps["by_awb"][awb_number] = freight_amount
                    if channel_order_id:
                        maps["by_order_id"][channel_order_id] = freight_amount
        except Exception as e:
            logger.error(f"Failed to fetch freight data from DB: {e}")
        return maps

    def get_all_freight_costs(self):
        """DEPRECATED: returns one flat dict mixing awb_number and
        channel_order_id keys (collisions possible). Use get_freight_maps().
        Kept for existing callers (sync_dashboard.py)."""
        maps = self.get_freight_maps()
        costs = {}
        costs.update(maps["by_awb"])
        costs.update(maps["by_order_id"])
        return costs

    def is_order_in_db(self, channel_order_id=None, awb_number=None):
        try:
            with sqlite3.connect(self.db_path) as conn:
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
